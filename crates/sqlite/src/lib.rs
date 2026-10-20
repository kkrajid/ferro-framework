//! SQLite backend for the Ferro ORM (research R3, R4).
//!
//! SQLite is a blocking library, so each connection lives on its own worker thread and jobs
//! arrive through a bounded channel. Neither Python's event loop nor HTTP workers ever wait on
//! SQLite directly. The driver crate stays private to this crate (constitution II).

#![forbid(unsafe_code)]

mod errors;
mod worker;

use std::path::PathBuf;
use std::sync::atomic::AtomicBool;
use std::sync::{Arc, Mutex};
use std::thread::JoinHandle;
use std::time::Duration;

use crossbeam_channel::{Sender, TrySendError};
use ferro_database::{Backend, Completion, DbError, Job, SessionRegistry, reject};

/// Default number of jobs that may wait for a connection; beyond it new jobs fail at once
/// with [`DbError::Unavailable`] (`queue_size` URL parameter, Feature 011).
const QUEUE_CAPACITY: usize = 1024;
const QUEUE_LIMIT: usize = 1_000_000;
const FILE_POOL_SIZE: usize = 4;

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum SqlitePath {
    Memory,
    File(PathBuf),
}

#[derive(Debug, Clone)]
pub struct SqliteConfig {
    pub path: SqlitePath,
    pub pool_size: usize,
    pub busy_timeout: Duration,
    /// Jobs that may wait for a connection.
    pub queue_size: usize,
}

fn queue_full() -> DbError {
    DbError::Unavailable {
        message: "the database is busy: too many queries are waiting (queue_size)".into(),
    }
}

impl SqliteConfig {
    /// `sqlite:///relative.db`, `sqlite:////absolute.db`, `sqlite:///C:/x.db`,
    /// `sqlite:///:memory:`, optionally followed by `?queue_size=N&timeout=SECONDS`.
    pub fn from_url(url: &str) -> Result<Self, DbError> {
        let (url, query) = url.split_once('?').unwrap_or((url, ""));
        let mut queue_size = QUEUE_CAPACITY;
        let mut busy_timeout = Duration::from_secs(5);
        for pair in query.split('&').filter(|p| !p.is_empty()) {
            let (key, value) = pair.split_once('=').unwrap_or((pair, ""));
            match key {
                "queue_size" => {
                    queue_size = value
                        .parse::<usize>()
                        .ok()
                        .filter(|n| (1..=QUEUE_LIMIT).contains(n))
                        .ok_or_else(|| DbError::Other {
                            message: format!(
                                "invalid queue_size '{value}' in the database URL: expected 1 to {QUEUE_LIMIT}"
                            ),
                        })?;
                }
                // How long a write waits for another connection's lock (Feature 021).
                "timeout" => {
                    busy_timeout = value
                        .parse::<f64>()
                        .ok()
                        .filter(|s| s.is_finite() && (0.0..=3600.0).contains(s))
                        .map(Duration::from_secs_f64)
                        .ok_or_else(|| DbError::Other {
                            message: format!(
                                "invalid timeout '{value}' in the database URL: expected 0 to \
                                 3600 seconds"
                            ),
                        })?;
                }
                other => {
                    return Err(DbError::Other {
                        message: format!(
                            "unknown parameter '{other}' in the database URL (SQLite accepts \
                             queue_size and timeout)"
                        ),
                    });
                }
            }
        }
        let invalid = || DbError::Other {
            message: format!(
                "invalid database URL '{url}': expected sqlite:///path/to/file.db or \
                 sqlite:///:memory:"
            ),
        };
        let rest = url.strip_prefix("sqlite:///").ok_or_else(invalid)?;
        if rest.is_empty() {
            return Err(invalid());
        }
        let path = if rest == ":memory:" {
            SqlitePath::Memory
        } else {
            SqlitePath::File(PathBuf::from(rest))
        };
        let pool_size = match path {
            SqlitePath::Memory => 1,
            SqlitePath::File(_) => FILE_POOL_SIZE,
        };
        Ok(Self {
            path,
            pool_size,
            busy_timeout,
            queue_size,
        })
    }

    /// Sets the connection count; an in-memory database always uses one connection, because
    /// every connection to `:memory:` would be a separate database.
    pub fn with_pool_size(mut self, pool_size: usize) -> Self {
        self.pool_size = match self.path {
            SqlitePath::Memory => 1,
            SqlitePath::File(_) => pool_size.max(1),
        };
        self
    }
}

pub(crate) struct Task {
    pub(crate) job: Job,
    pub(crate) cancelled: Arc<AtomicBool>,
    pub(crate) done: Completion,
}

pub struct SqliteBackend {
    sender: Mutex<Option<Sender<Task>>>,
    workers: Mutex<Vec<JoinHandle<()>>>,
    sessions: SessionRegistry,
}

impl std::fmt::Debug for SqliteBackend {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("SqliteBackend").finish_non_exhaustive()
    }
}

impl SqliteBackend {
    /// Opens every connection up front so configuration problems (bad path, permissions)
    /// surface immediately.
    pub fn open(config: SqliteConfig) -> Result<Self, DbError> {
        let connections = (0..config.pool_size.max(1))
            .map(|_| worker::open_connection(&config))
            .collect::<Result<Vec<_>, _>>()?;
        let (sender, receiver) = crossbeam_channel::bounded::<Task>(config.queue_size);
        let workers = connections
            .into_iter()
            .enumerate()
            .map(|(i, connection)| {
                let receiver = receiver.clone();
                std::thread::Builder::new()
                    .name(format!("ferro-db-{i}"))
                    .spawn(move || worker::run(connection, receiver))
                    .map_err(|err| DbError::Other {
                        message: format!("could not start database thread: {err}"),
                    })
            })
            .collect::<Result<Vec<_>, _>>()?;
        Ok(Self {
            sender: Mutex::new(Some(sender)),
            workers: Mutex::new(workers),
            sessions: SessionRegistry::default(),
        })
    }
}

impl Backend for SqliteBackend {
    fn submit(&self, job: Job, cancelled: Arc<AtomicBool>, done: Completion) {
        let sender = self
            .sender
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .clone();
        let Some(sender) = sender else {
            return reject(job, done, DbError::Closed);
        };
        self.sessions.track(&job);
        let task = Task {
            job,
            cancelled,
            done,
        };
        match sender.try_send(task) {
            Ok(()) => {}
            // Never wait here: the caller is the application's execution thread.
            Err(TrySendError::Full(task)) => reject(task.job, task.done, queue_full()),
            Err(TrySendError::Disconnected(task)) => {
                reject(task.job, task.done, DbError::Closed);
            }
        }
    }

    fn close(&self) {
        self.sender.lock().unwrap_or_else(|e| e.into_inner()).take();
        self.sessions.close_all();
        let workers: Vec<_> = self
            .workers
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .drain(..)
            .collect();
        for worker in workers {
            let _ = worker.join();
        }
    }
}

impl Drop for SqliteBackend {
    fn drop(&mut self) {
        self.close();
    }
}
