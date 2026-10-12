//! Models vs replayed state → operations (research R3; Feature 005 R9 for foreign keys).

use std::collections::BTreeSet;
use std::sync::Arc;

use ferro_orm::ModelMeta;
use ferro_orm::ddl::{creation_order, index_name};

use crate::ops::{ColumnDef, Operation};
use crate::state::{IndexDef, SchemaState};
use crate::{MigrationError, error};

#[derive(Debug, Clone, Default, PartialEq)]
pub struct Diff {
    pub operations: Vec<Operation>,
    pub warnings: Vec<String>,
}

fn desired_indexes(meta: &ModelMeta) -> Vec<IndexDef> {
    let mut indexes: Vec<IndexDef> = meta
        .fields
        .iter()
        .filter(|f| f.index && !f.unique)
        .map(|f| IndexDef::single(index_name(&meta.table, &f.column), f.column.clone()))
        .collect();
    indexes.extend(meta.indexes.iter().map(|i| IndexDef {
        name: i.name.clone(),
        columns: i.columns.clone(),
        unique: i.unique,
    }));
    indexes
}

fn create_index(table: &str, index: IndexDef) -> Operation {
    Operation::CreateIndex {
        table: table.to_string(),
        columns: index.columns,
        name: index.name,
        unique: index.unique,
    }
}

/// Tables to drop, referencing tables first.
fn drop_order(state: &SchemaState, tables: Vec<String>) -> Vec<String> {
    let mut remaining = tables;
    let mut ordered = Vec::with_capacity(remaining.len());
    while !remaining.is_empty() {
        // A table can go once no other remaining table references it.
        let position = remaining
            .iter()
            .position(|candidate| {
                !remaining.iter().any(|other| {
                    other != candidate
                        && state.table(other).is_some_and(|t| {
                            t.columns.iter().any(|c| {
                                c.references.as_ref().is_some_and(|r| r.table == *candidate)
                            })
                        })
                })
            })
            .unwrap_or(0); // a cycle among dropped tables: keep the given order
        ordered.push(remaining.remove(position));
    }
    ordered
}

pub fn diff(state: &SchemaState, models: &[Arc<ModelMeta>]) -> Result<Diff, MigrationError> {
    let mut result = Diff::default();
    let new_models: Vec<Arc<ModelMeta>> = models
        .iter()
        .filter(|m| state.table(&m.table).is_none())
        .cloned()
        .collect();
    let new_models = creation_order(&new_models).map_err(|e| {
        error(format!(
            "{e} (create the models in two migrations: first without one of the keys)"
        ))
    })?;
    for meta in &new_models {
        result.operations.push(Operation::CreateTable {
            table: meta.table.clone(),
            columns: meta.fields.iter().map(ColumnDef::from_field).collect(),
        });
        for index in desired_indexes(meta) {
            result.operations.push(create_index(&meta.table, index));
        }
    }

    let mut changes = Vec::new();
    for meta in models {
        let table = &meta.table;
        let Some(current) = state.table(table) else {
            continue;
        };
        let columns: Vec<ColumnDef> = meta.fields.iter().map(ColumnDef::from_field).collect();
        let indexes = desired_indexes(meta);

        let current_pk = current.columns.iter().find(|c| c.primary_key);
        let desired_pk = columns.iter().find(|c| c.primary_key);
        if current_pk != desired_pk {
            return Err(error(format!(
                "{}: changing the primary key of table '{table}' is not supported by migrations yet",
                meta.label
            )));
        }

        let removed: Vec<&ColumnDef> = current
            .columns
            .iter()
            .filter(|c| !columns.iter().any(|d| d.name == c.name))
            .collect();
        let added: Vec<&ColumnDef> = columns
            .iter()
            .filter(|d| current.column(&d.name).is_none())
            .collect();
        let altered: Vec<&ColumnDef> = columns
            .iter()
            .filter(|d| current.column(&d.name).is_some_and(|c| c != *d))
            .collect();

        for index in current.indexes.iter().filter(|i| !indexes.contains(i)) {
            changes.push(Operation::DropIndex {
                table: table.clone(),
                name: index.name.clone(),
            });
        }
        for column in &removed {
            changes.push(Operation::RemoveColumn {
                table: table.clone(),
                column: column.name.clone(),
                default: None,
            });
        }
        for column in &added {
            changes.push(Operation::AddColumn {
                table: table.clone(),
                column: (*column).clone(),
                default: None,
            });
        }
        for column in &altered {
            changes.push(Operation::AlterColumn {
                table: table.clone(),
                column: (*column).clone(),
            });
        }
        for index in indexes.into_iter().filter(|i| !current.indexes.contains(i)) {
            changes.push(create_index(table, index));
        }
        if let ([old], [new]) = (removed.as_slice(), added.as_slice())
            && old.kind == new.kind
        {
            result.warnings.push(format!(
                "table '{table}': column '{}' was removed and '{}' added; if this is a rename, \
                 replace both operations with ops.RenameColumn(\"{table}\", \"{}\", \"{}\") to keep \
                 the data",
                old.name, new.name, old.name, new.name
            ));
        }
    }
    result.operations.extend(changes);

    let modelled: BTreeSet<&str> = models.iter().map(|m| m.table.as_str()).collect();
    let dropped: Vec<String> = state
        .tables
        .keys()
        .filter(|t| !modelled.contains(t.as_str()))
        .cloned()
        .collect();
    for table in drop_order(state, dropped) {
        result.warnings.push(format!(
            "table '{table}' will be dropped (its model no longer exists); if it was renamed, use \
             ops.RenameTable instead to keep the data"
        ));
        result.operations.push(Operation::DropTable { table });
    }
    Ok(result)
}
