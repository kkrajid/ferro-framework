//! One worker per pooled connection: runs jobs sequentially, caches prepared statements and
//! reconnects when the connection broke (research R2).

use std::collections::HashMap;
use std::sync::Arc;
use std::sync::atomic::Ordering;
use std::time::Instant;

use async_channel::Receiver;
use ferro_database::{
    DbError, Job, JobResult, Outcome, Rows, SessionReceiver, Statement, TransactionCommand, Value,
    skip,
};
use tokio_postgres::types::ToSql;
use tokio_postgres::{Client, GenericClient};
use tokio_postgres_rustls::MakeRustlsConnect;

use crate::Task;
use crate::config::PgConfig;
use crate::errors::{connect_error, map_error};
use crate::values::{Param, decode_row};

const STATEMENT_CACHE: usize = 256;

pub(crate) struct Shared {
    pub(crate) config: PgConfig,
    pub(crate) connector: MakeRustlsConnect,
}

pub(crate) struct Connection {
    client: Client,
    statements: HashMap<String, tokio_postgres::Statement>,
    /// A session opened a transaction that has not ended yet.
    in_transaction: bool,
    /// The transaction's BEGIN has not been sent yet: it travels with the first statement
    /// (Feature 020), and a transaction without statements never sends BEGIN or COMMIT.
    begin_pending: bool,
    /// The BEGIN statement of the pending or open transaction (with its isolation level).
    begin_statement: &'static str,
    opened: Instant,
    last_used: Instant,
}

impl Connection {
    /// Whether the connection must be replaced before its next job: broken, or past
    /// `max_lifetime` / `max_idle` (never inside a transaction).
    fn needs_replacing(&self, config: &PgConfig) -> bool {
        if self.client.is_closed() {
            return true;
        }
        if self.in_transaction {
            return false;
        }
        config
            .max_lifetime
            .is_some_and(|limit| self.opened.elapsed() >= limit)
            || config
                .max_idle
                .is_some_and(|limit| self.last_used.elapsed() >= limit)
    }

    /// Whether the next statement must carry the pending BEGIN (and clears it).
    fn take_begin(&mut self) -> Option<&'static str> {
        std::mem::take(&mut self.begin_pending).then_some(self.begin_statement)
    }

    /// Sends a pending BEGIN on its own (before savepoints and batches).
    async fn flush_begin(&mut self) -> Result<(), DbError> {
        if let Some(begin) = self.take_begin() {
            if let Err(err) = self.client.batch_execute(begin).await {
                self.in_transaction = false;
                return Err(map_error(err));
            }
        }
        Ok(())
    }
}

/// A failure of a statement sent with [`with_begin_ref`]: `begin_failed` when the pipelined
/// BEGIN itself failed (no transaction is open), else the statement failed inside it.
struct Failed {
    error: DbError,
    begin_failed: bool,
}

/// Runs `statement` with a pending BEGIN pipelined in front of it: both requests leave in one
/// round trip. BEGIN is Ferro's fixed statement, which fails only when the connection itself
/// fails, and then the statement after it fails too.
async fn with_begin_ref<T>(
    client: &Client,
    statement: impl std::future::Future<Output = Result<T, tokio_postgres::Error>>,
    begin: Option<&'static str>,
) -> Result<T, Failed> {
    let failed = |err, begin_failed| Failed {
        error: map_error(err),
        begin_failed,
    };
    let Some(begin) = begin else {
        return statement.await.map_err(|err| failed(err, false));
    };
    let (begun, result) = tokio::join!(client.batch_execute(begin), statement);
    begun.map_err(|err| failed(err, true))?;
    result.map_err(|err| failed(err, false))
}

impl Connection {
    /// The error of [`with_begin_ref`], after ending the transaction state if BEGIN failed.
    fn settle(&mut self, failed: Failed) -> DbError {
        if failed.begin_failed {
            self.in_transaction = false;
        }
        failed.error
    }
}

pub(crate) async fn connect(shared: &Shared) -> Result<Connection, DbError> {
    let (client, connection) = shared
        .config
        .driver
        .connect(shared.connector.clone())
        .await
        .map_err(|err| connect_error(&shared.config, &err))?;
    tokio::spawn(async move {
        // Ends when the server closes the connection; the worker notices `is_closed()`.
        let _ = connection.await;
    });
    Ok(Connection {
        client,
        statements: HashMap::new(),
        in_transaction: false,
        begin_pending: false,
        begin_statement: "BEGIN",
        opened: Instant::now(),
        last_used: Instant::now(),
    })
}

pub(crate) async fn run(
    shared: Arc<Shared>,
    mut connection: Option<Connection>,
    jobs: Receiver<Task>,
) {
    while let Ok(task) = jobs.recv().await {
        if task.cancelled.load(Ordering::Acquire) {
            skip(&task.job);
            continue;
        }
        let result = match task.job {
            Job::Session(session) => run_session(&shared, &mut connection, &session).await,
            job => run_job(&shared, &mut connection, job).await,
        };
        (task.done)(result);
    }
}

/// Runs one job, reconnecting first when the connection broke.
async fn run_job(shared: &Shared, connection: &mut Option<Connection>, job: Job) -> JobResult {
    if connection
        .as_ref()
        .is_none_or(|c| c.needs_replacing(&shared.config))
    {
        *connection = None;
        *connection = Some(connect(shared).await?);
    }
    let active = connection.as_mut().expect("connected above");
    active.last_used = Instant::now();
    let retry = job.clone();
    let result = match execute(active, job).await {
        // A schema change (migration) invalidated a cached prepared statement: refresh the
        // cache and run the job once more (Feature 004 research R6). Inside a transaction the
        // failure has already aborted it, so it is reported instead.
        Err(DbError::Operational { message })
            if message.contains("cached plan") && !active.in_transaction =>
        {
            active.statements.clear();
            execute(active, retry).await
        }
        other => other,
    };
    if active.client.is_closed() {
        *connection = None;
    }
    result
}

/// Runs a session's jobs on this connection until the session ends, then rolls back a
/// transaction left open (dropped or cancelled `transaction()` blocks). If the connection is
/// lost while a transaction is open, the rest of the session fails instead of silently running
/// outside the transaction on a new connection.
async fn run_session(
    shared: &Shared,
    connection: &mut Option<Connection>,
    session: &SessionReceiver,
) -> JobResult {
    let mut lost = false;
    while let Some(task) = session.recv().await {
        if task.cancelled.load(Ordering::Acquire) {
            skip(&task.job);
            continue;
        }
        if !lost
            && let Some(active) = connection.as_ref()
            && active.in_transaction
            && active.client.is_closed()
        {
            // The connection broke between two statements of the transaction.
            if active.begin_pending {
                // Nothing was sent yet: the transaction starts on a new connection.
                match connect(shared).await {
                    Ok(mut fresh) => {
                        fresh.in_transaction = true;
                        fresh.begin_pending = true;
                        fresh.begin_statement = active.begin_statement;
                        *connection = Some(fresh);
                    }
                    Err(err) => {
                        *connection = None;
                        lost = true;
                        (task.done)(Err(err));
                        continue;
                    }
                }
            } else {
                // Its statements were rolled back with it; reconnecting would run the rest
                // outside any transaction.
                *connection = None;
                lost = true;
            }
        }
        if lost {
            (task.done)(Err(DbError::Unavailable {
                message: "the database connection was lost during the transaction; it was \
                          rolled back"
                    .into(),
            }));
            continue;
        }
        let was_open = connection.as_ref().is_some_and(|c| c.in_transaction);
        let result = run_job(shared, connection, task.job).await;
        if was_open && connection.as_ref().is_none_or(|c| c.client.is_closed()) {
            lost = true;
        }
        (task.done)(result);
    }
    session.abandon();
    if let Some(active) = connection.as_mut()
        && active.in_transaction
    {
        active.in_transaction = false;
        if active.take_begin().is_none() {
            active
                .client
                .batch_execute("ROLLBACK")
                .await
                .map_err(map_error)?;
        }
    }
    Ok(Outcome::Affected(0))
}

async fn prepare(
    connection: &mut Connection,
    sql: &str,
) -> Result<tokio_postgres::Statement, tokio_postgres::Error> {
    if let Some(statement) = connection.statements.get(sql) {
        return Ok(statement.clone());
    }
    let statement = connection.client.prepare(sql).await?;
    if connection.statements.len() >= STATEMENT_CACHE {
        connection.statements.clear();
    }
    connection
        .statements
        .insert(sql.to_string(), statement.clone());
    Ok(statement)
}

fn params(statement: &Statement) -> Vec<Param<'_>> {
    statement.params.iter().map(Param).collect()
}

fn refs<'a>(params: &'a [Param<'a>]) -> Vec<&'a (dyn ToSql + Sync)> {
    params.iter().map(|p| p as &(dyn ToSql + Sync)).collect()
}

fn decode_error(err: Box<dyn std::error::Error + Sync + Send>) -> DbError {
    DbError::Other {
        message: format!("could not read a database value: {err}"),
    }
}

async fn query(connection: &mut Connection, statement: &Statement) -> JobResult {
    let prepared = prepare(connection, &statement.sql)
        .await
        .map_err(map_error)?;
    let values = params(statement);
    let values = refs(&values);
    let begin = connection.take_begin();
    let client = &connection.client;
    let rows = match with_begin_ref(client, client.query(&prepared, &values), begin).await {
        Ok(rows) => rows,
        Err(failed) => return Err(connection.settle(failed)),
    };
    let columns = prepared
        .columns()
        .iter()
        .map(|c| c.name().to_string())
        .collect();
    let rows = rows
        .iter()
        .map(decode_row)
        .collect::<Result<Vec<_>, _>>()
        .map_err(decode_error)?;
    Ok(Outcome::Rows(Rows { columns, rows }))
}

async fn execute(connection: &mut Connection, job: Job) -> JobResult {
    match job {
        Job::Query(statement) => query(connection, &statement).await,
        Job::Execute(statement) => {
            let prepared = prepare(connection, &statement.sql)
                .await
                .map_err(map_error)?;
            let values = params(&statement);
            let values = refs(&values);
            let begin = connection.take_begin();
            let client = &connection.client;
            match with_begin_ref(client, client.execute(&prepared, &values), begin).await {
                Ok(affected) => Ok(Outcome::Affected(affected)),
                Err(failed) => Err(connection.settle(failed)),
            }
        }
        Job::Batch {
            statements,
            returning,
        } => {
            if connection.in_transaction {
                connection.flush_begin().await?;
                // Inside a session's transaction: a savepoint keeps the batch atomic.
                let client = &connection.client;
                client
                    .batch_execute("SAVEPOINT ferro_batch")
                    .await
                    .map_err(map_error)?;
                match run_statements(client, &statements, returning).await {
                    Ok(outcome) => {
                        client
                            .batch_execute("RELEASE SAVEPOINT ferro_batch")
                            .await
                            .map_err(map_error)?;
                        Ok(outcome)
                    }
                    Err(err) => {
                        let _ = client
                            .batch_execute(
                                "ROLLBACK TO SAVEPOINT ferro_batch; RELEASE SAVEPOINT ferro_batch",
                            )
                            .await;
                        Err(err)
                    }
                }
            } else {
                in_transaction(connection, &statements, returning).await
            }
        }
        Job::Schema(statements) => {
            if connection.in_transaction {
                return Err(DbError::Operational {
                    message: "schema changes cannot run inside transaction(); run them outside it"
                        .into(),
                });
            }
            in_transaction(connection, &statements, false).await
        }
        Job::Transaction(command) => {
            if command.is_begin() && connection.in_transaction {
                return Err(DbError::Operational {
                    message: "a transaction is already open on this connection".into(),
                });
            }
            match command {
                // Sent later, with the first statement.
                TransactionCommand::Begin | TransactionCommand::BeginIsolated(_) => {
                    connection.in_transaction = true;
                    connection.begin_pending = true;
                    connection.begin_statement = match command {
                        TransactionCommand::BeginIsolated(isolation) => isolation.postgres_begin(),
                        _ => "BEGIN",
                    };
                    return Ok(Outcome::Affected(0));
                }
                // Nothing was sent: there is nothing to commit or roll back.
                TransactionCommand::Commit | TransactionCommand::Rollback
                    if connection.take_begin().is_some() =>
                {
                    connection.in_transaction = false;
                    return Ok(Outcome::Affected(0));
                }
                _ => connection.flush_begin().await?,
            }
            let result = connection
                .client
                .batch_execute(&command.sql("BEGIN"))
                .await
                .map(|()| Outcome::Affected(0))
                .map_err(map_error);
            match command {
                TransactionCommand::Begin | TransactionCommand::BeginIsolated(_) => {
                    connection.in_transaction = result.is_ok();
                }
                TransactionCommand::Commit | TransactionCommand::Rollback => {
                    connection.in_transaction = false;
                }
                _ => {}
            }
            result
        }
        Job::Session(session) => {
            session.abandon();
            Err(DbError::Other {
                message: "a session cannot be started inside another session".into(),
            })
        }
        Job::ListTables => {
            let statement = Statement::new(
                "SELECT tablename::text FROM pg_catalog.pg_tables \
                 WHERE schemaname = current_schema() ORDER BY tablename",
                vec![],
            );
            match query(connection, &statement).await? {
                Outcome::Rows(rows) => Ok(Outcome::Tables(
                    rows.rows
                        .into_iter()
                        .filter_map(|row| match row.into_iter().next() {
                            Some(Value::Text(name)) => Some(name),
                            _ => None,
                        })
                        .collect(),
                )),
                other => Ok(other),
            }
        }
    }
}

/// Runs statements in their own transaction. They are prepared inside it, one by one: later
/// statements may refer to columns created by earlier ones (migrations).
async fn in_transaction(
    connection: &mut Connection,
    statements: &[Statement],
    returning: bool,
) -> JobResult {
    let transaction = connection.client.transaction().await.map_err(map_error)?;
    let outcome = run_statements(&transaction, statements, returning).await?;
    transaction.commit().await.map_err(map_error)?;
    Ok(outcome)
}

async fn run_statements<C: GenericClient + Sync>(
    client: &C,
    statements: &[Statement],
    returning: bool,
) -> JobResult {
    let mut prepared: HashMap<&str, tokio_postgres::Statement> = HashMap::new();
    let mut affected = Vec::with_capacity(statements.len());
    let mut returned = Vec::with_capacity(statements.len());
    for statement in statements {
        let handle = match prepared.get(statement.sql.as_str()) {
            Some(handle) => handle.clone(),
            None => {
                let handle = client.prepare(&statement.sql).await.map_err(map_error)?;
                prepared.insert(statement.sql.as_str(), handle.clone());
                handle
            }
        };
        let values = params(statement);
        if returning {
            let rows = client
                .query(&handle, &refs(&values))
                .await
                .map_err(map_error)?;
            let first = match rows.first() {
                Some(row) => decode_row(row).map_err(decode_error)?.into_iter().next(),
                None => None,
            };
            affected.push(rows.len() as u64);
            returned.push(first);
        } else {
            affected.push(
                client
                    .execute(&handle, &refs(&values))
                    .await
                    .map_err(map_error)?,
            );
            returned.push(None);
        }
    }
    Ok(Outcome::Batch { affected, returned })
}
