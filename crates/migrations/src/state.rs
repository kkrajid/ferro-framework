use std::collections::BTreeMap;

use ferro_orm::ddl::index_name;

use crate::ops::{ColumnDef, Operation};
use crate::{MigrationError, error};

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct IndexDef {
    pub name: String,
    pub columns: Vec<String>,
    pub unique: bool,
}

impl IndexDef {
    /// A plain single-column index.
    pub fn single(name: impl Into<String>, column: impl Into<String>) -> Self {
        Self {
            name: name.into(),
            columns: vec![column.into()],
            unique: false,
        }
    }

    /// The name Ferro gives this index on `table` (`<t>_<c>_idx`, `<t>_<a>_<b>_uniq`).
    pub fn default_name(table: &str, columns: &[String], unique: bool) -> String {
        if unique {
            format!("{table}_{}_uniq", columns.join("_"))
        } else {
            index_name(table, &columns.join("_"))
        }
    }

    pub fn has_default_name(&self, table: &str) -> bool {
        self.name == Self::default_name(table, &self.columns, self.unique)
    }
}

#[derive(Debug, Clone, Default, PartialEq)]
pub struct TableState {
    pub columns: Vec<ColumnDef>,
    pub indexes: Vec<IndexDef>,
}

impl TableState {
    pub fn column(&self, name: &str) -> Option<&ColumnDef> {
        self.columns.iter().find(|c| c.name == name)
    }

    fn column_position(&self, table: &str, name: &str) -> Result<usize, MigrationError> {
        self.columns
            .iter()
            .position(|c| c.name == name)
            .ok_or_else(|| error(format!("column '{name}' does not exist in table '{table}'")))
    }
}

/// The schema implied by a sequence of operations (research: replay, no snapshot file).
#[derive(Debug, Clone, Default, PartialEq)]
pub struct SchemaState {
    pub tables: BTreeMap<String, TableState>,
}

impl SchemaState {
    pub fn replay<'a>(
        operations: impl IntoIterator<Item = &'a Operation>,
    ) -> Result<Self, MigrationError> {
        let mut state = Self::default();
        for operation in operations {
            state.apply(operation)?;
        }
        Ok(state)
    }

    pub fn table(&self, name: &str) -> Option<&TableState> {
        self.tables.get(name)
    }

    pub(crate) fn existing(&self, name: &str) -> Result<&TableState, MigrationError> {
        self.tables
            .get(name)
            .ok_or_else(|| error(format!("table '{name}' does not exist")))
    }

    fn existing_mut(&mut self, name: &str) -> Result<&mut TableState, MigrationError> {
        self.tables
            .get_mut(name)
            .ok_or_else(|| error(format!("table '{name}' does not exist")))
    }

    fn index_exists(&self, name: &str) -> bool {
        self.tables
            .values()
            .any(|t| t.indexes.iter().any(|i| i.name == name))
    }

    /// Applies one operation, validating it against the current state.
    pub fn apply(&mut self, operation: &Operation) -> Result<(), MigrationError> {
        match operation {
            Operation::CreateTable { table, columns } => {
                if self.tables.contains_key(table) {
                    return Err(error(format!("table '{table}' already exists")));
                }
                self.tables.insert(
                    table.clone(),
                    TableState {
                        columns: columns.clone(),
                        indexes: Vec::new(),
                    },
                );
            }
            Operation::DropTable { table } => {
                self.existing(table)?;
                self.tables.remove(table);
            }
            Operation::RenameTable { old, new } => {
                if self.tables.contains_key(new) {
                    return Err(error(format!("table '{new}' already exists")));
                }
                let mut state = self.existing(old)?.clone();
                // Indexes keep following Ferro's naming, so later operations find them.
                for index in &mut state.indexes {
                    if index.has_default_name(old) {
                        index.name = IndexDef::default_name(new, &index.columns, index.unique);
                    }
                }
                self.tables.remove(old);
                self.tables.insert(new.clone(), state);
                for table in self.tables.values_mut() {
                    for column in &mut table.columns {
                        if let Some(reference) = &mut column.references
                            && reference.table == *old
                        {
                            reference.table = new.clone();
                        }
                    }
                }
            }
            Operation::AddColumn { table, column, .. } => {
                let state = self.existing_mut(table)?;
                if state.column(&column.name).is_some() {
                    return Err(error(format!(
                        "column '{}' already exists in table '{table}'",
                        column.name
                    )));
                }
                state.columns.push(column.clone());
            }
            Operation::RemoveColumn { table, column, .. } => {
                let state = self.existing_mut(table)?;
                let position = state.column_position(table, column)?;
                state.columns.remove(position);
                state.indexes.retain(|i| !i.columns.contains(column));
            }
            Operation::AlterColumn { table, column } => {
                let state = self.existing_mut(table)?;
                let position = state.column_position(table, &column.name)?;
                state.columns[position] = column.clone();
            }
            Operation::RenameColumn { table, old, new } => {
                let state = self.existing_mut(table)?;
                if state.column(new).is_some() {
                    return Err(error(format!(
                        "column '{new}' already exists in table '{table}'"
                    )));
                }
                let position = state.column_position(table, old)?;
                state.columns[position].name = new.clone();
                for index in state.indexes.iter_mut().filter(|i| i.columns.contains(old)) {
                    let default = index.has_default_name(table);
                    for column in &mut index.columns {
                        if column == old {
                            *column = new.clone();
                        }
                    }
                    if default {
                        index.name = IndexDef::default_name(table, &index.columns, index.unique);
                    }
                }
                for other in self.tables.values_mut() {
                    for column in &mut other.columns {
                        if let Some(reference) = &mut column.references
                            && reference.table == *table
                            && reference.column == *old
                        {
                            reference.column = new.clone();
                        }
                    }
                }
            }
            Operation::CreateIndex {
                table,
                columns,
                name,
                unique,
            } => {
                if self.index_exists(name) {
                    return Err(error(format!("index '{name}' already exists")));
                }
                if columns.is_empty() {
                    return Err(error(format!("index '{name}' has no columns")));
                }
                let state = self.existing_mut(table)?;
                for column in columns {
                    state.column_position(table, column)?;
                }
                state.indexes.push(IndexDef {
                    name: name.clone(),
                    columns: columns.clone(),
                    unique: *unique,
                });
            }
            Operation::DropIndex { table, name } => {
                let state = self.existing_mut(table)?;
                let before = state.indexes.len();
                state.indexes.retain(|i| i.name != *name);
                if state.indexes.len() == before {
                    return Err(error(format!(
                        "index '{name}' does not exist on table '{table}'"
                    )));
                }
            }
        }
        Ok(())
    }
}
