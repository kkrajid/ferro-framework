//! Python ↔ database value conversion and validation per field kind (research R5, FR-006).

use ferro_database::{DbError, IntegrityKind, Value};
use ferro_orm::{FieldKind, FieldMeta, ModelMeta, OrmError};
use pyo3::prelude::*;
use pyo3::sync::PyOnceLock;
use pyo3::types::{IntoPyDict, PyBool, PyBytes, PyFloat, PyInt, PyString, PyType};

use crate::json;

pub(crate) struct OrmTypes {
    pub(crate) field_error: Py<PyType>,
    pub(crate) database_error: Py<PyType>,
    pub(crate) integrity_error: Py<PyType>,
    pub(crate) protected_error: Py<PyType>,
    pub(crate) operational_error: Py<PyType>,
    pub(crate) database_unavailable_error: Py<PyType>,
    /// `ferro.models.Model`, to recognize instances passed as lookup values.
    pub(crate) model_base: Py<PyType>,
    pub(crate) configuration_error: Py<PyType>,
    pub(crate) date: Py<PyType>,
    pub(crate) datetime: Py<PyType>,
    pub(crate) time: Py<PyType>,
    utc: Py<PyAny>,
    pub(crate) decimal: Py<PyType>,
    pub(crate) uuid: Py<PyType>,
    pub(crate) get_running_loop: Py<PyAny>,
}

static ORM_TYPES: PyOnceLock<OrmTypes> = PyOnceLock::new();

fn class(py: Python<'_>, module: &str, name: &str) -> PyResult<Py<PyType>> {
    Ok(py
        .import(module)?
        .getattr(name)?
        .cast_into::<PyType>()?
        .unbind())
}

pub(crate) fn orm_types(py: Python<'_>) -> PyResult<&OrmTypes> {
    ORM_TYPES.get_or_try_init(py, || {
        let exceptions = "ferro.models.exceptions";
        Ok(OrmTypes {
            field_error: class(py, exceptions, "FieldError")?,
            database_error: class(py, exceptions, "DatabaseError")?,
            integrity_error: class(py, exceptions, "IntegrityError")?,
            protected_error: class(py, exceptions, "ProtectedError")?,
            operational_error: class(py, exceptions, "OperationalError")?,
            database_unavailable_error: class(py, exceptions, "DatabaseUnavailableError")?,
            model_base: class(py, "ferro.models.base", "Model")?,
            configuration_error: class(py, "ferro.exceptions", "ConfigurationError")?,
            date: class(py, "datetime", "date")?,
            datetime: class(py, "datetime", "datetime")?,
            time: class(py, "datetime", "time")?,
            utc: py.import("datetime")?.getattr("UTC")?.unbind(),
            decimal: class(py, "decimal", "Decimal")?,
            uuid: class(py, "uuid", "UUID")?,
            get_running_loop: py.import("asyncio")?.getattr("get_running_loop")?.unbind(),
        })
    })
}

fn raise(py: Python<'_>, class: &Py<PyType>, message: String) -> PyErr {
    match class.bind(py).call1((message,)) {
        Ok(exception) => PyErr::from_value(exception),
        Err(err) => err,
    }
}

pub(crate) fn field_error(py: Python<'_>, message: impl Into<String>) -> PyErr {
    match orm_types(py) {
        Ok(types) => raise(py, &types.field_error, message.into()),
        Err(err) => err,
    }
}

pub(crate) fn orm_error(py: Python<'_>, err: OrmError) -> PyErr {
    field_error(py, err.0)
}

pub(crate) fn configuration_error(py: Python<'_>, message: impl Into<String>) -> PyErr {
    match orm_types(py) {
        Ok(types) => raise(py, &types.configuration_error, message.into()),
        Err(err) => err,
    }
}

/// Maps a backend error to the `DatabaseError` family, naming model fields when known.
pub(crate) fn db_error(py: Python<'_>, err: DbError, meta: Option<&ModelMeta>) -> PyErr {
    let types = match orm_types(py) {
        Ok(types) => types,
        Err(err) => return err,
    };
    match err {
        DbError::Integrity {
            kind,
            table,
            columns,
            message,
        } => {
            let meta = meta.filter(|m| table.as_deref().is_none_or(|t| t == m.table));
            let fields: Vec<String> = columns
                .iter()
                .map(|column| {
                    meta.and_then(|m| m.fields.iter().find(|f| &f.column == column))
                        .map_or_else(|| column.clone(), |f| f.name.clone())
                })
                .collect();
            let label = meta.map(|m| m.label.clone());
            let what = match kind {
                IntegrityKind::Unique => "duplicate value for unique field",
                IntegrityKind::NotNull => "missing value for non-null field",
                IntegrityKind::ForeignKey => "foreign key constraint failed",
                IntegrityKind::Check => "check constraint failed",
                IntegrityKind::Other => "constraint failed",
            };
            let subject = match (&label, fields.as_slice()) {
                (Some(label), []) => format!("{label}: {what}"),
                (Some(label), fields) => format!("{label}: {what} '{}'", fields.join("', '")),
                (None, _) => what.to_string(),
            };
            let exception = types.integrity_error.bind(py).call1((
                format!("{subject} ({message})"),
                label,
                fields,
            ));
            match exception {
                Ok(exception) => PyErr::from_value(exception),
                Err(err) => err,
            }
        }
        DbError::MissingTable { message } => raise(
            py,
            &types.operational_error,
            format!(
                "{message}; create it with `ferro db create-tables` (or ferro.db.create_tables())"
            ),
        ),
        DbError::Operational { message } => raise(py, &types.operational_error, message),
        DbError::Unavailable { message } => raise(py, &types.database_unavailable_error, message),
        DbError::Closed => raise(
            py,
            &types.database_unavailable_error,
            DbError::Closed.to_string(),
        ),
        DbError::Other { message } => raise(py, &types.database_error, message),
    }
}

/// Errors of a DELETE: a foreign key violation means rows still refer to the deleted ones
/// through `PROTECT` (or `DO_NOTHING`), reported as `ProtectedError`.
pub(crate) fn delete_error(py: Python<'_>, err: DbError, meta: &ModelMeta) -> PyErr {
    match err {
        DbError::Integrity {
            kind: IntegrityKind::ForeignKey,
            message,
            ..
        } => {
            let types = match orm_types(py) {
                Ok(types) => types,
                Err(err) => return err,
            };
            let exception = types.protected_error.bind(py).call1((
                format!(
                    "{}: cannot delete, other rows still refer to it through a protected \
                     foreign key ({message})",
                    meta.label
                ),
                meta.label.clone(),
                Vec::<String>::new(),
            ));
            match exception {
                Ok(exception) => PyErr::from_value(exception),
                Err(err) => err,
            }
        }
        other => db_error(py, other, Some(meta)),
    }
}

fn type_name(value: &Bound<'_, PyAny>) -> String {
    value
        .get_type()
        .name()
        .map(|n| n.to_string())
        .unwrap_or_else(|_| "object".into())
}

/// How strictly a value is checked: `Store` for writes (all rules), `Compare` for lookup values
/// (type rules only; a too-long string simply matches nothing).
#[derive(Clone, Copy, PartialEq, Eq)]
pub(crate) enum Mode {
    Store,
    Compare,
}

pub(crate) fn to_value(
    label: &str,
    field: &FieldMeta,
    value: &Bound<'_, PyAny>,
    mode: Mode,
) -> PyResult<Value> {
    let py = value.py();
    let fail = |problem: String| field_error(py, format!("{label}.{}: {problem}", field.name));
    let expected = |what: &str| fail(format!("expected {what}, got {}", type_name(value)));
    if value.is_none() {
        return if field.null || field.auto || mode == Mode::Compare {
            Ok(Value::Null)
        } else {
            Err(fail(
                "value may not be None (set null=True to allow it)".into(),
            ))
        };
    }
    let types = orm_types(py)?;
    let is_bool = value.is_instance_of::<PyBool>();
    match &field.kind {
        FieldKind::String { max_length } => {
            let text = value
                .cast::<PyString>()
                .map_err(|_| expected("str"))?
                .to_str()?;
            let length = text.chars().count();
            if mode == Mode::Store && length > *max_length as usize {
                return Err(fail(format!(
                    "value has {length} characters; max_length is {max_length}"
                )));
            }
            Ok(Value::Text(text.to_string()))
        }
        FieldKind::Text => Ok(Value::Text(
            value
                .cast::<PyString>()
                .map_err(|_| expected("str"))?
                .to_str()?
                .to_string(),
        )),
        FieldKind::Integer | FieldKind::BigInteger => {
            if is_bool || !value.is_instance_of::<PyInt>() {
                return Err(expected("int"));
            }
            let number: i64 = value
                .extract()
                .map_err(|_| fail("value is outside the 64-bit integer range".into()))?;
            if field.kind == FieldKind::Integer && i32::try_from(number).is_err() {
                return Err(fail(format!(
                    "value {number} is outside the Integer range ({} to {}); use BigInteger",
                    i32::MIN,
                    i32::MAX
                )));
            }
            Ok(Value::Integer(number))
        }
        FieldKind::Float => {
            if is_bool {
                return Err(expected("float"));
            }
            let number = if let Ok(float) = value.cast::<PyFloat>() {
                float.value()
            } else if value.is_instance_of::<PyInt>() {
                value.extract::<f64>()?
            } else {
                return Err(expected("float"));
            };
            if !number.is_finite() {
                return Err(fail("NaN and infinity cannot be stored".into()));
            }
            Ok(Value::Real(number))
        }
        FieldKind::Decimal {
            max_digits,
            decimal_places,
        } => {
            let decimal_class = types.decimal.bind(py);
            let decimal = if value.is_instance(decimal_class)? {
                value.clone()
            } else if is_bool || value.is_instance_of::<PyFloat>() {
                return Err(fail(format!(
                    "expected Decimal, int or str, got {} (floats are inexact; use Decimal)",
                    type_name(value)
                )));
            } else if value.is_instance_of::<PyInt>() || value.is_instance_of::<PyString>() {
                decimal_class.call1((value,)).map_err(|_| {
                    fail(format!(
                        "{} is not a decimal number",
                        value.repr().map(|r| r.to_string()).unwrap_or_default()
                    ))
                })?
            } else {
                return Err(expected("Decimal"));
            };
            if !decimal.call_method0("is_finite")?.is_truthy()? {
                return Err(fail("NaN and infinity cannot be stored".into()));
            }
            if mode == Mode::Store {
                let (_, digits, exponent): (Bound<'_, PyAny>, Vec<u8>, i64) =
                    decimal.call_method0("as_tuple")?.extract()?;
                let count = digits.len() as i64;
                let (whole, places) = if exponent >= 0 {
                    (count + exponent, 0)
                } else {
                    ((count + exponent).max(0), -exponent)
                };
                let allowed_whole = i64::from(*max_digits) - i64::from(*decimal_places);
                if places > i64::from(*decimal_places) {
                    return Err(fail(format!(
                        "value has {places} decimal places; decimal_places is {decimal_places}"
                    )));
                }
                if whole > allowed_whole {
                    return Err(fail(format!(
                        "value has {whole} digits before the decimal point; at most {allowed_whole} allowed (max_digits={max_digits}, decimal_places={decimal_places})"
                    )));
                }
            }
            // Plain notation ("1E+3" → "1000"), exact; backends without a decimal type convert.
            let text: String = decimal.call_method1("__format__", ("f",))?.extract()?;
            Ok(Value::Decimal(text))
        }
        FieldKind::Boolean => {
            if !is_bool {
                return Err(expected("bool"));
            }
            Ok(Value::Integer(i64::from(value.is_truthy()?)))
        }
        FieldKind::Date => {
            if !value.is_instance(types.date.bind(py))?
                || value.is_instance(types.datetime.bind(py))?
            {
                return Err(expected("datetime.date"));
            }
            Ok(Value::Text(value.call_method0("isoformat")?.extract()?))
        }
        FieldKind::DateTime => {
            if !value.is_instance(types.datetime.bind(py))? {
                return Err(expected("datetime.datetime"));
            }
            if value.call_method0("utcoffset")?.is_none() {
                return Err(fail(
                    "naive datetime; use a timezone-aware value such as datetime.now(UTC)".into(),
                ));
            }
            let utc = value.call_method1("astimezone", (types.utc.bind(py),))?;
            let naive = utc.call_method(
                "replace",
                (),
                Some(&[("tzinfo", py.None())].into_py_dict(py)?),
            )?;
            Ok(Value::Text(
                naive
                    .call_method1("isoformat", (" ", "microseconds"))?
                    .extract()?,
            ))
        }
        FieldKind::Time => {
            if !value.is_instance(types.time.bind(py))? {
                return Err(expected("datetime.time"));
            }
            if !value.call_method0("utcoffset")?.is_none() {
                return Err(fail(
                    "timezone-aware times are not supported; use a naive time".into(),
                ));
            }
            Ok(Value::Text(
                value
                    .call_method(
                        "isoformat",
                        (),
                        Some(&[("timespec", "microseconds")].into_py_dict(py)?),
                    )?
                    .extract()?,
            ))
        }
        FieldKind::Uuid => {
            let uuid_class = types.uuid.bind(py);
            let uuid = if value.is_instance(uuid_class)? {
                value.clone()
            } else if value.is_instance_of::<PyString>() {
                uuid_class
                    .call1((value,))
                    .map_err(|_| fail("not a valid UUID string".into()))?
            } else {
                return Err(expected("uuid.UUID"));
            };
            Ok(Value::Text(uuid.str()?.to_string()))
        }
        FieldKind::Json => {
            let encoded = json::encode(value).map_err(|err| {
                fail(format!(
                    "value is not JSON serializable: {}",
                    err.message(py)
                ))
            })?;
            Ok(Value::Text(String::from_utf8(encoded).unwrap_or_default()))
        }
    }
}

/// Converts a stored cell back to the field's Python type.
pub(crate) fn from_value<'py>(
    py: Python<'py>,
    field: &FieldMeta,
    value: Value,
) -> PyResult<Bound<'py, PyAny>> {
    if value == Value::Null {
        return Ok(py.None().into_bound(py));
    }
    let types = orm_types(py)?;
    Ok(match (&field.kind, value) {
        (FieldKind::Boolean, Value::Integer(v)) => PyBool::new(py, v != 0).to_owned().into_any(),
        (FieldKind::Float, Value::Integer(v)) => PyFloat::new(py, v as f64).into_any(),
        (FieldKind::Decimal { decimal_places, .. }, Value::Real(v)) => {
            let places = usize::from(*decimal_places);
            types.decimal.bind(py).call1((format!("{v:.places$}"),))?
        }
        (FieldKind::Decimal { decimal_places, .. }, Value::Integer(v)) => {
            let places = usize::from(*decimal_places);
            types
                .decimal
                .bind(py)
                .call1((format!("{:.places$}", v as f64),))?
        }
        (FieldKind::Decimal { .. }, Value::Text(v) | Value::Decimal(v)) => {
            types.decimal.bind(py).call1((v,))?
        }
        (FieldKind::Date, Value::Text(v)) => {
            types.date.bind(py).call_method1("fromisoformat", (v,))?
        }
        (FieldKind::DateTime, Value::Text(v)) => types
            .datetime
            .bind(py)
            .call_method1("fromisoformat", (format!("{v}+00:00"),))?,
        (FieldKind::Time, Value::Text(v)) => {
            types.time.bind(py).call_method1("fromisoformat", (v,))?
        }
        (FieldKind::Uuid, Value::Text(v)) => types.uuid.bind(py).call1((v,))?,
        (FieldKind::Json, Value::Text(v)) => json::decode(py, v.as_bytes())?,
        (_, value) => raw(py, value)?,
    })
}

/// Plain Python value for a database value (used for `sql()` parameters and unknown cells).
pub(crate) fn raw<'py>(py: Python<'py>, value: Value) -> PyResult<Bound<'py, PyAny>> {
    Ok(match value {
        Value::Null => py.None().into_bound(py),
        Value::Integer(v) => v.into_pyobject(py)?.into_any(),
        Value::Real(v) => PyFloat::new(py, v).into_any(),
        Value::Decimal(v) => orm_types(py)?.decimal.bind(py).call1((v,))?,
        Value::Text(v) => PyString::new(py, &v).into_any(),
        Value::Blob(v) => PyBytes::new(py, &v).into_any(),
    })
}
