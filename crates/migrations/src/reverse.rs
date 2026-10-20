//! Reverse operations computed from the schema before each operation (research R5).

use crate::ops::Operation;
use crate::state::SchemaState;
use crate::{MigrationError, error};

pub fn reverse(
    operation: &Operation,
    before: &SchemaState,
) -> Result<Vec<Operation>, MigrationError> {
    Ok(match operation {
        Operation::CreateTable { table, .. } => vec![Operation::DropTable {
            table: table.clone(),
        }],
        Operation::DropTable { table } => {
            let state = before.existing(table)?;
            let mut ops = vec![Operation::CreateTable {
                table: table.clone(),
                columns: state.columns.clone(),
            }];
            ops.extend(state.indexes.iter().map(|i| Operation::CreateIndex {
                table: table.clone(),
                columns: i.columns.clone(),
                name: i.name.clone(),
                unique: i.unique,
            }));
            ops
        }
        Operation::RenameTable { old, new } => vec![Operation::RenameTable {
            old: new.clone(),
            new: old.clone(),
        }],
        Operation::AddColumn {
            table,
            column,
            default,
        } => vec![Operation::RemoveColumn {
            table: table.clone(),
            column: column.name.clone(),
            default: default.clone(),
        }],
        Operation::RemoveColumn {
            table,
            column,
            default,
        } => {
            let state = before.existing(table)?;
            let definition = state.column(column).ok_or_else(|| {
                error(format!(
                    "column '{column}' does not exist in table '{table}'"
                ))
            })?;
            if !definition.null && !definition.auto && default.is_none() {
                return Err(error(format!(
                    "removing column '{column}' from '{table}' cannot be reversed: it is non-null \
                     and the operation has no default to restore existing rows with \
                     (add default=... to ops.RemoveColumn)"
                )));
            }
            let mut ops = vec![Operation::AddColumn {
                table: table.clone(),
                column: definition.clone(),
                default: default.clone(),
            }];
            ops.extend(
                state
                    .indexes
                    .iter()
                    .filter(|i| i.columns.contains(column))
                    .map(|i| Operation::CreateIndex {
                        table: table.clone(),
                        columns: i.columns.clone(),
                        name: i.name.clone(),
                        unique: i.unique,
                    }),
            );
            ops
        }
        Operation::AlterColumn { table, column } => {
            let state = before.existing(table)?;
            let old = state.column(&column.name).ok_or_else(|| {
                error(format!(
                    "column '{}' does not exist in table '{table}'",
                    column.name
                ))
            })?;
            vec![Operation::AlterColumn {
                table: table.clone(),
                column: old.clone(),
            }]
        }
        Operation::RenameColumn { table, old, new } => vec![Operation::RenameColumn {
            table: table.clone(),
            old: new.clone(),
            new: old.clone(),
        }],
        Operation::CreateIndex { table, name, .. } => vec![Operation::DropIndex {
            table: table.clone(),
            name: name.clone(),
        }],
        Operation::DropIndex { table, name } => {
            let state = before.existing(table)?;
            let index = state
                .indexes
                .iter()
                .find(|i| i.name == *name)
                .ok_or_else(|| error(format!("index '{name}' does not exist")))?;
            vec![Operation::CreateIndex {
                table: table.clone(),
                columns: index.columns.clone(),
                name: name.clone(),
                unique: index.unique,
            }]
        }
        Operation::RunSql { sql, reverse_sql } => match reverse_sql {
            Some(reverse_sql) => vec![Operation::RunSql {
                sql: reverse_sql.clone(),
                reverse_sql: Some(sql.clone()),
            }],
            None => {
                return Err(error(
                    "a RunSQL operation without reverse_sql cannot be reversed (give \
                     reverse_sql=..., or reverse_sql=[] when there is nothing to undo)",
                ));
            }
        },
        Operation::RunPython { reversible } => {
            if !reversible {
                return Err(error(
                    "a RunPython operation without a backward function cannot be reversed",
                ));
            }
            vec![operation.clone()]
        }
        Operation::CreateExtension { name } => {
            vec![Operation::DropExtension { name: name.clone() }]
        }
        Operation::DropExtension { name } => {
            vec![Operation::CreateExtension { name: name.clone() }]
        }
    })
}

/// Reverses a whole migration: every reverse is computed first (so an irreversible operation
/// fails before anything runs), then returned in reverse order.
pub fn reverse_all(
    base: &SchemaState,
    operations: &[Operation],
) -> Result<Vec<Operation>, MigrationError> {
    let mut state = base.clone();
    let mut reversed = Vec::with_capacity(operations.len());
    for operation in operations {
        reversed.push(reverse(operation, &state)?);
        state.apply(operation)?;
    }
    Ok(reversed.into_iter().rev().flatten().collect())
}
