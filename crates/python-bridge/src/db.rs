//! The process-wide database and the bridge between backend threads and asyncio (research R4).
//!
//! A query creates an `asyncio.Future` on the running loop and submits a job. The backend runs
//! it on its own thread; on completion the result is resolved on the loop thread, where rows
//! become Python objects in one pass. On a server's loop the completion joins the bridge's job
//! queue (no interpreter access from the backend thread while a drain is pending); elsewhere
//! the thread briefly attaches to schedule [`Deliver`] with `call_soon_threadsafe`.

use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex, Weak};

use ferro_database::{Backend, Completion, DbError, Job, JobResult};
use ferro_orm::compile::{Dialect, PostgresDialect, SqliteDialect};
use ferro_postgres::{PgBackend, PgConfig};
use ferro_sqlite::{SqliteBackend, SqliteConfig};
use pyo3::exceptions::PyRuntimeError;
use pyo3::intern;
use pyo3::prelude::*;

use crate::orm_values::{configuration_error, db_error, orm_types};

pub(crate) struct Database {
    pub(crate) backend: Arc<dyn Backend>,
    pub(crate) dialect: &'static dyn Dialect,
}

static DATABASE: Mutex<Option<Arc<Database>>> = Mutex::new(None);
static SQLITE: SqliteDialect = SqliteDialect;
static POSTGRES: PostgresDialect = PostgresDialect;

fn lock() -> std::sync::MutexGuard<'static, Option<Arc<Database>>> {
    DATABASE.lock().unwrap_or_else(|e| e.into_inner())
}

/// The connected database, if any (no interpreter needed).
pub(crate) fn connected() -> Option<Arc<Database>> {
    lock().clone()
}

pub(crate) fn current(py: Python<'_>) -> PyResult<Arc<Database>> {
    lock().clone().ok_or_else(|| {
        configuration_error(
            py,
            "no database configured; set database_url (App(database_url=...), \
             FERRO_DATABASE_URL or --database-url) or call `await ferro.db.connect(url)`",
        )
    })
}

/// The dialect of the connected database, or SQLite's when none is connected (`Query.sql()`).
pub(crate) fn dialect() -> &'static dyn Dialect {
    lock()
        .as_ref()
        .map_or(&SQLITE as &dyn Dialect, |db| db.dialect)
}

/// Opens the database for `url`, replacing (and closing) any previous one.
#[pyfunction]
#[pyo3(signature = (url, pool_size=None))]
pub(crate) fn db_connect(py: Python<'_>, url: &str, pool_size: Option<usize>) -> PyResult<()> {
    let config_error = |err: DbError| match err {
        DbError::Other { message } => configuration_error(py, message),
        other => db_error(py, other, None),
    };
    let (backend, dialect): (Arc<dyn Backend>, &'static dyn Dialect) = if url.starts_with("sqlite:")
    {
        let mut config = SqliteConfig::from_url(url).map_err(config_error)?;
        if let Some(pool_size) = pool_size {
            config = config.with_pool_size(pool_size);
        }
        let backend = py
            .detach(move || SqliteBackend::open(config))
            .map_err(|err| db_error(py, err, None))?;
        (Arc::new(backend), &SQLITE)
    } else if url.starts_with("postgresql:") || url.starts_with("postgres:") {
        let mut config = PgConfig::from_url(url).map_err(config_error)?;
        if let Some(pool_size) = pool_size {
            config = config.with_pool_size(pool_size);
        }
        let backend = py
            .detach(move || PgBackend::open(config))
            .map_err(|err| match err {
                DbError::Other { message } => configuration_error(py, message),
                other => db_error(py, other, None),
            })?;
        (Arc::new(backend), &POSTGRES)
    } else {
        let scheme = url.split(':').next().unwrap_or_default();
        return Err(configuration_error(
            py,
            format!(
                "unsupported database URL scheme '{scheme}'; supported: sqlite:///path/to/file.db,                  sqlite:///:memory:, postgresql://user:password@host:port/database"
            ),
        ));
    };
    let database = Arc::new(Database { backend, dialect });
    let previous = lock().replace(database);
    if let Some(previous) = previous {
        py.detach(move || previous.backend.close());
    }
    Ok(())
}

/// Closes the database after queued jobs finish.
#[pyfunction]
pub(crate) fn db_disconnect(py: Python<'_>) {
    let previous = lock().take();
    if let Some(previous) = previous {
        py.detach(move || previous.backend.close());
    }
}

#[pyfunction]
pub(crate) fn db_is_connected() -> bool {
    lock().is_some()
}

/// Turns a job result into the value the awaiting coroutine receives. Runs on the loop thread.
pub(crate) type Converter = Box<dyn FnOnce(Python<'_>, JobResult) -> PyResult<Py<PyAny>> + Send>;

/// Submits a job and returns an `asyncio.Future` resolved with `convert(result)`. Inside
/// `transaction()` the job runs on the transaction's connection.
pub(crate) fn submit<'py>(
    py: Python<'py>,
    database: &Arc<Database>,
    job: Job,
    convert: Converter,
) -> PyResult<Bound<'py, PyAny>> {
    if let Some(session) = crate::session::active(py)? {
        let session = session.get();
        if !Arc::ptr_eq(&session.database, database) {
            return Err(configuration_error(
                py,
                "the database was reconnected while a transaction was open",
            ));
        }
        return deliver_to(py, convert, |cancelled, done| {
            session.send(job, cancelled, done);
        });
    }
    deliver_to(py, convert, |cancelled, done| {
        database.backend.submit(job, cancelled, done);
    })
}

/// Creates an `asyncio.Future` on the running loop, hands `send` the cancellation flag and the
/// completion that resolves it on the loop thread, and returns the future.
pub(crate) fn deliver_to<'py>(
    py: Python<'py>,
    convert: Converter,
    send: impl FnOnce(Arc<AtomicBool>, Completion),
) -> PyResult<Bound<'py, PyAny>> {
    let event_loop = orm_types(py)?
        .get_running_loop
        .bind(py)
        .call0()
        .map_err(|_| {
            PyRuntimeError::new_err(
                "ORM queries must be awaited inside async code (no running event loop)",
            )
        })?;
    let result_future = event_loop.call_method0(intern!(py, "create_future"))?;
    let future = &result_future;
    let cancelled = Arc::new(AtomicBool::new(false));
    future.call_method1(
        intern!(py, "add_done_callback"),
        (CancelFlag {
            flag: cancelled.clone(),
        },),
    )?;
    let executor = crate::bridge::executor_for(&event_loop);
    let submitted = std::time::Instant::now();
    let future = future.clone().unbind();
    let event_loop = event_loop.unbind();
    send(
        cancelled,
        Box::new(move |result| {
            record_query(&result, submitted.elapsed());
            let job = DeliverJob {
                future,
                result,
                convert,
            };
            if let Some(executor) = executor.as_ref().and_then(Weak::upgrade) {
                executor.post(Box::new(move |py| job.run(py)));
                return;
            }
            Python::try_attach(|py| {
                let deliver = Deliver {
                    job: Mutex::new(Some(job)),
                };
                if let Ok(deliver) = Py::new(py, deliver) {
                    // Fails only if the loop is closed; then nobody is waiting.
                    let _ = event_loop
                        .bind(py)
                        .call_method1(intern!(py, "call_soon_threadsafe"), (deliver,));
                }
            });
        }),
    );
    Ok(result_future)
}

/// Query counts by outcome and their duration, submit to completion (Feature 011 US5).
#[derive(Default)]
struct QueryMetrics {
    ok: std::sync::atomic::AtomicU64,
    error: std::sync::atomic::AtomicU64,
    unavailable: std::sync::atomic::AtomicU64,
    duration: ferro_core::metrics::Histogram,
}

static QUERY_METRICS: std::sync::LazyLock<QueryMetrics> =
    std::sync::LazyLock::new(QueryMetrics::default);

fn record_query(result: &ferro_database::JobResult, elapsed: std::time::Duration) {
    let metrics = &*QUERY_METRICS;
    let counter = match result {
        Ok(_) => &metrics.ok,
        Err(DbError::Unavailable { .. } | DbError::Closed) => &metrics.unavailable,
        Err(_) => &metrics.error,
    };
    counter.fetch_add(1, Ordering::Relaxed);
    metrics.duration.observe(elapsed);
}

/// The database series of a metrics response.
pub(crate) fn write_metrics(out: &mut String) {
    use std::fmt::Write as _;
    let metrics = &*QUERY_METRICS;
    let total = "ferro_db_queries_total";
    ferro_core::metrics::type_line(out, total, "counter");
    for (outcome, counter) in [
        ("ok", &metrics.ok),
        ("error", &metrics.error),
        ("unavailable", &metrics.unavailable),
    ] {
        let _ = writeln!(
            out,
            "{total}{{outcome=\"{outcome}\"}} {}",
            counter.load(Ordering::Relaxed)
        );
    }
    let duration = "ferro_db_query_duration_seconds";
    ferro_core::metrics::type_line(out, duration, "histogram");
    metrics.duration.render(out, duration, "");
}

#[pyclass(module = "ferro._ferro", frozen)]
struct CancelFlag {
    flag: Arc<AtomicBool>,
}

#[pymethods]
impl CancelFlag {
    fn __call__(&self, future: &Bound<'_, PyAny>) -> PyResult<()> {
        if future
            .call_method0(intern!(future.py(), "cancelled"))?
            .is_truthy()?
        {
            self.flag.store(true, Ordering::Release);
        }
        Ok(())
    }
}

/// A finished job and the future it resolves (on the loop thread).
struct DeliverJob {
    future: Py<PyAny>,
    result: JobResult,
    convert: Converter,
}

impl DeliverJob {
    fn resolve(self, py: Python<'_>) -> PyResult<()> {
        let future = self.future.bind(py);
        if future.call_method0(intern!(py, "done"))?.is_truthy()? {
            return Ok(());
        }
        match (self.convert)(py, self.result) {
            Ok(value) => future.call_method1(intern!(py, "set_result"), (value,))?,
            Err(err) => future.call_method1(intern!(py, "set_exception"), (err.value(py),))?,
        };
        Ok(())
    }

    /// From the bridge's queue: errors are reported like those of asyncio callbacks.
    fn run(self, py: Python<'_>) {
        if let Err(err) = self.resolve(py) {
            err.write_unraisable(py, None);
        }
    }
}

#[pyclass(module = "ferro._ferro", frozen)]
struct Deliver {
    job: Mutex<Option<DeliverJob>>,
}

#[pymethods]
impl Deliver {
    fn __call__(&self, py: Python<'_>) -> PyResult<()> {
        let job = self.job.lock().unwrap_or_else(|e| e.into_inner()).take();
        job.map_or(Ok(()), |job| job.resolve(py))
    }
}
