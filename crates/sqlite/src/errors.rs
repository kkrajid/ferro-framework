//! Driver errors → [`DbError`]. Messages are SQLite's own text, never the driver's type names
//! (FR-024).

use ferro_database::{DbError, IntegrityKind};
use rusqlite::ErrorCode;

use crate::SqlitePath;

pub(crate) fn map_error(err: rusqlite::Error) -> DbError {
    match err {
        rusqlite::Error::SqliteFailure(code, message) => {
            let message = message.unwrap_or_else(|| code.to_string());
            if code.code == ErrorCode::ConstraintViolation {
                integrity(message)
            } else if matches!(
                code.code,
                ErrorCode::DatabaseBusy | ErrorCode::DatabaseLocked
            ) {
                // The busy timeout expired while another connection held the lock.
                DbError::Unavailable { message }
            } else if message.starts_with("no such table") {
                DbError::MissingTable { message }
            } else {
                DbError::Operational { message }
            }
        }
        other => DbError::Other {
            message: other.to_string(),
        },
    }
}

pub(crate) fn open_error(path: &SqlitePath, err: &rusqlite::Error) -> DbError {
    let target = match path {
        SqlitePath::Memory => ":memory:".to_string(),
        SqlitePath::File(path) => path.display().to_string(),
    };
    let detail = match err {
        rusqlite::Error::SqliteFailure(code, _) => code.to_string(),
        other => other.to_string(),
    };
    DbError::Operational {
        message: format!("unable to open database file '{target}': {detail}"),
    }
}

/// Parses `UNIQUE constraint failed: product.sku, product.name` and similar messages.
fn integrity(message: String) -> DbError {
    let kind = if message.starts_with("UNIQUE") {
        IntegrityKind::Unique
    } else if message.starts_with("NOT NULL") {
        IntegrityKind::NotNull
    } else if message.starts_with("FOREIGN KEY") {
        IntegrityKind::ForeignKey
    } else if message.starts_with("CHECK") {
        IntegrityKind::Check
    } else {
        IntegrityKind::Other
    };
    let mut table = None;
    let mut columns = Vec::new();
    if matches!(kind, IntegrityKind::Unique | IntegrityKind::NotNull)
        && let Some((_, targets)) = message.split_once(": ")
    {
        for target in targets.split(", ") {
            if let Some((t, column)) = target.trim().split_once('.') {
                table.get_or_insert_with(|| t.to_string());
                columns.push(column.to_string());
            }
        }
    }
    DbError::Integrity {
        kind,
        table,
        columns,
        message,
    }
}
