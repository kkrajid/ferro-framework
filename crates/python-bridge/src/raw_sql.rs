//! Hand-written SQL with bound parameters (Feature 022): `db.execute()`, `db.fetch()` and
//! `Model.objects.raw()`. SQL text uses `%s` for each parameter (`%%` for a literal `%`), which
//! becomes the connected database's placeholder; values are never written into the text.

use std::collections::HashMap;

use ferro_database::{Job, Outcome, Statement, Value};
use ferro_orm::{FieldKind, FieldMeta};
use pyo3::exceptions::PyRuntimeError;
use pyo3::prelude::*;
use pyo3::types::{PyBool, PyBytes, PyDict, PyFloat, PyInt, PyList, PyString};

use crate::db::{current, submit};
use crate::orm::{PyModelMeta, instance_dict};
use crate::orm_values::{Mode, db_error, field_error, from_value, orm_types, raw, to_value};

/// `%s` → placeholders; `%%` → `%`. Fails when the count does not match `params`.
fn placeholders(
    py: Python<'_>,
    sql: &str,
    count: usize,
    placeholder: impl Fn(usize) -> String,
) -> PyResult<String> {
    let mut out = String::with_capacity(sql.len() + 8);
    let mut used = 0;
    let mut chars = sql.chars().peekable();
    while let Some(c) = chars.next() {
        if c != '%' {
            out.push(c);
            continue;
        }
        match chars.next() {
            Some('s') => {
                used += 1;
                out.push_str(&placeholder(used));
            }
            Some('%') => out.push('%'),
            other => {
                let after = other.map(String::from).unwrap_or_default();
                return Err(field_error(
                    py,
                    format!(
                        "raw SQL: unexpected '%{after}'; use %s for a parameter and %% for a \
                         literal %"
                    ),
                ));
            }
        }
    }
    if used != count {
        return Err(field_error(
            py,
            format!("raw SQL has {used} %s placeholder(s) but {count} parameter(s) were given"),
        ));
    }
    Ok(out)
}

/// A parameter of hand-written SQL, converted like a field value of the matching kind.
fn param(value: &Bound<'_, PyAny>) -> PyResult<Value> {
    let py = value.py();
    let types = orm_types(py)?;
    let kind = if value.is_none() {
        return Ok(Value::Null);
    } else if value.is_instance_of::<PyBool>() {
        return Ok(Value::Integer(i64::from(value.is_truthy()?)));
    } else if value.is_instance_of::<PyInt>() {
        FieldKind::BigInteger
    } else if value.is_instance_of::<PyFloat>() {
        FieldKind::Float
    } else if value.is_instance_of::<PyString>() {
        FieldKind::Text
    } else if let Ok(bytes) = value.cast::<PyBytes>() {
        return Ok(Value::Blob(bytes.as_bytes().to_vec()));
    } else if value.is_instance(types.decimal.bind(py))? {
        return Ok(Value::Decimal(value.str()?.to_string()));
    } else if value.is_instance(types.datetime.bind(py))? {
        FieldKind::DateTime
    } else if value.is_instance(types.date.bind(py))? {
        FieldKind::Date
    } else if value.is_instance(types.time.bind(py))? {
        FieldKind::Time
    } else if value.is_instance(types.uuid.bind(py))? {
        FieldKind::Uuid
    } else {
        FieldKind::Json
    };
    to_value(
        "raw SQL",
        &FieldMeta::column("parameter", kind),
        value,
        Mode::Compare,
    )
}

fn statement(py: Python<'_>, sql: &str, params: &Bound<'_, PyAny>) -> PyResult<Statement> {
    let database = current(py)?;
    let dialect = database.dialect;
    let values: Vec<Value> = if params.is_none() {
        Vec::new()
    } else {
        if params.is_instance_of::<PyString>() || params.is_instance_of::<PyDict>() {
            return Err(field_error(
                py,
                "raw SQL parameters must be a list or tuple of values",
            ));
        }
        params
            .try_iter()
            .map_err(|_| field_error(py, "raw SQL parameters must be a list or tuple of values"))?
            .map(|item| param(&item?))
            .collect::<PyResult<_>>()?
    };
    let text = placeholders(py, sql, values.len(), |i| dialect.placeholder(i))?;
    Ok(Statement::new(text, values))
}

/// Runs a statement that returns no rows; resolves to the number of rows affected.
#[pyfunction]
#[pyo3(signature = (sql, params=None))]
pub(crate) fn raw_execute<'py>(
    py: Python<'py>,
    sql: &str,
    params: Option<Bound<'py, PyAny>>,
) -> PyResult<Bound<'py, PyAny>> {
    let params = params.unwrap_or_else(|| py.None().into_bound(py));
    let statement = statement(py, sql, &params)?;
    let database = current(py)?;
    submit(
        py,
        &database,
        Job::Execute(statement),
        Box::new(|py, result| match result {
            Ok(Outcome::Affected(count)) => Ok(count.into_pyobject(py)?.into_any().unbind()),
            Ok(Outcome::Rows(rows)) => Ok(rows.rows.len().into_pyobject(py)?.into_any().unbind()),
            Ok(other) => Err(PyRuntimeError::new_err(format!(
                "unexpected database outcome {other:?}"
            ))),
            Err(err) => Err(db_error(py, err, None)),
        }),
    )
}

/// Runs a query; resolves to a list of dicts (column → value), or of `meta`'s model instances
/// when `meta` is given (every field's column must be selected; other columns become
/// attributes).
#[pyfunction]
#[pyo3(signature = (sql, params=None, meta=None))]
pub(crate) fn raw_fetch<'py>(
    py: Python<'py>,
    sql: &str,
    params: Option<Bound<'py, PyAny>>,
    meta: Option<PyRef<'py, PyModelMeta>>,
) -> PyResult<Bound<'py, PyAny>> {
    let params = params.unwrap_or_else(|| py.None().into_bound(py));
    let statement = statement(py, sql, &params)?;
    let database = current(py)?;
    let model = meta.map(|m| (m.meta.clone(), m.class(py)));
    submit(
        py,
        &database,
        Job::Query(statement),
        Box::new(move |py, result| {
            let rows = match result {
                Ok(Outcome::Rows(rows)) => rows,
                Ok(other) => {
                    return Err(PyRuntimeError::new_err(format!(
                        "unexpected database outcome {other:?}"
                    )));
                }
                Err(err) => return Err(db_error(py, err, None)),
            };
            let list = PyList::empty(py);
            let Some((meta, cls)) = model else {
                for row in rows.rows {
                    let dict = PyDict::new(py);
                    for (name, value) in rows.columns.iter().zip(row) {
                        dict.set_item(name, raw(py, value)?)?;
                    }
                    list.append(dict)?;
                }
                return Ok(list.into_any().unbind());
            };
            let positions: HashMap<&str, usize> = rows
                .columns
                .iter()
                .enumerate()
                .map(|(i, name)| (name.as_str(), i))
                .collect();
            let missing: Vec<&str> = meta
                .fields
                .iter()
                .filter(|f| !positions.contains_key(f.column.as_str()))
                .map(|f| f.column.as_str())
                .collect();
            if !missing.is_empty() {
                return Err(field_error(
                    py,
                    format!(
                        "{}.objects.raw(): the query must select every column of the model; \
                         missing: {}",
                        meta.label,
                        missing.join(", ")
                    ),
                ));
            }
            let cls = cls.bind(py);
            let new = cls.getattr("__new__")?;
            for row in rows.rows {
                let instance = new.call1((cls,))?;
                let dict = instance_dict(&instance)?;
                let mut cells: Vec<Option<Value>> = row.into_iter().map(Some).collect();
                for field in &meta.fields {
                    let value = cells[positions[field.column.as_str()]]
                        .take()
                        .unwrap_or(Value::Null);
                    dict.set_item(&field.attname, from_value(py, field, value)?)?;
                }
                for (name, cell) in rows.columns.iter().zip(cells) {
                    if let Some(value) = cell
                        && meta.fields.iter().all(|f| f.column != *name)
                    {
                        dict.set_item(name, raw(py, value)?)?;
                    }
                }
                dict.set_item("_persisted", true)?;
                list.append(instance)?;
            }
            Ok(list.into_any().unbind())
        }),
    )
}
