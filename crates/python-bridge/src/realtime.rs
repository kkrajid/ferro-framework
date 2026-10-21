//! Streaming responses and WebSocket connections on the Python side (Feature 018).
//!
//! Everything that touches Python runs on the event-loop thread: the engine's requests for the
//! next chunk and its socket events are queued with [`Executor::post`].

use std::collections::VecDeque;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex, Weak};

use bytes::Bytes;
use ferro_core::socket::close_code;
use ferro_core::{
    ChunkSource, FerroRequest, FerroResponse, LocalBoxFuture, SocketEvent, SocketEvents,
    SocketLink, SocketMessage, StreamAborted,
};
use pyo3::exceptions::{PyRuntimeError, PyStopAsyncIteration, PyTypeError, PyValueError};
use pyo3::intern;
use pyo3::prelude::*;
use pyo3::types::{PyByteArray, PyBytes, PyMemoryView, PyString};
use tokio::sync::oneshot;

use crate::bridge::{Executor, request_id_context};
use crate::convert::{self, ConvertError};
use crate::errors;
use crate::request::{Ready, Request};
use crate::types::types;

fn lock<T>(mutex: &Mutex<T>) -> std::sync::MutexGuard<'_, T> {
    mutex.lock().unwrap_or_else(|e| e.into_inner())
}

/// Runs `make` with this request's id in the context, so tasks created inside see it.
fn with_request_id<'py, T>(
    py: Python<'py>,
    request_id: &str,
    make: impl FnOnce() -> PyResult<T>,
) -> PyResult<T> {
    let context = request_id_context(py)?.bind(py);
    let token = context.call_method1(intern!(py, "set"), (request_id,))?;
    let result = make();
    context.call_method1(intern!(py, "reset"), (token,))?;
    result
}

fn is_done(future: &Bound<'_, PyAny>) -> bool {
    future
        .call_method0(intern!(future.py(), "done"))
        .and_then(|done| done.is_truthy())
        .unwrap_or(true)
}

// -- streaming responses ---------------------------------------------------------------------

type ChunkResult = Result<Option<Bytes>, StreamAborted>;

struct ChunksInner {
    iterator: Py<PyAny>,
    executor: Weak<Executor>,
    request_id: String,
    handler: String,
    pending: Mutex<Option<Py<PyAny>>>,
    /// The engine dropped the body: close the iterator once no chunk is in progress.
    closing: AtomicBool,
}

/// The engine's view of a `StreamingResponse`: each `next_chunk` runs `__anext__()` on the
/// event loop; dropping it cancels a pending chunk and closes the iterator.
struct PyChunks(Arc<ChunksInner>);

impl ChunkSource for PyChunks {
    fn next_chunk(&self) -> LocalBoxFuture<Result<Option<Bytes>, StreamAborted>> {
        let (reply, receiver) = oneshot::channel();
        let Some(executor) = self.0.executor.upgrade() else {
            return Box::pin(async { Err(StreamAborted) });
        };
        let inner = self.0.clone();
        executor.post(Box::new(move |py| ChunksInner::start(&inner, py, reply)));
        // No answer: the bridge stopped before the chunk was made.
        Box::pin(async move { receiver.await.unwrap_or(Err(StreamAborted)) })
    }
}

impl Drop for PyChunks {
    fn drop(&mut self) {
        let Some(executor) = self.0.executor.upgrade() else {
            return;
        };
        let inner = self.0.clone();
        executor.post(Box::new(move |py| inner.close(py)));
    }
}

impl ChunksInner {
    fn start(this: &Arc<Self>, py: Python<'_>, reply: oneshot::Sender<ChunkResult>) {
        let Some(executor) = this.executor.upgrade() else {
            let _ = reply.send(Err(StreamAborted));
            return;
        };
        let started = with_request_id(py, &this.request_id, || {
            let coroutine = this
                .iterator
                .bind(py)
                .call_method0(intern!(py, "__anext__"))?;
            types(py)?
                .eager_task_factory
                .bind(py)
                .call1((executor.event_loop().bind(py), coroutine))
        });
        let task = match started {
            Ok(task) => task,
            Err(err) => {
                errors::log_failure(py, &err, &this.request_id, &this.handler, "stream failed");
                let _ = reply.send(Err(StreamAborted));
                return;
            }
        };
        if is_done(&task) {
            let _ = reply.send(this.outcome(py, &task));
            return;
        }
        *lock(&this.pending) = Some(task.clone().unbind());
        let callback = ChunkDone {
            inner: this.clone(),
            reply: Mutex::new(Some(reply)),
        };
        let attached = Bound::new(py, callback)
            .and_then(|callback| task.call_method1(intern!(py, "add_done_callback"), (callback,)));
        if let Err(err) = attached {
            errors::log_failure(py, &err, &this.request_id, &this.handler, "stream failed");
        }
    }

    /// The chunk a finished `__anext__()` task produced: the end of the body at
    /// `StopAsyncIteration`, an abort (logged) for any other failure.
    fn outcome(&self, py: Python<'_>, task: &Bound<'_, PyAny>) -> ChunkResult {
        let value = (|| -> PyResult<Option<Bound<'_, PyAny>>> {
            if task.call_method0(intern!(py, "cancelled"))?.is_truthy()? {
                return Ok(None);
            }
            let exception = task.call_method0(intern!(py, "exception"))?;
            if !exception.is_none() {
                return Err(PyErr::from_value(exception));
            }
            Ok(Some(task.call_method0(intern!(py, "result"))?))
        })();
        match value {
            Ok(Some(value)) => match chunk_bytes(&value) {
                Ok(bytes) => Ok(Some(bytes)),
                Err(err) => {
                    errors::log_failure(py, &err, &self.request_id, &self.handler, "stream failed");
                    Err(StreamAborted)
                }
            },
            // Cancelled: only when the engine dropped the body, so nobody reads this.
            Ok(None) => Err(StreamAborted),
            Err(err) if err.is_instance_of::<PyStopAsyncIteration>(py) => Ok(None),
            Err(err) => {
                errors::log_failure(py, &err, &self.request_id, &self.handler, "stream failed");
                Err(StreamAborted)
            }
        }
    }

    /// Cancels a chunk in progress and closes the iterator (runs `finally` blocks). A generator
    /// cannot be closed while its `__anext__` runs, so with a chunk in progress the iterator is
    /// closed by [`ChunkDone`] once the cancellation has landed.
    fn close(&self, py: Python<'_>) {
        self.closing.store(true, Ordering::Relaxed);
        let pending = lock(&self.pending).as_ref().map(|task| task.clone_ref(py));
        match pending {
            Some(task) => {
                let _ = task.bind(py).call_method0(intern!(py, "cancel"));
            }
            None => self.close_iterator(py),
        }
    }

    fn close_iterator(&self, py: Python<'_>) {
        let iterator = self.iterator.bind(py);
        let Ok(aclose) = iterator.getattr(intern!(py, "aclose")) else {
            return;
        };
        let Some(executor) = self.executor.upgrade() else {
            return;
        };
        let closed = with_request_id(py, &self.request_id, || {
            let coroutine = aclose.call0()?;
            let task = types(py)?
                .eager_task_factory
                .bind(py)
                .call1((executor.event_loop().bind(py), coroutine))?;
            // Retrieve the outcome so a failing `finally` is logged once, not as "never
            // retrieved".
            let watcher = Bound::new(
                py,
                CloseDone {
                    request_id: self.request_id.clone(),
                    handler: self.handler.clone(),
                },
            )?;
            if is_done(&task) {
                watcher.call1((task,))?;
            } else {
                task.call_method1(intern!(py, "add_done_callback"), (watcher,))?;
            }
            Ok(())
        });
        if let Err(err) = closed {
            errors::log_failure(
                py,
                &err,
                &self.request_id,
                &self.handler,
                "stream cleanup failed",
            );
        }
    }
}

fn chunk_bytes(value: &Bound<'_, PyAny>) -> PyResult<Bytes> {
    let py = value.py();
    if let Ok(text) = value.cast::<PyString>() {
        Ok(Bytes::copy_from_slice(text.to_str()?.as_bytes()))
    } else if let Ok(bytes) = value.cast::<PyBytes>() {
        Ok(Bytes::copy_from_slice(bytes.as_bytes()))
    } else if let Ok(array) = value.cast::<PyByteArray>() {
        Ok(Bytes::from(array.to_vec()))
    } else if value.cast::<PyMemoryView>().is_ok() {
        let bytes = py.get_type::<PyBytes>().call1((value,))?;
        Ok(Bytes::copy_from_slice(bytes.cast::<PyBytes>()?.as_bytes()))
    } else {
        Err(PyTypeError::new_err(format!(
            "a streaming response must yield str or bytes, got {}",
            value.get_type().qualname()?
        )))
    }
}

#[pyclass(module = "ferro._ferro", frozen)]
struct ChunkDone {
    inner: Arc<ChunksInner>,
    reply: Mutex<Option<oneshot::Sender<ChunkResult>>>,
}

#[pymethods]
impl ChunkDone {
    fn __call__(&self, task: &Bound<'_, PyAny>) {
        lock(&self.inner.pending).take();
        if let Some(reply) = lock(&self.reply).take() {
            let _ = reply.send(self.inner.outcome(task.py(), task));
        }
        if self.inner.closing.load(Ordering::Relaxed) {
            self.inner.close_iterator(task.py());
        }
    }
}

#[pyclass(module = "ferro._ferro", frozen)]
struct CloseDone {
    request_id: String,
    handler: String,
}

#[pymethods]
impl CloseDone {
    fn __call__(&self, task: &Bound<'_, PyAny>) {
        let py = task.py();
        let failed = (|| -> PyResult<Option<PyErr>> {
            if task.call_method0(intern!(py, "cancelled"))?.is_truthy()? {
                return Ok(None);
            }
            let exception = task.call_method0(intern!(py, "exception"))?;
            Ok((!exception.is_none()).then(|| PyErr::from_value(exception)))
        })();
        if let Ok(Some(err)) = failed {
            errors::log_failure(
                py,
                &err,
                &self.request_id,
                &self.handler,
                "stream cleanup failed",
            );
        }
    }
}

/// A `StreamingResponse` returned by a handler: status and headers now, the body later.
pub(crate) fn streaming_response(
    value: &Bound<'_, PyAny>,
    executor: Weak<Executor>,
    request_id: &str,
    handler: &str,
) -> Result<FerroResponse, ConvertError> {
    let py = value.py();
    let mut response = convert::head(value)?;
    let iterator = value.getattr(intern!(py, "_iterator"))?.unbind();
    response = response.with_stream(Box::new(PyChunks(Arc::new(ChunksInner {
        iterator,
        executor,
        request_id: request_id.to_string(),
        handler: handler.to_string(),
        pending: Mutex::new(None),
        closing: AtomicBool::new(false),
    }))));
    Ok(response)
}

/// A `StreamingResponse`, or a response with a body iterator (`FileResponse` of a large file,
/// Feature 025).
pub(crate) fn is_streaming(value: &Bound<'_, PyAny>) -> PyResult<bool> {
    let py = value.py();
    if value.is_instance(types(py)?.streaming_response.bind(py))? {
        return Ok(true);
    }
    Ok(value.is_instance(types(py)?.response.bind(py))?
        && value
            .getattr(intern!(py, "_iterator"))
            .is_ok_and(|iterator| !iterator.is_none()))
}

// -- WebSockets --------------------------------------------------------------------------------

#[derive(Default)]
struct SocketInner {
    buffer: VecDeque<SocketMessage>,
    /// The future a `receive()` is waiting on.
    receiving: Option<Py<PyAny>>,
    /// Futures of `send()` calls waiting for the queue to drain.
    sending: Vec<Py<PyAny>>,
    closed: Option<(u16, String)>,
    /// The application called `close()`: the connection ending is not a reason to cancel it.
    closed_by_app: bool,
    task: Option<Py<PyAny>>,
}

pub(crate) struct SocketShared {
    link: Arc<SocketLink>,
    executor: Weak<Executor>,
    request_id: String,
    handler: String,
    inner: Mutex<SocketInner>,
}

impl SocketShared {
    pub(crate) fn link(&self) -> &SocketLink {
        &self.link
    }

    pub(crate) fn new(
        link: Arc<SocketLink>,
        executor: Weak<Executor>,
        request_id: String,
        handler: String,
    ) -> Arc<Self> {
        Arc::new(Self {
            link,
            executor,
            request_id,
            handler,
            inner: Mutex::new(SocketInner::default()),
        })
    }

    fn disconnect(py: Python<'_>, code: u16, reason: &str) -> PyErr {
        match types(py).and_then(|t| t.websocket_disconnect.bind(py).call1((code, reason))) {
            Ok(exception) => PyErr::from_value(exception),
            Err(err) => err,
        }
    }

    /// Handles an engine event on the event-loop thread.
    fn handle(&self, py: Python<'_>, event: SocketEvent) {
        let mut inner = lock(&self.inner);
        match event {
            SocketEvent::Message(message) => {
                if let Some(future) = inner.receiving.take()
                    && !is_done(future.bind(py))
                {
                    drop(inner);
                    self.link.consumed(message.len());
                    let value = message_value(py, message);
                    if let Ok(value) = value {
                        let _ = future
                            .bind(py)
                            .call_method1(intern!(py, "set_result"), (value,));
                    }
                    return;
                }
                inner.buffer.push_back(message);
            }
            SocketEvent::Drained => {
                for future in inner.sending.drain(..) {
                    let future = future.bind(py);
                    if !is_done(future) {
                        let _ = future.call_method1(intern!(py, "set_result"), (py.None(),));
                    }
                }
            }
            SocketEvent::Closed { code, reason } => {
                inner.closed = Some((code, reason.clone()));
                let receiving = inner.receiving.take().filter(|f| !is_done(f.bind(py)));
                let sending: Vec<Py<PyAny>> = std::mem::take(&mut inner.sending);
                let cancel = (receiving.is_none() && !inner.closed_by_app)
                    .then(|| inner.task.as_ref().map(|task| task.clone_ref(py)))
                    .flatten();
                drop(inner);
                for future in receiving.iter().chain(sending.iter()) {
                    let future = future.bind(py);
                    if !is_done(future) {
                        let error = Self::disconnect(py, code, &reason);
                        let _ = future
                            .call_method1(intern!(py, "set_exception"), (error.value(py).clone(),));
                    }
                }
                // Nobody waits in receive(): the handler is doing something else for a client
                // that is gone, as with an HTTP client that disconnects.
                if let Some(task) = cancel {
                    let task = task.bind(py);
                    if !is_done(task) {
                        let _ = task.call_method0(intern!(py, "cancel"));
                    }
                }
            }
        }
    }

    fn finish(&self, py: Python<'_>, task: &Bound<'_, PyAny>) {
        lock(&self.inner).task = None;
        let outcome = (|| -> PyResult<Option<PyErr>> {
            if task.call_method0(intern!(py, "cancelled"))?.is_truthy()? {
                return Ok(None);
            }
            let exception = task.call_method0(intern!(py, "exception"))?;
            Ok((!exception.is_none()).then(|| PyErr::from_value(exception)))
        })();
        match outcome {
            Ok(None) => self.link.close(close_code::NORMAL, ""),
            Ok(Some(err)) => {
                let disconnected = types(py)
                    .map(|t| err.is_instance(py, t.websocket_disconnect.bind(py)))
                    .unwrap_or(false);
                if disconnected {
                    self.link.close(close_code::NORMAL, "");
                } else {
                    errors::log_failure(py, &err, &self.request_id, &self.handler, "raised");
                    self.link.close(close_code::INTERNAL, "internal error");
                }
            }
            Err(err) => {
                errors::log_failure(py, &err, &self.request_id, &self.handler, "raised");
                self.link.close(close_code::INTERNAL, "internal error");
            }
        }
    }
}

fn message_value(py: Python<'_>, message: SocketMessage) -> PyResult<Py<PyAny>> {
    Ok(match message {
        SocketMessage::Text(text) => PyString::new(py, &text).into_any().unbind(),
        SocketMessage::Binary(bytes) => PyBytes::new(py, &bytes).into_any().unbind(),
    })
}

/// The engine's handle on the application side of one socket.
pub(crate) struct BridgeEvents(pub(crate) Arc<SocketShared>);

impl SocketEvents for BridgeEvents {
    fn event(&self, event: SocketEvent) {
        let Some(executor) = self.0.executor.upgrade() else {
            return;
        };
        let shared = self.0.clone();
        executor.post(Box::new(move |py| shared.handle(py, event)));
    }
}

/// Starts the handler of an accepted connection (on the event-loop thread).
pub(crate) fn start_socket(
    py: Python<'_>,
    executor: &Executor,
    endpoint: &Py<PyAny>,
    request: FerroRequest,
    shared: Arc<SocketShared>,
) {
    let request_id = shared.request_id.clone();
    let started = with_request_id(py, &request_id, || {
        let request = Py::new(py, Request::new(request))?;
        let socket = Bound::new(
            py,
            Socket {
                shared: shared.clone(),
                request,
            },
        )?;
        let coroutine = endpoint.bind(py).call1((socket,))?;
        types(py)?
            .eager_task_factory
            .bind(py)
            .call1((executor.event_loop().bind(py), coroutine))
    });
    let task = match started {
        Ok(task) => task,
        Err(err) => {
            errors::log_failure(py, &err, &request_id, &shared.handler, "raised");
            shared.link.close(close_code::INTERNAL, "internal error");
            return;
        }
    };
    if is_done(&task) {
        shared.finish(py, &task);
        return;
    }
    lock(&shared.inner).task = Some(task.clone().unbind());
    let attached = Bound::new(py, SocketDone { shared })
        .and_then(|callback| task.call_method1(intern!(py, "add_done_callback"), (callback,)));
    if let Err(err) = attached {
        let text = err.to_string();
        py.detach(|| {
            tracing::error!(target: "ferro.server", "could not watch socket handler: {text}");
        });
    }
}

#[pyclass(module = "ferro._ferro", frozen)]
struct SocketDone {
    shared: Arc<SocketShared>,
}

#[pymethods]
impl SocketDone {
    fn __call__(&self, task: &Bound<'_, PyAny>) {
        self.shared.finish(task.py(), task);
    }
}

/// `ws._socket`: the native side of `ferro.websockets.WebSocket`.
#[pyclass(module = "ferro._ferro", name = "Socket", frozen)]
pub(crate) struct Socket {
    shared: Arc<SocketShared>,
    request: Py<Request>,
}

#[pymethods]
impl Socket {
    /// The handshake request (headers, cookies, path and query parameters, client).
    #[getter]
    fn request(&self, py: Python<'_>) -> Py<Request> {
        self.request.clone_ref(py)
    }

    #[getter]
    fn closed(&self) -> bool {
        lock(&self.shared.inner).closed.is_some() || self.shared.link.is_closed()
    }

    /// `(code, reason)` once closed.
    #[getter]
    fn close_info(&self) -> Option<(u16, String)> {
        lock(&self.shared.inner).closed.clone()
    }

    /// The next message (`str` or `bytes`); raises `WebSocketDisconnect` once closed.
    fn receive<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyAny>> {
        let mut inner = lock(&self.shared.inner);
        if let Some(message) = inner.buffer.pop_front() {
            drop(inner);
            self.shared.link.consumed(message.len());
            let value = message_value(py, message)?;
            return Ok(Bound::new(py, Ready::new(value))?.into_any());
        }
        if let Some((code, reason)) = &inner.closed {
            return Err(SocketShared::disconnect(py, *code, reason));
        }
        if inner
            .receiving
            .as_ref()
            .is_some_and(|future| !is_done(future.bind(py)))
        {
            return Err(PyRuntimeError::new_err(
                "another receive() is already waiting on this WebSocket",
            ));
        }
        let executor = self
            .shared
            .executor
            .upgrade()
            .ok_or_else(|| PyRuntimeError::new_err("the server has stopped"))?;
        let future = executor
            .event_loop()
            .bind(py)
            .call_method0(intern!(py, "create_future"))?;
        inner.receiving = Some(future.clone().unbind());
        Ok(future)
    }

    /// Sends `str` (text frame) or `bytes` (binary frame); waits only when 1 MiB is queued.
    fn send<'py>(&self, py: Python<'py>, data: &Bound<'py, PyAny>) -> PyResult<Bound<'py, PyAny>> {
        let message = if let Ok(text) = data.cast::<PyString>() {
            SocketMessage::Text(text.to_str()?.to_string())
        } else if data.cast::<PyBytes>().is_ok()
            || data.cast::<PyByteArray>().is_ok()
            || data.cast::<PyMemoryView>().is_ok()
        {
            SocketMessage::Binary(chunk_bytes(data)?)
        } else {
            return Err(PyTypeError::new_err(format!(
                "WebSocket.send() takes str or bytes, got {} (use send_json for other values)",
                data.get_type().qualname()?
            )));
        };
        let mut inner = lock(&self.shared.inner);
        if let Some((code, reason)) = &inner.closed {
            return Err(SocketShared::disconnect(py, *code, reason));
        }
        match self.shared.link.send(message) {
            Err(_) => Err(SocketShared::disconnect(py, close_code::ABNORMAL, "closed")),
            Ok(false) => Ok(Bound::new(py, Ready::new(py.None()))?.into_any()),
            Ok(true) => {
                let executor = self
                    .shared
                    .executor
                    .upgrade()
                    .ok_or_else(|| PyRuntimeError::new_err("the server has stopped"))?;
                let future = executor
                    .event_loop()
                    .bind(py)
                    .call_method0(intern!(py, "create_future"))?;
                inner.sending.push(future.clone().unbind());
                Ok(future)
            }
        }
    }

    /// Closes the connection with `code` (1000, or 3000–4999 for your own) and `reason`.
    #[pyo3(signature = (code=1000, reason=""))]
    fn close<'py>(&self, py: Python<'py>, code: u16, reason: &str) -> PyResult<Bound<'py, PyAny>> {
        let allowed =
            matches!(code, 1000 | 1001 | 1003 | 1007 | 1008 | 1011) || (3000..5000).contains(&code);
        if !allowed {
            return Err(PyValueError::new_err(format!(
                "invalid close code {code}: use 1000, 1001, 1003, 1007, 1008, 1011 or 3000-4999"
            )));
        }
        if reason.len() > 123 {
            return Err(PyValueError::new_err(
                "a close reason may be at most 123 bytes",
            ));
        }
        lock(&self.shared.inner).closed_by_app = true;
        self.shared.link.close(code, reason);
        Ok(Bound::new(py, Ready::new(py.None()))?.into_any())
    }
}

// -- broadcast across processes (PostgreSQL LISTEN/NOTIFY) -------------------------------------

/// A running notification listener (`ferro.broadcast` with the database backend).
#[pyclass(module = "ferro._ferro", frozen)]
pub(crate) struct BroadcastListener {
    listener: Mutex<Option<ferro_database::Listener>>,
}

#[pymethods]
impl BroadcastListener {
    fn stop(&self) {
        lock(&self.listener).take();
    }
}

/// Calls `callback(payload)` on the running event loop for every notification on `channel`.
#[pyfunction]
pub(crate) fn broadcast_listen(
    py: Python<'_>,
    channel: String,
    callback: Py<PyAny>,
) -> PyResult<BroadcastListener> {
    let database = crate::db::current(py)?;
    let event_loop = py
        .import("asyncio")?
        .call_method0("get_running_loop")
        .map_err(|_| PyRuntimeError::new_err("broadcast_listen must run inside the event loop"))?
        .unbind();
    let deliver = Arc::new(move |payload: String| {
        Python::try_attach(|py| {
            let _ = event_loop.bind(py).call_method1(
                intern!(py, "call_soon_threadsafe"),
                (callback.bind(py), payload),
            );
        });
    });
    let listener = database
        .backend
        .listen(&channel, deliver)
        .map_err(|err| crate::orm_values::configuration_error(py, err.to_string()))?;
    Ok(BroadcastListener {
        listener: Mutex::new(Some(listener)),
    })
}

/// `SELECT pg_notify(channel, payload)` through the connection pool (or the current
/// transaction, which then delivers it on commit).
#[pyfunction]
pub(crate) fn broadcast_publish<'py>(
    py: Python<'py>,
    channel: String,
    payload: String,
) -> PyResult<Bound<'py, PyAny>> {
    use ferro_database::{Job, Statement, Value};

    let database = crate::db::current(py)?;
    if database.dialect.name() != "postgresql" {
        return Err(crate::orm_values::configuration_error(
            py,
            "broadcast with the database backend needs PostgreSQL (LISTEN/NOTIFY)",
        ));
    }
    let statement = Statement::new(
        "SELECT pg_notify($1, $2)",
        vec![Value::Text(channel), Value::Text(payload)],
    );
    crate::db::submit(
        py,
        &database,
        Job::Query(statement),
        Box::new(|py, result| {
            crate::orm::rows(py, result, None)?;
            Ok(py.None())
        }),
    )
}
