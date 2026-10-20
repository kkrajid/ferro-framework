//! PostgreSQL backend for the Ferro ORM (Feature 003).
//!
//! The backend owns a small Tokio runtime and one worker task per pooled connection; workers pull
//! jobs from a bounded channel, reconnect when their connection broke, and report through the same
//! callback contract as the SQLite backend. The driver and TLS libraries stay private to this
//! crate (constitution II).

#![forbid(unsafe_code)]

mod config;
mod errors;
mod listener;
pub mod numeric;
pub mod pgtime;
mod tls;
mod values;
mod worker;

use std::sync::atomic::AtomicBool;
use std::sync::{Arc, Mutex};
use std::time::Duration;

use async_channel::{Sender, TrySendError};
use ferro_database::{Backend, Completion, DbError, Job, SessionRegistry, reject};
use tokio::runtime::Runtime;
use tokio::task::JoinHandle;

pub use config::{PgConfig, TlsMode};

pub(crate) struct Task {
    pub(crate) job: Job,
    pub(crate) cancelled: Arc<AtomicBool>,
    pub(crate) done: Completion,
}

pub struct PgBackend {
    sender: Mutex<Option<Sender<Task>>>,
    runtime: Mutex<Option<Runtime>>,
    workers: Mutex<Vec<JoinHandle<()>>>,
    sessions: SessionRegistry,
    shared: Arc<worker::Shared>,
}

impl std::fmt::Debug for PgBackend {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("PgBackend").finish_non_exhaustive()
    }
}

impl PgBackend {
    /// Connects once immediately (so bad URLs, credentials and hosts fail here) and starts the
    /// pool; the remaining connections open on first use.
    pub fn open(config: PgConfig) -> Result<Self, DbError> {
        let runtime = tokio::runtime::Builder::new_multi_thread()
            .worker_threads(2)
            .thread_name("ferro-pg")
            .enable_all()
            .build()
            .map_err(|err| DbError::Other {
                message: format!("could not start the database runtime: {err}"),
            })?;
        let shared = Arc::new(worker::Shared {
            connector: tls::connector(&config)?,
            config,
        });
        let first = runtime.block_on(worker::connect(&shared))?;
        let (sender, receiver) = async_channel::bounded::<Task>(shared.config.queue_size);
        let mut first = Some(first);
        let workers = (0..shared.config.pool_size)
            .map(|_| runtime.spawn(worker::run(shared.clone(), first.take(), receiver.clone())))
            .collect();
        Ok(Self {
            sender: Mutex::new(Some(sender)),
            runtime: Mutex::new(Some(runtime)),
            workers: Mutex::new(workers),
            sessions: SessionRegistry::default(),
            shared,
        })
    }
}

impl Backend for PgBackend {
    fn listen(
        &self,
        channel: &str,
        deliver: Arc<dyn Fn(String) + Send + Sync>,
    ) -> Result<ferro_database::Listener, DbError> {
        let runtime = self.runtime.lock().unwrap_or_else(|e| e.into_inner());
        let runtime = runtime.as_ref().ok_or(DbError::Closed)?;
        let task = runtime.spawn(listener::run(
            self.shared.clone(),
            channel.to_string(),
            deliver,
        ));
        Ok(ferro_database::Listener::new(move || task.abort()))
    }

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
            Err(TrySendError::Full(task)) => reject(
                task.job,
                task.done,
                DbError::Unavailable {
                    message: "the database is busy: too many queries are waiting (queue_size)"
                        .into(),
                },
            ),
            Err(TrySendError::Closed(task)) => reject(task.job, task.done, DbError::Closed),
        }
    }

    fn close(&self) {
        if let Some(sender) = self.sender.lock().unwrap_or_else(|e| e.into_inner()).take() {
            sender.close();
        }
        self.sessions.close_all();
        let workers: Vec<_> = self
            .workers
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .drain(..)
            .collect();
        if let Some(runtime) = self
            .runtime
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .take()
        {
            runtime.block_on(async {
                for worker in workers {
                    let _ = worker.await;
                }
            });
            runtime.shutdown_timeout(Duration::from_secs(5));
        }
    }
}

impl Drop for PgBackend {
    fn drop(&mut self) {
        self.close();
    }
}
