//! Ferro-owned asyncio bridge (research R2, R13; constitution v1.1.0).
//!
//! Engine workers never touch the interpreter on the request path. They push jobs into a Rust
//! queue; the Python execution thread drains the queue in batches:
//!
//! 1. A worker pushes [`Job::Start`] and, only if no drain is pending, wakes the event loop with
//!    `loop.call_soon_threadsafe(drain)` (the only interpreter access a worker ever makes).
//!    The flag stays set while a drain runs, and the drain re-checks the queue before it
//!    returns, so under load jobs are picked up without any wake-up (Feature 010 profile).
//!    Database completions use the same queue ([`Job::Call`], [`executor_for`]).
//! 2. [`Drain`] runs on the event-loop thread: it builds the `Request`, calls the endpoint and
//!    starts the coroutine with `asyncio.eager_task_factory`, so a handler that never suspends
//!    finishes inside the drain without being scheduled.
//! 3. The outcome is converted to a [`FerroResponse`] on the Python thread (the interpreter is
//!    held there anyway) and sent to the worker through a oneshot channel.
//! 4. Dropping an unfinished call (timeout, client disconnect, shutdown) queues [`Job::Cancel`],
//!    which cancels the asyncio task.

use std::cell::RefCell;
use std::collections::VecDeque;
use std::future::Future;
use std::pin::Pin;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex, OnceLock, Weak};
use std::task::{Context, Poll};

use ferro_core::{ErrorEnvelope, FerroRequest, FerroResponse};
use pyo3::intern;
use pyo3::prelude::*;
use tokio::sync::oneshot;

use crate::request::Request;
use crate::types::types;
use crate::{convert, errors};

#[derive(Debug)]
pub(crate) enum CallError {
    Cancelled,
    Raised(PyErr),
}

pub(crate) struct Endpoint {
    /// Python callable `endpoint(request) -> coroutine`, built once per route by `ferro.routing`.
    pub(crate) func: Py<PyAny>,
    /// `module.qualname` of the developer's handler, for logs and errors.
    pub(crate) name: String,
}

#[derive(Default)]
struct CallState {
    abandoned: AtomicBool,
    task: Mutex<Option<Py<PyAny>>>,
}

struct StartJob {
    request: FerroRequest,
    reply: oneshot::Sender<FerroResponse>,
    state: Arc<CallState>,
}

/// Work for the event-loop thread that needs the interpreter (e.g. resolving a database
/// future); queued without it.
pub(crate) type LoopCall = Box<dyn FnOnce(Python<'_>) + Send>;

enum Job {
    Start(Box<StartJob>),
    Cancel(Arc<CallState>),
    Call(LoopCall),
}

static REQUEST_ID: pyo3::sync::PyOnceLock<Py<PyAny>> = pyo3::sync::PyOnceLock::new();

/// `contextvars.ContextVar("ferro_request_id", default=None)`: the id of the request a task
/// (or a sync handler's thread) is handling, read by Ferro's log record factory (Feature 011).
pub(crate) fn request_id_context(py: Python<'_>) -> PyResult<&Py<PyAny>> {
    REQUEST_ID.get_or_try_init(py, || {
        let kwargs = pyo3::types::PyDict::new(py);
        kwargs.set_item("default", py.None())?;
        Ok(py
            .import("contextvars")?
            .getattr("ContextVar")?
            .call(("ferro_request_id",), Some(&kwargs))?
            .unbind())
    })
}

/// Passes per drain before it yields to the callbacks it made ready (handler continuations).
const DRAIN_PASSES: usize = 4;

thread_local! {
    /// The executor whose drain runs on this thread (a server's event-loop thread).
    static LOOP_EXECUTOR: RefCell<Weak<Executor>> = const { RefCell::new(Weak::new()) };
}

/// The executor serving `event_loop`, when called on that loop's thread: database completions
/// for it are queued with [`Executor::post`] instead of `call_soon_threadsafe`.
pub(crate) fn executor_for(event_loop: &Bound<'_, PyAny>) -> Option<Weak<Executor>> {
    LOOP_EXECUTOR.with(|slot| {
        let weak = slot.borrow().clone();
        let executor = weak.upgrade()?;
        (executor.event_loop.as_ptr() == event_loop.as_ptr()).then_some(weak)
    })
}

fn lock<T>(mutex: &Mutex<T>) -> std::sync::MutexGuard<'_, T> {
    mutex.lock().unwrap_or_else(|e| e.into_inner())
}

fn unavailable(request_id: &str) -> FerroResponse {
    ErrorEnvelope::new(503, request_id)
        .with_header("retry-after", "1")
        .into_response()
}

pub(crate) struct Executor {
    queue: Mutex<VecDeque<Job>>,
    drain_pending: AtomicBool,
    event_loop: Py<PyAny>,
    endpoints: Vec<Endpoint>,
    debug: bool,
    /// The [`Drain`] callable scheduled on the loop (it refers back weakly).
    drain: OnceLock<Py<PyAny>>,
}

impl Executor {
    pub(crate) fn new(event_loop: Py<PyAny>, endpoints: Vec<Endpoint>, debug: bool) -> Self {
        Self {
            queue: Mutex::new(VecDeque::new()),
            drain_pending: AtomicBool::new(false),
            event_loop,
            endpoints,
            debug,
            drain: OnceLock::new(),
        }
    }

    /// Queues `call` for the event-loop thread (from any thread, without the interpreter).
    pub(crate) fn post(&self, call: LoopCall) {
        self.push(Job::Call(call));
    }

    fn push(&self, job: Job) {
        lock(&self.queue).push_back(job);
        if !self.drain_pending.swap(true, Ordering::AcqRel) {
            self.wake();
        }
    }

    fn wake(&self) {
        let scheduled = self.drain.get().is_some_and(|drain| {
            Python::try_attach(|py| {
                self.event_loop
                    .bind(py)
                    .call_method1(intern!(py, "call_soon_threadsafe"), (drain.bind(py),))
                    .is_ok()
            })
            .unwrap_or(false)
        });
        if !scheduled {
            self.fail_queued();
        }
    }

    /// The loop is closed or the interpreter is shutting down: fail queued work now instead
    /// of leaving workers waiting.
    fn fail_queued(&self) {
        self.drain_pending.store(false, Ordering::Release);
        let jobs: Vec<Job> = lock(&self.queue).drain(..).collect();
        for job in jobs {
            if let Job::Start(job) = job {
                let _ = job.reply.send(unavailable(&job.request.request_id));
            }
        }
    }

    /// Runs with `drain_pending` set, so jobs queued meanwhile wake nobody; they are taken by
    /// the next pass. The flag is cleared only once the queue is seen empty.
    fn drain(&self, py: Python<'_>, this: &Bound<'_, PyAny>) {
        for _ in 0..DRAIN_PASSES {
            let jobs: Vec<Job> = lock(&self.queue).drain(..).collect();
            if jobs.is_empty() {
                self.drain_pending.store(false, Ordering::Release);
                // A job queued between the take and the store saw the flag set and did not
                // wake the loop: keep draining unless a later push already re-armed it.
                if lock(&self.queue).is_empty() || self.drain_pending.swap(true, Ordering::AcqRel) {
                    return;
                }
                continue;
            }
            self.run(py, jobs);
        }
        // Still busy: continue after the callbacks made ready so far (the flag stays set).
        let rescheduled = self
            .event_loop
            .bind(py)
            .call_method1(intern!(py, "call_soon"), (this,))
            .is_ok();
        if !rescheduled {
            self.fail_queued();
        }
    }

    fn run(&self, py: Python<'_>, jobs: Vec<Job>) {
        for job in jobs {
            match job {
                Job::Start(job) => self.start(py, *job),
                Job::Cancel(state) => {
                    if let Some(task) = lock(&state.task).take() {
                        let _ = task.bind(py).call_method0(intern!(py, "cancel"));
                    }
                }
                Job::Call(call) => call(py),
            }
        }
    }

    fn start(&self, py: Python<'_>, job: StartJob) {
        if job.state.abandoned.load(Ordering::Acquire) {
            return;
        }
        let route_id = job.request.route_id;
        let request_id = job.request.request_id.clone();
        let endpoint = &self.endpoints[route_id];
        let started = (|| -> PyResult<Bound<'_, PyAny>> {
            // The task copies the current context when it is created: setting the request id
            // around its creation gives this request's task (and only it) the id.
            let context = request_id_context(py)?.bind(py);
            let token = context.call_method1(intern!(py, "set"), (request_id.as_str(),))?;
            let task = (|| {
                let request = Bound::new(py, Request::new(job.request))?;
                let coroutine = endpoint.func.bind(py).call1((request,))?;
                types(py)?
                    .eager_task_factory
                    .bind(py)
                    .call1((self.event_loop.bind(py), coroutine))
            })();
            context.call_method1(intern!(py, "reset"), (token,))?;
            task
        })();
        let task = match started {
            Ok(task) => task,
            Err(err) => {
                let response = errors::from_call_error(
                    py,
                    CallError::Raised(err),
                    &request_id,
                    &endpoint.name,
                    self.debug,
                );
                let _ = job.reply.send(response);
                return;
            }
        };
        let finished = task
            .call_method0(intern!(py, "done"))
            .and_then(|done| done.is_truthy())
            .unwrap_or(false);
        if finished {
            let response = task_outcome(py, &task, &request_id, &endpoint.name, self.debug);
            let _ = job.reply.send(response);
            return;
        }
        *lock(&job.state.task) = Some(task.clone().unbind());
        let callback = TaskDone {
            reply: Mutex::new(Some(job.reply)),
            request_id,
            state: job.state,
            debug: self.debug,
            handler: endpoint.name.clone(),
        };
        let attached = Bound::new(py, callback)
            .and_then(|callback| task.call_method1(intern!(py, "add_done_callback"), (callback,)));
        if let Err(err) = attached {
            tracing::error!(target: "ferro.server", "could not watch handler task: {err}");
        }
    }
}

fn task_outcome(
    py: Python<'_>,
    task: &Bound<'_, PyAny>,
    request_id: &str,
    handler: &str,
    debug: bool,
) -> FerroResponse {
    let outcome = (|| -> PyResult<Result<Bound<'_, PyAny>, CallError>> {
        if task.call_method0(intern!(py, "cancelled"))?.is_truthy()? {
            return Ok(Err(CallError::Cancelled));
        }
        let exception = task.call_method0(intern!(py, "exception"))?;
        if !exception.is_none() {
            return Ok(Err(CallError::Raised(PyErr::from_value(exception))));
        }
        Ok(Ok(task.call_method0(intern!(py, "result"))?))
    })();
    match outcome {
        Ok(Ok(value)) => convert::to_response(&value)
            .unwrap_or_else(|err| errors::from_convert_error(py, err, request_id, handler, debug)),
        Ok(Err(error)) => errors::from_call_error(py, error, request_id, handler, debug),
        Err(err) => errors::from_call_error(py, CallError::Raised(err), request_id, handler, debug),
    }
}

/// `loop.call_soon_threadsafe` target: processes every queued job on the event-loop thread.
#[pyclass(module = "ferro._ferro", frozen)]
pub(crate) struct Drain {
    executor: Weak<Executor>,
}

#[pymethods]
impl Drain {
    fn __call__(slf: &Bound<'_, Self>) {
        let weak = &slf.get().executor;
        let Some(executor) = weak.upgrade() else {
            return;
        };
        LOOP_EXECUTOR.with(|slot| {
            let mut slot = slot.borrow_mut();
            if !slot.ptr_eq(weak) {
                *slot = weak.clone();
            }
        });
        executor.drain(slf.py(), slf.as_any());
    }
}

/// Done-callback for handler tasks that suspended; runs on the event-loop thread.
#[pyclass(module = "ferro._ferro", frozen)]
pub(crate) struct TaskDone {
    reply: Mutex<Option<oneshot::Sender<FerroResponse>>>,
    request_id: String,
    state: Arc<CallState>,
    debug: bool,
    handler: String,
}

#[pymethods]
impl TaskDone {
    fn __call__(&self, task: &Bound<'_, PyAny>) {
        lock(&self.state.task).take();
        let Some(reply) = lock(&self.reply).take() else {
            return;
        };
        if reply.is_closed() {
            return;
        }
        let response = task_outcome(task.py(), task, &self.request_id, &self.handler, self.debug);
        let _ = reply.send(response);
    }
}

/// Handle used by engine workers: submits calls without touching the interpreter.
pub(crate) struct Bridge {
    executor: Arc<Executor>,
}

impl Bridge {
    pub(crate) fn new(py: Python<'_>, executor: Executor) -> PyResult<Self> {
        let executor = Arc::new(executor);
        let drain = Py::new(
            py,
            Drain {
                executor: Arc::downgrade(&executor),
            },
        )?
        .into_any();
        let _ = executor.drain.set(drain);
        Ok(Self { executor })
    }

    pub(crate) fn submit(&self, request: FerroRequest) -> PendingCall {
        let (reply, receiver) = oneshot::channel();
        let state = Arc::new(CallState::default());
        let request_id = request.request_id.clone();
        self.executor.push(Job::Start(Box::new(StartJob {
            request,
            reply,
            state: state.clone(),
        })));
        PendingCall {
            receiver,
            state,
            executor: self.executor.clone(),
            request_id,
            finished: false,
        }
    }
}

pub(crate) struct PendingCall {
    receiver: oneshot::Receiver<FerroResponse>,
    state: Arc<CallState>,
    executor: Arc<Executor>,
    request_id: String,
    finished: bool,
}

impl Future for PendingCall {
    type Output = FerroResponse;

    fn poll(mut self: Pin<&mut Self>, cx: &mut Context<'_>) -> Poll<Self::Output> {
        match Pin::new(&mut self.receiver).poll(cx) {
            Poll::Ready(result) => {
                self.finished = true;
                Poll::Ready(result.unwrap_or_else(|_| unavailable(&self.request_id)))
            }
            Poll::Pending => Poll::Pending,
        }
    }
}

impl Drop for PendingCall {
    fn drop(&mut self) {
        if !self.finished {
            self.state.abandoned.store(true, Ordering::Release);
            self.executor.push(Job::Cancel(self.state.clone()));
        }
    }
}
