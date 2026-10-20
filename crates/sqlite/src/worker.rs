use std::sync::atomic::Ordering;

use crossbeam_channel::Receiver;
use ferro_database::{
    DbError, IntegrityKind, Job, JobResult, Outcome, Rows, SessionReceiver, Statement, Value, skip,
};
use rusqlite::types::{ToSql, ToSqlOutput, ValueRef};
use rusqlite::{Connection, TransactionBehavior, params_from_iter};

use crate::errors::{map_error, open_error};
use crate::{SqliteConfig, SqlitePath, Task};

const STATEMENT_CACHE: usize = 256;

pub(crate) fn open_connection(config: &SqliteConfig) -> Result<Connection, DbError> {
    let connection = match &config.path {
        SqlitePath::Memory => Connection::open_in_memory(),
        SqlitePath::File(path) => Connection::open(path),
    }
    .map_err(|err| open_error(&config.path, &err))?;
    connection
        .busy_timeout(config.busy_timeout)
        .map_err(map_error)?;
    connection
        .pragma_update(None, "foreign_keys", "ON")
        .map_err(map_error)?;
    if let SqlitePath::File(_) = config.path {
        connection
            .query_row("PRAGMA journal_mode = WAL", [], |row| {
                row.get::<_, String>(0)
            })
            .map_err(map_error)?;
        connection
            .pragma_update(None, "synchronous", "NORMAL")
            .map_err(map_error)?;
    }
    connection.set_prepared_statement_cache_capacity(STATEMENT_CACHE);
    Ok(connection)
}

pub(crate) fn run(mut connection: Connection, receiver: Receiver<Task>) {
    for task in receiver.iter() {
        if task.cancelled.load(Ordering::Acquire) {
            skip(&task.job);
            continue;
        }
        let result = match task.job {
            Job::Session(session) => run_session(&mut connection, &session),
            job => execute(&mut connection, job),
        };
        (task.done)(result);
    }
}

/// Runs a session's jobs on this connection until the session ends, then rolls back a
/// transaction left open (dropped or cancelled `transaction()` blocks).
fn run_session(connection: &mut Connection, session: &SessionReceiver) -> JobResult {
    while let Some(task) = session.recv_blocking() {
        if task.cancelled.load(Ordering::Acquire) {
            skip(&task.job);
            continue;
        }
        let result = execute(connection, task.job);
        (task.done)(result);
    }
    session.abandon();
    if !connection.is_autocommit() {
        connection.execute_batch("ROLLBACK").map_err(map_error)?;
    }
    Ok(Outcome::Affected(0))
}

struct Param<'a>(&'a Value);

impl ToSql for Param<'_> {
    fn to_sql(&self) -> rusqlite::Result<ToSqlOutput<'_>> {
        Ok(match self.0 {
            Value::Null => ToSqlOutput::Borrowed(ValueRef::Null),
            Value::Integer(v) => ToSqlOutput::Borrowed(ValueRef::Integer(*v)),
            Value::Real(v) => ToSqlOutput::Borrowed(ValueRef::Real(*v)),
            // SQLite has no exact decimal type; NUMERIC-affinity columns hold REAL values.
            Value::Decimal(v) => match v.parse::<f64>() {
                Ok(number) => ToSqlOutput::Borrowed(ValueRef::Real(number)),
                Err(_) => ToSqlOutput::Borrowed(ValueRef::Text(v.as_bytes())),
            },
            Value::Text(v) => ToSqlOutput::Borrowed(ValueRef::Text(v.as_bytes())),
            Value::Blob(v) => ToSqlOutput::Borrowed(ValueRef::Blob(v)),
        })
    }
}

fn params(statement: &Statement) -> impl Iterator<Item = Param<'_>> {
    statement.params.iter().map(Param)
}

fn value(cell: ValueRef<'_>) -> Value {
    match cell {
        ValueRef::Null => Value::Null,
        ValueRef::Integer(v) => Value::Integer(v),
        ValueRef::Real(v) => Value::Real(v),
        ValueRef::Text(v) => Value::Text(String::from_utf8_lossy(v).into_owned()),
        ValueRef::Blob(v) => Value::Blob(v.to_vec()),
    }
}

fn query(connection: &Connection, statement: &Statement) -> rusqlite::Result<Rows> {
    let mut prepared = connection.prepare_cached(&statement.sql)?;
    let columns: Vec<String> = prepared
        .column_names()
        .into_iter()
        .map(str::to_string)
        .collect();
    let width = columns.len();
    let mut cursor = prepared.query(params_from_iter(params(statement)))?;
    let mut rows = Vec::new();
    while let Some(row) = cursor.next()? {
        let mut cells = Vec::with_capacity(width);
        for i in 0..width {
            cells.push(value(row.get_ref(i)?));
        }
        rows.push(cells);
    }
    Ok(Rows { columns, rows })
}

fn execute(connection: &mut Connection, job: Job) -> JobResult {
    match job {
        Job::Query(statement) => query(connection, &statement)
            .map(Outcome::Rows)
            .map_err(map_error),
        Job::Execute(statement) => connection
            .prepare_cached(&statement.sql)
            .and_then(|mut prepared| prepared.execute(params_from_iter(params(&statement))))
            .map(|n| Outcome::Affected(n as u64))
            .map_err(map_error),
        Job::Batch {
            statements,
            returning,
        } => {
            let result = if connection.is_autocommit() {
                connection
                    .transaction_with_behavior(TransactionBehavior::Immediate)
                    .and_then(|transaction| {
                        let outcome = run_batch(&transaction, &statements, returning)?;
                        transaction.commit()?;
                        Ok(outcome)
                    })
            } else {
                // Inside a session's transaction: a savepoint keeps the batch atomic.
                connection.savepoint().and_then(|savepoint| {
                    let outcome = run_batch(&savepoint, &statements, returning)?;
                    savepoint.commit()?;
                    Ok(outcome)
                })
            };
            result.map_err(map_error)
        }
        Job::Schema(statements) => run_schema(connection, &statements),
        Job::Transaction(command) => {
            if command.is_begin() && !connection.is_autocommit() {
                return Err(DbError::Operational {
                    message: "a transaction is already open on this connection".into(),
                });
            }
            connection
                .execute_batch(&command.sql("BEGIN IMMEDIATE"))
                .map(|()| Outcome::Affected(0))
                .map_err(map_error)
        }
        Job::Session(session) => {
            session.abandon();
            Err(DbError::Other {
                message: "a session cannot be started inside another session".into(),
            })
        }
        Job::ListTables => query(
            connection,
            &Statement::new(
                "SELECT name FROM sqlite_master WHERE type = 'table' \
                 AND name NOT LIKE 'sqlite_%' ORDER BY name",
                vec![],
            ),
        )
        .map(|rows| {
            Outcome::Tables(
                rows.rows
                    .into_iter()
                    .filter_map(|row| match row.into_iter().next() {
                        Some(Value::Text(name)) => Some(name),
                        _ => None,
                    })
                    .collect(),
            )
        })
        .map_err(map_error),
    }
}

fn run_batch(
    connection: &Connection,
    statements: &[Statement],
    returning: bool,
) -> rusqlite::Result<Outcome> {
    let mut affected = Vec::with_capacity(statements.len());
    let mut returned = Vec::with_capacity(statements.len());
    for statement in statements {
        let mut prepared = connection.prepare_cached(&statement.sql)?;
        if returning {
            let mut rows = prepared.query(params_from_iter(params(statement)))?;
            let first = match rows.next()? {
                Some(row) => Some(value(row.get_ref(0)?)),
                None => None,
            };
            while rows.next()?.is_some() {}
            drop(rows);
            affected.push(connection.changes());
            returned.push(first);
        } else {
            affected.push(prepared.execute(params_from_iter(params(statement)))? as u64);
            returned.push(None);
        }
    }
    Ok(Outcome::Batch { affected, returned })
}

/// SQLite's documented procedure for schema changes that rebuild tables: foreign key enforcement
/// is switched off outside the transaction (so dropping a referenced table neither cascades nor
/// fails) and integrity is verified before committing.
fn run_schema(connection: &mut Connection, statements: &[Statement]) -> JobResult {
    if !connection.is_autocommit() {
        return Err(DbError::Operational {
            message: "schema changes cannot run inside transaction(); run them outside it".into(),
        });
    }
    connection
        .pragma_update(None, "foreign_keys", "OFF")
        .map_err(map_error)?;
    let result = schema_transaction(connection, statements);
    let restored = connection
        .pragma_update(None, "foreign_keys", "ON")
        .map_err(map_error);
    let outcome = result?;
    restored?;
    Ok(outcome)
}

fn schema_transaction(connection: &mut Connection, statements: &[Statement]) -> JobResult {
    let transaction = connection
        .transaction_with_behavior(TransactionBehavior::Immediate)
        .map_err(map_error)?;
    let mut affected = Vec::with_capacity(statements.len());
    for statement in statements {
        let count = transaction
            .prepare(&statement.sql)
            .and_then(|mut prepared| prepared.execute(params_from_iter(params(statement))))
            .map_err(map_error)?;
        affected.push(count as u64);
    }
    let violations = query(
        &transaction,
        &Statement::new("PRAGMA foreign_key_check", vec![]),
    )
    .map_err(map_error)?;
    if let Some(row) = violations.rows.first() {
        let text = |index: usize| match row.get(index) {
            Some(Value::Text(name)) => name.clone(),
            _ => "?".to_string(),
        };
        let (table, parent) = (text(0), text(2));
        return Err(DbError::Integrity {
            kind: IntegrityKind::ForeignKey,
            table: Some(table.clone()),
            columns: Vec::new(),
            message: format!(
                "FOREIGN KEY check failed: rows of '{table}' reference missing rows of '{parent}'"
            ),
        });
    }
    transaction.commit().map_err(map_error)?;
    Ok(Outcome::Batch {
        returned: vec![None; affected.len()],
        affected,
    })
}
