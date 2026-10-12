//! Migration engine bindings (Feature 004): operation dicts ↔ engine operations, diff against
//! registered models, and applying/reverting one migration atomically with its history row.

use std::fmt::Display;

use ferro_database::{Job, Outcome, Statement, Value};
use ferro_migrations::{ColumnDef, Operation, SchemaState, compile_all, diff, reverse_all};
use ferro_orm::ForeignKeyRef;
use ferro_orm::compile::Dialect;
use pyo3::exceptions::PyRuntimeError;
use pyo3::prelude::*;
use pyo3::sync::PyOnceLock;
use pyo3::types::{PyDict, PyList, PyType};

use crate::db::{current, submit};
use crate::orm::{kind_from_dict, kind_into_dict, on_delete, optional, registered_metas};
use crate::orm_values::{Mode, db_error, from_value, raw, to_value};

const HISTORY: &str = "ferro_migrations";

static MIGRATION_ERROR: PyOnceLock<Py<PyType>> = PyOnceLock::new();

fn migration_error(py: Python<'_>, message: impl Display) -> PyErr {
    let class = MIGRATION_ERROR.get_or_try_init(py, || -> PyResult<Py<PyType>> {
        Ok(py
            .import("ferro.migrations.errors")?
            .getattr("MigrationError")?
            .cast_into::<PyType>()?
            .unbind())
    });
    match class.and_then(|c| c.bind(py).call1((message.to_string(),))) {
        Ok(exception) => PyErr::from_value(exception),
        Err(err) => err,
    }
}

fn required(py: Python<'_>, dict: &Bound<'_, PyDict>, key: &str, op: &str) -> PyResult<String> {
    optional(dict, key)?
        .ok_or_else(|| migration_error(py, format!("operation '{op}' is missing '{key}'")))
}

fn column_from_dict(py: Python<'_>, table: &str, dict: &Bound<'_, PyDict>) -> PyResult<ColumnDef> {
    let name: String = optional(dict, "name")?
        .ok_or_else(|| migration_error(py, format!("a column of table '{table}' has no name")))?;
    let kind = kind_from_dict(py, &format!("{table}.{name}"), dict)?;
    let mut column = ColumnDef::new(name, kind);
    column.null = optional(dict, "null")?.unwrap_or(false);
    column.unique = optional(dict, "unique")?.unwrap_or(false);
    column.primary_key = optional(dict, "primary_key")?.unwrap_or(false);
    column.auto = optional(dict, "auto")?.unwrap_or(false);
    if column.primary_key {
        column.unique = true;
    }
    if let Some(target) = optional::<String>(dict, "references")? {
        let context = format!("{table}.{}", column.name);
        let (target_table, target_column) = target.split_once('.').ok_or_else(|| {
            migration_error(
                py,
                format!("{context}: references must be 'table.column', got '{target}'"),
            )
        })?;
        let rule: String = optional(dict, "on_delete")?.unwrap_or_else(|| "cascade".into());
        column.references = Some(ForeignKeyRef {
            table: target_table.to_string(),
            column: target_column.to_string(),
            on_delete: on_delete(py, &context, &rule)?,
        });
    }
    Ok(column)
}

fn column_to_dict<'py>(py: Python<'py>, column: &ColumnDef) -> PyResult<Bound<'py, PyDict>> {
    let dict = PyDict::new(py);
    dict.set_item("name", &column.name)?;
    kind_into_dict(&column.kind, &dict)?;
    for (key, value) in [
        ("null", column.null),
        ("unique", column.unique && !column.primary_key),
        ("primary_key", column.primary_key),
        ("auto", column.auto),
    ] {
        if value {
            dict.set_item(key, true)?;
        }
    }
    if let Some(reference) = &column.references {
        dict.set_item(
            "references",
            format!("{}.{}", reference.table, reference.column),
        )?;
        dict.set_item("on_delete", reference.on_delete.name())?;
    }
    Ok(dict)
}

fn default_value(
    table: &str,
    column: &ColumnDef,
    dict: &Bound<'_, PyDict>,
) -> PyResult<Option<Value>> {
    match dict.get_item("default")? {
        Some(value) if !value.is_none() => Ok(Some(to_value(
            table,
            &column.to_field(),
            &value,
            Mode::Store,
        )?)),
        _ => Ok(None),
    }
}

fn op_from_dict(
    py: Python<'_>,
    dict: &Bound<'_, PyDict>,
    state: &SchemaState,
) -> PyResult<Operation> {
    let kind: String = optional(dict, "op")?
        .ok_or_else(|| migration_error(py, "operation without an 'op' key"))?;
    let text = |key: &str| required(py, dict, key, &kind);
    Ok(match kind.as_str() {
        "create_table" => {
            let table = text("table")?;
            let columns = dict
                .get_item("columns")?
                .ok_or_else(|| migration_error(py, "create_table needs 'columns'"))?;
            let columns = columns
                .try_iter()?
                .map(|c| column_from_dict(py, &table, c?.cast::<PyDict>()?))
                .collect::<PyResult<Vec<_>>>()?;
            Operation::CreateTable { table, columns }
        }
        "drop_table" => Operation::DropTable {
            table: text("table")?,
        },
        "rename_table" => Operation::RenameTable {
            old: text("old")?,
            new: text("new")?,
        },
        "add_column" => {
            let table = text("table")?;
            let column = dict
                .get_item("column")?
                .ok_or_else(|| migration_error(py, "add_column needs 'column'"))?;
            let column = column_from_dict(py, &table, column.cast::<PyDict>()?)?;
            let default = default_value(&table, &column, dict)?;
            Operation::AddColumn {
                table,
                column,
                default,
            }
        }
        "remove_column" => {
            let table = text("table")?;
            let column = text("column")?;
            let default = match state.table(&table).and_then(|t| t.column(&column)) {
                Some(definition) => default_value(&table, definition, dict)?,
                None => None,
            };
            Operation::RemoveColumn {
                table,
                column,
                default,
            }
        }
        "alter_column" => {
            let table = text("table")?;
            let column = dict
                .get_item("column")?
                .ok_or_else(|| migration_error(py, "alter_column needs 'column'"))?;
            let column = column_from_dict(py, &table, column.cast::<PyDict>()?)?;
            Operation::AlterColumn { table, column }
        }
        "rename_column" => Operation::RenameColumn {
            table: text("table")?,
            old: text("old")?,
            new: text("new")?,
        },
        "create_index" => Operation::CreateIndex {
            table: text("table")?,
            columns: match optional::<Vec<String>>(dict, "columns")? {
                Some(columns) => columns,
                None => vec![text("column")?],
            },
            name: text("name")?,
            unique: optional(dict, "unique")?.unwrap_or(false),
        },
        "drop_index" => Operation::DropIndex {
            table: text("table")?,
            name: text("name")?,
        },
        other => return Err(migration_error(py, format!("unknown operation '{other}'"))),
    })
}

fn op_to_dict<'py>(
    py: Python<'py>,
    operation: &Operation,
    state: &SchemaState,
) -> PyResult<Bound<'py, PyDict>> {
    let dict = PyDict::new(py);
    let default = |table: &str, column: &ColumnDef, value: &Option<Value>| -> PyResult<()> {
        if let Some(value) = value {
            let _ = table;
            dict.set_item(
                "default",
                from_value(py, &column.to_field(), value.clone())?,
            )?;
        }
        Ok(())
    };
    match operation {
        Operation::CreateTable { table, columns } => {
            dict.set_item("op", "create_table")?;
            dict.set_item("table", table)?;
            let list = PyList::empty(py);
            for column in columns {
                list.append(column_to_dict(py, column)?)?;
            }
            dict.set_item("columns", list)?;
        }
        Operation::DropTable { table } => {
            dict.set_item("op", "drop_table")?;
            dict.set_item("table", table)?;
        }
        Operation::RenameTable { old, new } => {
            dict.set_item("op", "rename_table")?;
            dict.set_item("old", old)?;
            dict.set_item("new", new)?;
        }
        Operation::AddColumn {
            table,
            column,
            default: value,
        } => {
            dict.set_item("op", "add_column")?;
            dict.set_item("table", table)?;
            dict.set_item("column", column_to_dict(py, column)?)?;
            default(table, column, value)?;
        }
        Operation::RemoveColumn {
            table,
            column,
            default: value,
        } => {
            dict.set_item("op", "remove_column")?;
            dict.set_item("table", table)?;
            dict.set_item("column", column)?;
            if let Some(definition) = state.table(table).and_then(|t| t.column(column)) {
                default(table, definition, value)?;
            }
        }
        Operation::AlterColumn { table, column } => {
            dict.set_item("op", "alter_column")?;
            dict.set_item("table", table)?;
            dict.set_item("column", column_to_dict(py, column)?)?;
        }
        Operation::RenameColumn { table, old, new } => {
            dict.set_item("op", "rename_column")?;
            dict.set_item("table", table)?;
            dict.set_item("old", old)?;
            dict.set_item("new", new)?;
        }
        Operation::CreateIndex {
            table,
            columns,
            name,
            unique,
        } => {
            dict.set_item("op", "create_index")?;
            dict.set_item("table", table)?;
            dict.set_item("columns", columns)?;
            dict.set_item("name", name)?;
            if *unique {
                dict.set_item("unique", true)?;
            }
        }
        Operation::DropIndex { table, name } => {
            dict.set_item("op", "drop_index")?;
            dict.set_item("table", table)?;
            dict.set_item("name", name)?;
        }
    }
    Ok(dict)
}

type Migration<'py> = (String, Vec<Bound<'py, PyDict>>);

/// Parses operations of one migration starting from `state`, applying them as it goes.
fn parse(
    py: Python<'_>,
    name: &str,
    dicts: &[Bound<'_, PyDict>],
    state: &mut SchemaState,
) -> PyResult<Vec<Operation>> {
    let mut operations = Vec::with_capacity(dicts.len());
    for dict in dicts {
        let operation = op_from_dict(py, dict, state)
            .map_err(|err| migration_error(py, format!("migration {name}: {}", err.value(py))))?;
        state
            .apply(&operation)
            .map_err(|err| migration_error(py, format!("migration {name}: {err}")))?;
        operations.push(operation);
    }
    Ok(operations)
}

fn replay(py: Python<'_>, migrations: &[Migration<'_>]) -> PyResult<SchemaState> {
    let mut state = SchemaState::default();
    for (name, dicts) in migrations {
        parse(py, name, dicts, &mut state)?;
    }
    Ok(state)
}

/// Operations that bring the replayed schema in line with the registered models.
#[pyfunction]
#[pyo3(signature = (migrations, qualnames=None))]
pub(crate) fn migration_diff<'py>(
    py: Python<'py>,
    migrations: Vec<Migration<'py>>,
    qualnames: Option<Vec<String>>,
) -> PyResult<(Vec<Bound<'py, PyDict>>, Vec<String>)> {
    let state = replay(py, &migrations)?;
    let models = registered_metas(py, qualnames)?;
    let result = diff(&state, &models).map_err(|err| migration_error(py, err))?;
    let mut running = state;
    let mut dicts = Vec::with_capacity(result.operations.len());
    for operation in &result.operations {
        dicts.push(op_to_dict(py, operation, &running)?);
        running
            .apply(operation)
            .map_err(|err| migration_error(py, err))?;
    }
    Ok((dicts, result.warnings))
}

fn statements_for(
    py: Python<'_>,
    dialect: &dyn Dialect,
    previous: &[Migration<'_>],
    migration: &Migration<'_>,
    forward: bool,
) -> PyResult<Vec<Statement>> {
    let base = replay(py, previous)?;
    let mut after = base.clone();
    let operations = parse(py, &migration.0, &migration.1, &mut after)?;
    let fail = |err: ferro_migrations::MigrationError| {
        migration_error(py, format!("migration {}: {err}", migration.0))
    };
    if forward {
        Ok(compile_all(dialect, &base, &operations).map_err(fail)?.0)
    } else {
        let reversed = reverse_all(&base, &operations).map_err(fail)?;
        Ok(compile_all(dialect, &after, &reversed).map_err(fail)?.0)
    }
}

/// The SQL a migration would run, as `[(sql, params)]` (for `--sql` output and tests).
#[pyfunction]
pub(crate) fn migration_sql<'py>(
    py: Python<'py>,
    previous: Vec<Migration<'py>>,
    migration: Migration<'py>,
    forward: bool,
) -> PyResult<Vec<(String, Vec<Py<PyAny>>)>> {
    let statements = statements_for(py, crate::db::dialect(), &previous, &migration, forward)?;
    statements
        .into_iter()
        .map(|s| {
            let params = s
                .params
                .into_iter()
                .map(|v| raw(py, v).map(Bound::unbind))
                .collect::<PyResult<Vec<_>>>()?;
            Ok((s.sql, params))
        })
        .collect()
}

/// Applies (or reverts) one migration and its history row in a single transaction.
#[pyfunction]
pub(crate) fn apply_migration<'py>(
    py: Python<'py>,
    previous: Vec<Migration<'py>>,
    migration: Migration<'py>,
    forward: bool,
    applied_at: String,
) -> PyResult<Bound<'py, PyAny>> {
    let database = current(py)?;
    let dialect = database.dialect;
    let mut statements = statements_for(py, dialect, &previous, &migration, forward)?;
    let table = dialect.quote(HISTORY);
    let name = migration.0.clone();
    statements.push(if forward {
        Statement::new(
            format!(
                "INSERT INTO {table} (\"name\", \"applied_at\") VALUES ({}, {})",
                dialect.placeholder(1),
                dialect.placeholder(2)
            ),
            vec![Value::Text(name), Value::Text(applied_at)],
        )
    } else {
        Statement::new(
            format!(
                "DELETE FROM {table} WHERE \"name\" = {}",
                dialect.placeholder(1)
            ),
            vec![Value::Text(name)],
        )
    });
    submit(
        py,
        &database,
        Job::Schema(statements),
        Box::new(|py, result| match result {
            Ok(_) => Ok(py.None()),
            Err(err) => Err(db_error(py, err, None)),
        }),
    )
}

/// Creates the history table if it does not exist.
#[pyfunction]
pub(crate) fn ensure_migration_table(py: Python<'_>) -> PyResult<Bound<'_, PyAny>> {
    let database = current(py)?;
    let dialect = database.dialect;
    let statement = Statement::new(
        format!(
            "CREATE TABLE IF NOT EXISTS {} (\"id\" {}, \"name\" varchar(255) NOT NULL UNIQUE, \
             \"applied_at\" varchar(32) NOT NULL)",
            dialect.quote(HISTORY),
            dialect.auto_primary_key()
        ),
        vec![],
    );
    submit(
        py,
        &database,
        Job::Execute(statement),
        Box::new(|py, result| match result {
            Ok(_) => Ok(py.None()),
            Err(err) => Err(db_error(py, err, None)),
        }),
    )
}

/// Applied migration names in the order they were applied.
#[pyfunction]
pub(crate) fn migration_history(py: Python<'_>) -> PyResult<Bound<'_, PyAny>> {
    let database = current(py)?;
    let statement = Statement::new(
        format!(
            "SELECT \"name\" FROM {} ORDER BY \"id\"",
            database.dialect.quote(HISTORY)
        ),
        vec![],
    );
    submit(
        py,
        &database,
        Job::Query(statement),
        Box::new(|py, result| match result {
            Ok(Outcome::Rows(rows)) => {
                let names: Vec<String> = rows
                    .rows
                    .into_iter()
                    .filter_map(|row| match row.into_iter().next() {
                        Some(Value::Text(name)) => Some(name),
                        _ => None,
                    })
                    .collect();
                Ok(names.into_pyobject(py)?.into_any().unbind())
            }
            Ok(other) => Err(PyRuntimeError::new_err(format!(
                "unexpected database outcome {other:?}"
            ))),
            Err(err) => Err(db_error(py, err, None)),
        }),
    )
}
