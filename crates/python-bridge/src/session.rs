//! Transactions (Feature 005 research R8): a [`Session`] reserves one pooled connection; while a
//! session is set in the `transaction_context` context variable, every ORM job of that task (and
//! of tasks it starts, which inherit the context) goes to the session's connection.

use std::sync::atomic::{AtomicBool, AtomicU32, Ordering};
use std::sync::{Arc, Mutex};

use ferro_database::{Job, SessionSender, SessionTask, TransactionCommand, session_channel};
use pyo3::prelude::*;
use pyo3::sync::PyOnceLock;
use pyo3::types::IntoPyDict;

use crate::db::{Converter, Database, current, deliver_to};
use crate::orm_values::{db_error, orm_types};

static CONTEXT: PyOnceLock<Py<PyAny>> = PyOnceLock::new();

/// `contextvars.ContextVar("ferro_transaction", default=None)`.
pub(crate) fn context(py: Python<'_>) -> PyResult<&Py<PyAny>> {
    CONTEXT.get_or_try_init(py, || {
        Ok(py
            .import("contextvars")?
            .getattr("ContextVar")?
            .call(
                ("ferro_transaction",),
                Some(&[("default", py.None())].into_py_dict(py)?),
            )?
            .unbind())
    })
}

#[pyclass(module = "ferro._ferro", frozen)]
pub(crate) struct Session {
    pub(crate) database: Arc<Database>,
    sender: Mutex<Option<SessionSender>>,
    savepoints: AtomicU32,
}

/// The session active in the current context, if any.
pub(crate) fn active(py: Python<'_>) -> PyResult<Option<Py<Session>>> {
    let value = context(py)?
        .bind(py)
        .call_method0(pyo3::intern!(py, "get"))?;
    if value.is_none() {
        return Ok(None);
    }
    Ok(Some(value.cast_into::<Session>()?.unbind()))
}

impl Session {
    /// Queues `job` on the session's connection. Fails with `Closed` once the session ended.
    pub(crate) fn send(
        &self,
        job: Job,
        cancelled: Arc<AtomicBool>,
        done: ferro_database::Completion,
    ) {
        let sender = self
            .sender
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .clone();
        let task = SessionTask {
            job,
            cancelled,
            done,
        };
        match sender {
            Some(sender) => sender.send(task),
            None => (task.done)(Err(ferro_database::DbError::Operational {
                message: "the transaction has already ended".into(),
            })),
        }
    }

    fn control<'py>(
        &self,
        py: Python<'py>,
        command: TransactionCommand,
    ) -> PyResult<Bound<'py, PyAny>> {
        let convert: Converter = Box::new(|py, result| match result {
            Ok(_) => Ok(py.None()),
            Err(err) => Err(db_error(py, err, None)),
        });
        deliver_to(py, convert, |cancelled, done| {
            self.send(Job::Transaction(command), cancelled, done);
        })
    }
}

#[pymethods]
impl Session {
    /// Reserves a connection of the current database for a new session.
    #[new]
    fn new(py: Python<'_>) -> PyResult<Self> {
        orm_types(py)?;
        let database = current(py)?;
        let (sender, receiver) = session_channel();
        database.backend.submit(
            Job::Session(receiver),
            Arc::new(AtomicBool::new(false)),
            Box::new(|_| {}),
        );
        Ok(Self {
            database,
            sender: Mutex::new(Some(sender)),
            savepoints: AtomicU32::new(0),
        })
    }

    fn begin<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyAny>> {
        self.control(py, TransactionCommand::Begin)
    }

    fn commit<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyAny>> {
        self.control(py, TransactionCommand::Commit)
    }

    fn rollback<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyAny>> {
        self.control(py, TransactionCommand::Rollback)
    }

    /// A new savepoint number (pass it to `savepoint`, `release`, `rollback_to`).
    fn next_savepoint(&self) -> u32 {
        self.savepoints.fetch_add(1, Ordering::Relaxed) + 1
    }

    fn savepoint<'py>(&self, py: Python<'py>, number: u32) -> PyResult<Bound<'py, PyAny>> {
        self.control(py, TransactionCommand::Savepoint(number))
    }

    fn release<'py>(&self, py: Python<'py>, number: u32) -> PyResult<Bound<'py, PyAny>> {
        self.control(py, TransactionCommand::Release(number))
    }

    fn rollback_to<'py>(&self, py: Python<'py>, number: u32) -> PyResult<Bound<'py, PyAny>> {
        self.control(py, TransactionCommand::RollbackTo(number))
    }

    /// Ends the session: the connection rolls back anything still open and returns to the pool.
    fn close(&self) {
        self.sender.lock().unwrap_or_else(|e| e.into_inner()).take();
    }

    #[getter]
    fn closed(&self) -> bool {
        self.sender
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .as_ref()
            .is_none_or(SessionSender::is_closed)
    }
}
