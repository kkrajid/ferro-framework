//! Driver errors → [`DbError`] (research R8). Messages carry the server's text, never the driver's
//! names or the connection password.

use ferro_database::{DbError, IntegrityKind};
use tokio_postgres::error::SqlState;

use crate::config::PgConfig;

pub(crate) fn connect_error(config: &PgConfig, err: &tokio_postgres::Error) -> DbError {
    let reason = match err.as_db_error() {
        Some(db) => db.message().to_string(),
        None => err.to_string(),
    };
    DbError::Unavailable {
        message: format!(
            "could not connect to PostgreSQL at {}: {reason}",
            config.describe()
        ),
    }
}

/// `Key (a, b)=(x, y) already exists.` → `["a", "b"]`.
fn key_columns(detail: Option<&str>) -> Vec<String> {
    detail
        .and_then(|d| d.strip_prefix("Key ("))
        .and_then(|d| d.split_once(")="))
        .map(|(columns, _)| {
            columns
                .split(", ")
                .map(|c| c.trim_matches('"').to_string())
                .collect()
        })
        .unwrap_or_default()
}

/// Server states that mean "cannot serve now, try again" rather than a bad statement:
/// connection exceptions (class 08), too many connections, shutting down / starting up, and a
/// lock that is not available.
fn unavailable(code: &str) -> bool {
    code.starts_with("08") || matches!(code, "53300" | "57P01" | "57P02" | "57P03" | "55P03")
}

pub(crate) fn map_error(err: tokio_postgres::Error) -> DbError {
    let Some(db) = err.as_db_error() else {
        return DbError::Unavailable {
            message: if err.is_closed() {
                "connection to the database was lost".to_string()
            } else {
                format!("database communication error: {err}")
            },
        };
    };
    let message = db.message().to_string();
    let table = db.table().map(str::to_string);
    let integrity = |kind, columns| DbError::Integrity {
        kind,
        table: table.clone(),
        columns,
        message: message.clone(),
    };
    match db.code() {
        code if *code == SqlState::UNIQUE_VIOLATION => {
            integrity(IntegrityKind::Unique, key_columns(db.detail()))
        }
        code if *code == SqlState::NOT_NULL_VIOLATION => integrity(
            IntegrityKind::NotNull,
            db.column().map(|c| vec![c.to_string()]).unwrap_or_default(),
        ),
        code if *code == SqlState::FOREIGN_KEY_VIOLATION
            || *code == SqlState::RESTRICT_VIOLATION =>
        {
            integrity(IntegrityKind::ForeignKey, key_columns(db.detail()))
        }
        code if *code == SqlState::CHECK_VIOLATION => integrity(IntegrityKind::Check, Vec::new()),
        code if *code == SqlState::UNDEFINED_TABLE => DbError::MissingTable { message },
        code if unavailable(code.code()) => DbError::Unavailable { message },
        _ => DbError::Operational { message },
    }
}
