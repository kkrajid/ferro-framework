//! JSON encoding of Python values and decoding into Python values, done in Rust.
//!
//! Encoding follows Python's `json` module where it matters to users (key coercion, exact big
//! integers, NaN rejection) and additionally supports `datetime`/`date`/`time` (ISO 8601),
//! `Decimal` and `UUID` (as strings). Non-ASCII text is written as UTF-8, not escaped.

use pyo3::exceptions::{PyTypeError, PyValueError};
use pyo3::prelude::*;
use pyo3::types::{PyBool, PyBytes, PyDict, PyFloat, PyInt, PyList, PyString, PyTuple};
use serde_json::Value;

use crate::types::{Types, types};

const MAX_DEPTH: usize = 256;

#[derive(Debug)]
pub(crate) enum EncodeError {
    Unsupported(String),
    UnsupportedKey(String),
    NotFinite,
    TooDeep,
    Python(PyErr),
}

impl From<PyErr> for EncodeError {
    fn from(err: PyErr) -> Self {
        Self::Python(err)
    }
}

impl EncodeError {
    pub(crate) fn message(&self, py: Python<'_>) -> String {
        match self {
            Self::Unsupported(name) => format!("Object of type {name} is not JSON serializable"),
            Self::UnsupportedKey(name) => {
                format!("keys must be str, int, float, bool or None, not {name}")
            }
            Self::NotFinite => {
                "Out of range float values (NaN, Infinity) are not JSON compliant".to_string()
            }
            Self::TooDeep => format!("value is nested too deep (more than {MAX_DEPTH} levels)"),
            Self::Python(err) => err.value(py).to_string(),
        }
    }

    pub(crate) fn into_pyerr(self, py: Python<'_>) -> PyErr {
        match self {
            Self::Python(err) => err,
            Self::Unsupported(_) | Self::UnsupportedKey(_) => {
                PyTypeError::new_err(self.message(py))
            }
            Self::NotFinite | Self::TooDeep => PyValueError::new_err(self.message(py)),
        }
    }
}

pub(crate) fn encode(value: &Bound<'_, PyAny>) -> Result<Vec<u8>, EncodeError> {
    let types = types(value.py())?;
    let mut out = Vec::with_capacity(128);
    write_value(value, &mut out, 0, types)?;
    Ok(out)
}

fn type_name(value: &Bound<'_, PyAny>) -> String {
    value
        .get_type()
        .name()
        .map(|n| n.to_string())
        .unwrap_or_else(|_| "object".to_string())
}

fn write_str(out: &mut Vec<u8>, text: &str) {
    serde_json::to_writer(&mut *out, text).expect("writing to a Vec cannot fail");
}

fn write_value(
    value: &Bound<'_, PyAny>,
    out: &mut Vec<u8>,
    depth: usize,
    types: &Types,
) -> Result<(), EncodeError> {
    if depth > MAX_DEPTH {
        return Err(EncodeError::TooDeep);
    }
    if value.is_none() {
        out.extend_from_slice(b"null");
    } else if let Ok(text) = value.cast::<PyString>() {
        write_str(out, text.to_str()?);
    } else if let Ok(flag) = value.cast::<PyBool>() {
        out.extend_from_slice(if flag.is_true() { b"true" } else { b"false" });
    } else if let Ok(int) = value.cast::<PyInt>() {
        match int.extract::<i64>() {
            Ok(small) => out.extend_from_slice(small.to_string().as_bytes()),
            Err(_) => out.extend_from_slice(int.str()?.to_str()?.as_bytes()),
        }
    } else if let Ok(float) = value.cast::<PyFloat>() {
        let number = float.value();
        if !number.is_finite() {
            return Err(EncodeError::NotFinite);
        }
        serde_json::to_writer(&mut *out, &number).expect("finite float serializes");
    } else if let Ok(dict) = value.cast::<PyDict>() {
        out.push(b'{');
        for (index, (key, item)) in dict.iter().enumerate() {
            if index > 0 {
                out.push(b',');
            }
            write_key(&key, out)?;
            out.push(b':');
            write_value(&item, out, depth + 1, types)?;
        }
        out.push(b'}');
    } else if let Ok(list) = value.cast::<PyList>() {
        out.push(b'[');
        for (index, item) in list.iter().enumerate() {
            if index > 0 {
                out.push(b',');
            }
            write_value(&item, out, depth + 1, types)?;
        }
        out.push(b']');
    } else if let Ok(tuple) = value.cast::<PyTuple>() {
        out.push(b'[');
        for (index, item) in tuple.iter().enumerate() {
            if index > 0 {
                out.push(b',');
            }
            write_value(&item, out, depth + 1, types)?;
        }
        out.push(b']');
    } else if is_any_instance(value, &types.datetime_types)? {
        let text = value.call_method0("isoformat")?;
        write_str(out, text.cast::<PyString>().map_err(PyErr::from)?.to_str()?);
    } else if value.is_instance(types.decimal.bind(value.py()))?
        || value.is_instance(types.uuid.bind(value.py()))?
    {
        write_str(out, value.str()?.to_str()?);
    } else {
        return Err(EncodeError::Unsupported(type_name(value)));
    }
    Ok(())
}

fn is_any_instance(
    value: &Bound<'_, PyAny>,
    classes: &[Py<pyo3::types::PyType>],
) -> PyResult<bool> {
    for class in classes {
        if value.is_instance(class.bind(value.py()))? {
            return Ok(true);
        }
    }
    Ok(false)
}

fn write_key(key: &Bound<'_, PyAny>, out: &mut Vec<u8>) -> Result<(), EncodeError> {
    if let Ok(text) = key.cast::<PyString>() {
        write_str(out, text.to_str()?);
    } else if let Ok(flag) = key.cast::<PyBool>() {
        write_str(out, if flag.is_true() { "true" } else { "false" });
    } else if key.is_none() {
        write_str(out, "null");
    } else if key.cast::<PyInt>().is_ok() {
        write_str(out, key.str()?.to_str()?);
    } else if let Ok(float) = key.cast::<PyFloat>() {
        if !float.value().is_finite() {
            return Err(EncodeError::NotFinite);
        }
        write_str(out, key.repr()?.to_str()?);
    } else {
        return Err(EncodeError::UnsupportedKey(type_name(key)));
    }
    Ok(())
}

pub(crate) fn decode<'py>(py: Python<'py>, data: &[u8]) -> PyResult<Bound<'py, PyAny>> {
    let value: Value = serde_json::from_slice(data)
        .map_err(|err| PyValueError::new_err(format!("invalid JSON: {err}")))?;
    to_python(py, &value)
}

fn to_python<'py>(py: Python<'py>, value: &Value) -> PyResult<Bound<'py, PyAny>> {
    Ok(match value {
        Value::Null => py.None().into_bound(py),
        Value::Bool(flag) => PyBool::new(py, *flag).to_owned().into_any(),
        Value::Number(number) => {
            if let Some(int) = number.as_i64() {
                int.into_pyobject(py)?.into_any()
            } else if let Some(int) = number.as_u64() {
                int.into_pyobject(py)?.into_any()
            } else {
                let text = number.to_string();
                if text.contains(['.', 'e', 'E']) {
                    let float: f64 = text
                        .parse()
                        .map_err(|_| PyValueError::new_err(format!("invalid number {text}")))?;
                    PyFloat::new(py, float).into_any()
                } else {
                    py.get_type::<PyInt>().call1((text,))?
                }
            }
        }
        Value::String(text) => PyString::new(py, text).into_any(),
        Value::Array(items) => {
            let list = PyList::empty(py);
            for item in items {
                list.append(to_python(py, item)?)?;
            }
            list.into_any()
        }
        Value::Object(map) => {
            let dict = PyDict::new(py);
            for (key, item) in map {
                dict.set_item(key, to_python(py, item)?)?;
            }
            dict.into_any()
        }
    })
}

/// Encodes a value with the same encoder the server uses for responses.
#[pyfunction]
pub(crate) fn json_dumps<'py>(
    py: Python<'py>,
    value: &Bound<'py, PyAny>,
) -> PyResult<Bound<'py, PyBytes>> {
    let encoded = encode(value).map_err(|err| err.into_pyerr(py))?;
    Ok(PyBytes::new(py, &encoded))
}

/// Decodes JSON bytes with the same decoder `Request.json()` uses.
#[pyfunction]
pub(crate) fn json_loads<'py>(py: Python<'py>, data: &[u8]) -> PyResult<Bound<'py, PyAny>> {
    decode(py, data)
}
