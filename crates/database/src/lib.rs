//! Database-independent building blocks shared by the ORM and every backend: values, statements,
//! results, errors, and the [`Backend`] trait.
//!
//! Backends are driver adapters (constitution III): they execute compiled [`Statement`]s and know
//! nothing about models. Completion is callback-based so blocking drivers (run on their own
//! threads) and async drivers fit the same trait.

#![forbid(unsafe_code)]

use std::fmt;
use std::sync::Arc;
use std::sync::atomic::AtomicBool;

/// A bound parameter or a result cell.
#[derive(Debug, Clone, PartialEq)]
pub enum Value {
    Null,
    Integer(i64),
    Real(f64),
    /// Exact decimal number as text (`"-12.3400"`); backends bind it natively when they can.
    Decimal(String),
    Text(String),
    Blob(Vec<u8>),
}

/// SQL text plus its parameters. SQL text never contains values (constitution IV).
#[derive(Debug, Clone, PartialEq)]
pub struct Statement {
    pub sql: String,
    pub params: Vec<Value>,
}

impl Statement {
    pub fn new(sql: impl Into<String>, params: Vec<Value>) -> Self {
        Self {
            sql: sql.into(),
            params,
        }
    }
}

#[derive(Debug, Clone, Default, PartialEq)]
pub struct Rows {
    pub columns: Vec<String>,
    pub rows: Vec<Vec<Value>>,
}

#[derive(Debug, Clone)]
pub enum Job {
    /// Statement producing rows (SELECT, or INSERT/UPDATE ... RETURNING).
    Query(Statement),
    /// Statement producing an affected-row count.
    Execute(Statement),
    /// Statements run in one transaction; all succeed or none do. With `returning`, each
    /// statement is run as a query and the first cell of its first row is collected. Inside a
    /// session's transaction a savepoint is used instead.
    Batch {
        statements: Vec<Statement>,
        returning: bool,
    },
    /// Schema statements (DDL plus bound back-fills) run atomically. SQLite suspends foreign key
    /// enforcement around them (table rebuilds) and checks integrity before committing. Refused
    /// inside a session's transaction.
    Schema(Vec<Statement>),
    /// Names of the user tables in the database.
    ListTables,
    /// Reserves one connection: the worker that takes this job runs only the session's jobs, in
    /// order, until every [`SessionSender`] is dropped, then rolls back anything left open.
    Session(SessionReceiver),
    /// Transaction control on a session's connection.
    Transaction(TransactionCommand),
}

/// Transaction control statements. Savepoint names are generated (`ferro_sp_<n>`), never taken
/// from user input.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum TransactionCommand {
    Begin,
    /// BEGIN with an isolation level (PostgreSQL; SQLite transactions are serializable already).
    BeginIsolated(Isolation),
    Commit,
    Rollback,
    Savepoint(u32),
    Release(u32),
    RollbackTo(u32),
}

/// Transaction isolation levels (`transaction(isolation=...)`, Feature 021).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Isolation {
    ReadCommitted,
    RepeatableRead,
    Serializable,
}

impl Isolation {
    /// `"read committed"`, `"repeatable read"` or `"serializable"` (any case).
    pub fn parse(name: &str) -> Option<Self> {
        match name.trim().to_ascii_lowercase().as_str() {
            "read committed" => Some(Self::ReadCommitted),
            "repeatable read" => Some(Self::RepeatableRead),
            "serializable" => Some(Self::Serializable),
            _ => None,
        }
    }

    /// PostgreSQL's BEGIN for this level.
    pub fn postgres_begin(self) -> &'static str {
        match self {
            Self::ReadCommitted => "BEGIN ISOLATION LEVEL READ COMMITTED",
            Self::RepeatableRead => "BEGIN ISOLATION LEVEL REPEATABLE READ",
            Self::Serializable => "BEGIN ISOLATION LEVEL SERIALIZABLE",
        }
    }
}

impl TransactionCommand {
    /// Whether the command opens a transaction.
    pub fn is_begin(self) -> bool {
        matches!(self, Self::Begin | Self::BeginIsolated(_))
    }

    /// SQL for the command; `begin` is the dialect's BEGIN statement.
    pub fn sql(self, begin: &str) -> String {
        match self {
            Self::Begin | Self::BeginIsolated(_) => begin.to_string(),
            Self::Commit => "COMMIT".to_string(),
            Self::Rollback => "ROLLBACK".to_string(),
            Self::Savepoint(n) => format!("SAVEPOINT ferro_sp_{n}"),
            Self::Release(n) => format!("RELEASE SAVEPOINT ferro_sp_{n}"),
            Self::RollbackTo(n) => {
                format!("ROLLBACK TO SAVEPOINT ferro_sp_{n}; RELEASE SAVEPOINT ferro_sp_{n}")
            }
        }
    }
}

/// A job sent to a session.
pub struct SessionTask {
    pub job: Job,
    pub cancelled: Arc<AtomicBool>,
    pub done: Completion,
}

impl fmt::Debug for SessionTask {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("SessionTask")
            .field("job", &self.job)
            .finish_non_exhaustive()
    }
}

/// Sending side of a session, held by the caller (the transaction).
#[derive(Debug, Clone)]
pub struct SessionSender(async_channel::Sender<SessionTask>);

/// Receiving side of a session, carried by [`Job::Session`] to a worker.
#[derive(Debug, Clone)]
pub struct SessionReceiver(async_channel::Receiver<SessionTask>);

/// A new session channel.
pub fn session_channel() -> (SessionSender, SessionReceiver) {
    let (sender, receiver) = async_channel::unbounded();
    (SessionSender(sender), SessionReceiver(receiver))
}

impl SessionSender {
    /// Queues a job on the session; if the session has ended the job fails with `Closed`.
    pub fn send(&self, task: SessionTask) {
        if let Err(rejected) = self.0.try_send(task) {
            (rejected.into_inner().done)(Err(DbError::Closed));
        }
    }

    pub fn is_closed(&self) -> bool {
        self.0.is_closed()
    }
}

impl SessionReceiver {
    /// Next job, blocking the current thread (thread-per-connection backends).
    pub fn recv_blocking(&self) -> Option<SessionTask> {
        self.0.recv_blocking().ok()
    }

    /// Next job (async backends).
    pub async fn recv(&self) -> Option<SessionTask> {
        self.0.recv().await.ok()
    }

    /// Ends the session: later sends fail, and jobs still queued fail with `Closed`.
    pub fn abandon(&self) {
        self.0.close();
        while let Ok(task) = self.0.try_recv() {
            (task.done)(Err(DbError::Closed));
        }
    }

    pub fn is_closed(&self) -> bool {
        self.0.is_closed()
    }
}

/// Sessions handed to a backend, so closing the backend can end them (otherwise a worker would
/// wait forever for a transaction that is never finished).
#[derive(Debug, Default)]
pub struct SessionRegistry(std::sync::Mutex<Vec<SessionReceiver>>);

impl SessionRegistry {
    pub fn track(&self, job: &Job) {
        if let Job::Session(receiver) = job {
            let mut sessions = self.0.lock().unwrap_or_else(|e| e.into_inner());
            sessions.retain(|s| !s.is_closed());
            sessions.push(receiver.clone());
        }
    }

    /// Stops every session from accepting jobs; queued jobs still run on their connections.
    pub fn close_all(&self) {
        for session in self.0.lock().unwrap_or_else(|e| e.into_inner()).drain(..) {
            session.0.close();
        }
    }
}

/// Completes a job that cannot run (backend closed, cancelled before start), including the jobs
/// queued on a session it carried.
pub fn reject(job: Job, done: Completion, error: DbError) {
    if let Job::Session(session) = &job {
        session.abandon();
    }
    done(Err(error));
}

/// A job that never starts because it was cancelled; sessions still release their queued jobs.
pub fn skip(job: &Job) {
    if let Job::Session(session) = job {
        session.abandon();
    }
}

#[derive(Debug, Clone, PartialEq)]
pub enum Outcome {
    Rows(Rows),
    Affected(u64),
    Batch {
        affected: Vec<u64>,
        returned: Vec<Option<Value>>,
    },
    Tables(Vec<String>),
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum IntegrityKind {
    Unique,
    NotNull,
    ForeignKey,
    Check,
    Other,
}

/// Errors reported by a backend. Messages come from the database itself and never name the
/// driver library (FR-024).
#[derive(Debug, Clone, PartialEq)]
pub enum DbError {
    Integrity {
        kind: IntegrityKind,
        table: Option<String>,
        columns: Vec<String>,
        message: String,
    },
    Operational {
        message: String,
    },
    /// The database cannot be reached or cannot take more work right now: no connection, a
    /// lost connection, a lock wait that expired, or a full job queue (Feature 011). Retrying
    /// later may succeed.
    Unavailable {
        message: String,
    },
    /// A statement referenced a table that does not exist.
    MissingTable {
        message: String,
    },
    Closed,
    Other {
        message: String,
    },
}

impl fmt::Display for DbError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Integrity { message, .. }
            | Self::Operational { message }
            | Self::Unavailable { message }
            | Self::MissingTable { message }
            | Self::Other { message } => f.write_str(message),
            Self::Closed => f.write_str("the database connection is closed"),
        }
    }
}

impl std::error::Error for DbError {}

pub type JobResult = Result<Outcome, DbError>;

/// Called exactly once with a job's result, on whichever thread finished it. Not called for a job
/// skipped because it was cancelled before it started.
pub type Completion = Box<dyn FnOnce(JobResult) + Send + 'static>;

pub trait Backend: Send + Sync + 'static {
    /// Queues a job. If `cancelled` is set before the job starts, the job is skipped.
    fn submit(&self, job: Job, cancelled: Arc<AtomicBool>, done: Completion);

    /// Stops accepting jobs and releases connections after queued jobs finish.
    fn close(&self);

    /// Delivers the payload of every notification sent on `channel` (PostgreSQL
    /// `LISTEN`/`NOTIFY`, Feature 018) until the returned listener is dropped.
    fn listen(
        &self,
        _channel: &str,
        _deliver: Arc<dyn Fn(String) + Send + Sync>,
    ) -> Result<Listener, DbError> {
        Err(DbError::Other {
            message: "notifications (LISTEN/NOTIFY) need a PostgreSQL database".into(),
        })
    }
}

/// A running notification listener; dropping it stops listening.
pub struct Listener(Option<Box<dyn FnOnce() + Send + Sync>>);

impl Listener {
    pub fn new(stop: impl FnOnce() + Send + Sync + 'static) -> Self {
        Self(Some(Box::new(stop)))
    }
}

impl Drop for Listener {
    fn drop(&mut self) {
        if let Some(stop) = self.0.take() {
            stop();
        }
    }
}

impl fmt::Debug for Listener {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str("Listener")
    }
}
