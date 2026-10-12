//! Ferro's migration engine (Feature 004): operations, schema state replay, diffing against model
//! metadata, reversal and per-dialect SQL. Pure Rust; the Python layer only loads and writes
//! migration files.

#![forbid(unsafe_code)]

mod diff;
mod ops;
mod reverse;
mod sql;
mod state;

use std::fmt;

pub use diff::{Diff, diff};
pub use ops::{ColumnDef, Operation};
pub use reverse::{reverse, reverse_all};
pub use sql::{compile, compile_all};
pub use state::{IndexDef, SchemaState, TableState};

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct MigrationError(pub String);

impl fmt::Display for MigrationError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.0)
    }
}

impl std::error::Error for MigrationError {}

pub(crate) fn error(message: impl Into<String>) -> MigrationError {
    MigrationError(message.into())
}
