//! Password hashing for `ferro.auth` (Feature 007 research R1). Argon2id is deliberately slow,
//! so it runs on a small dedicated thread pool; Python awaits an `asyncio.Future` resolved on
//! the event loop, which keeps serving other requests meanwhile.

use std::sync::mpsc::{SyncSender, TrySendError, sync_channel};
use std::sync::{Arc, LazyLock, Mutex};

use ferro_core::passwords::Hasher;
use pyo3::exceptions::{PyRuntimeError, PyValueError};
use pyo3::intern;
use pyo3::prelude::*;

type Work = Box<dyn FnOnce() + Send>;

struct Pool {
    sender: Mutex<SyncSender<Work>>,
}

/// Hashes waiting per pool thread at most: a login flood is answered 503 instead of queueing
/// work (19 MiB and tens of milliseconds each) without bound.
const QUEUED_PER_THREAD: usize = 32;

static POOL: LazyLock<Pool> = LazyLock::new(|| {
    let threads = std::thread::available_parallelism()
        .map_or(2, |n| n.get())
        .clamp(1, 4);
    let (sender, receiver) = sync_channel::<Work>(threads * QUEUED_PER_THREAD);
    let receiver = Arc::new(Mutex::new(receiver));
    for i in 0..threads {
        let receiver = receiver.clone();
        let _ = std::thread::Builder::new()
            .name(format!("ferro-hash-{i}"))
            .spawn(move || {
                loop {
                    let work = receiver.lock().unwrap_or_else(|e| e.into_inner()).recv();
                    match work {
                        Ok(work) => work(),
                        Err(_) => break,
                    }
                }
            });
    }
    Pool {
        sender: Mutex::new(sender),
    }
});

static HASHER: LazyLock<Hasher> = LazyLock::new(Hasher::default);

/// The value (or error message) a pool job produced, handed to the event loop.
type Outcome = Result<Py<PyAny>, String>;

#[pyclass(module = "ferro._ferro", frozen)]
struct Resolve {
    future: Py<PyAny>,
    outcome: Mutex<Option<Outcome>>,
}

#[pymethods]
impl Resolve {
    fn __call__(&self, py: Python<'_>) -> PyResult<()> {
        let future = self.future.bind(py);
        if future.call_method0(intern!(py, "done"))?.is_truthy()? {
            return Ok(());
        }
        match self
            .outcome
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .take()
        {
            Some(Ok(value)) => future.call_method1(intern!(py, "set_result"), (value,))?,
            Some(Err(message)) => future.call_method1(
                intern!(py, "set_exception"),
                (PyValueError::new_err(message).value(py),),
            )?,
            None => return Ok(()),
        };
        Ok(())
    }
}

/// Runs `work` on the pool and returns a future for its result.
fn spawn<'py>(
    py: Python<'py>,
    work: impl FnOnce(Python<'_>) -> Outcome + Send + 'static,
) -> PyResult<Bound<'py, PyAny>> {
    let event_loop = py
        .import("asyncio")?
        .call_method0("get_running_loop")
        .map_err(|_| PyRuntimeError::new_err("password hashing must be awaited in async code"))?;
    let future = event_loop.call_method0(intern!(py, "create_future"))?;
    let target = future.clone().unbind();
    let event_loop = event_loop.unbind();
    let job: Work = Box::new(move || {
        Python::try_attach(|py| {
            // A request cancelled (timeout, client gone) while queued needs no hash.
            if target
                .bind(py)
                .call_method0(intern!(py, "done"))
                .and_then(|done| done.is_truthy())
                .unwrap_or(false)
            {
                return;
            }
            let outcome = work(py);
            let resolve = Resolve {
                future: target,
                outcome: Mutex::new(Some(outcome)),
            };
            if let Ok(resolve) = Py::new(py, resolve) {
                let _ = event_loop
                    .bind(py)
                    .call_method1(intern!(py, "call_soon_threadsafe"), (resolve,));
            }
        });
    });
    match POOL
        .sender
        .lock()
        .unwrap_or_else(|e| e.into_inner())
        .try_send(job)
    {
        Ok(()) => Ok(future),
        Err(TrySendError::Full(_)) => {
            let headers = pyo3::types::PyDict::new(py);
            headers.set_item("retry-after", "1")?;
            let busy = py
                .import("ferro.exceptions")?
                .getattr("HTTPException")?
                .call1((
                    503,
                    "Too many password checks at once; try again shortly",
                    headers,
                ))?;
            Err(PyErr::from_value(busy))
        }
        Err(TrySendError::Disconnected(_)) => Err(PyRuntimeError::new_err(
            "the password hashing pool is not running",
        )),
    }
}

/// `await hash_password(password)` → an Argon2id PHC string.
#[pyfunction]
pub(crate) fn hash_password(py: Python<'_>, password: String) -> PyResult<Bound<'_, PyAny>> {
    spawn(py, move |py| {
        // The GIL is released while hashing: only the result needs Python.
        let hashed = py.detach(|| HASHER.hash(&password));
        hashed
            .map(|hash| hash.into_pyobject(py).map(|s| s.into_any().unbind()))
            .map_err(|e| e.to_string())?
            .map_err(|e| e.to_string())
    })
}

/// `await check_password(password, stored)` → `(matches, needs_rehash)`.
#[pyfunction]
pub(crate) fn check_password(
    py: Python<'_>,
    password: String,
    stored: String,
) -> PyResult<Bound<'_, PyAny>> {
    spawn(py, move |py| {
        let (matches, rehash) = py.detach(|| {
            (
                HASHER.verify(&password, &stored),
                HASHER.needs_rehash(&stored),
            )
        });
        (matches, rehash)
            .into_pyobject(py)
            .map(|t| t.into_any().unbind())
            .map_err(|e| e.to_string())
    })
}
