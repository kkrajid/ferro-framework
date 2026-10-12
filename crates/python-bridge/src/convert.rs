//! Converts a handler's return value into a response (FR-012).

use bytes::Bytes;
use ferro_core::FerroResponse;
use ferro_core::headers::{is_valid_header_name, is_valid_header_value};
use pyo3::intern;
use pyo3::prelude::*;
use pyo3::types::{PyByteArray, PyBytes, PyMemoryView, PyString};

use crate::json::{self, EncodeError};
use crate::types::types;

const JSON: &str = "application/json";
const TEXT: &str = "text/plain; charset=utf-8";
const BINARY: &str = "application/octet-stream";

#[derive(Debug)]
pub(crate) enum ConvertError {
    Encode(EncodeError),
    InvalidHeader(String),
    InvalidStatus(i64),
    Python(PyErr),
}

impl From<PyErr> for ConvertError {
    fn from(err: PyErr) -> Self {
        Self::Python(err)
    }
}

impl From<EncodeError> for ConvertError {
    fn from(err: EncodeError) -> Self {
        Self::Encode(err)
    }
}

impl ConvertError {
    pub(crate) fn message(&self, py: Python<'_>) -> String {
        match self {
            Self::Encode(err) => err.message(py),
            Self::InvalidHeader(name) => format!("invalid response header {name:?}"),
            Self::InvalidStatus(status) => {
                format!("invalid response status {status}; expected 100-599")
            }
            Self::Python(err) => err.value(py).to_string(),
        }
    }
}

pub(crate) fn to_response(value: &Bound<'_, PyAny>) -> Result<FerroResponse, ConvertError> {
    let py = value.py();
    if value.is_none() {
        return Ok(FerroResponse::new(204));
    }
    let types = types(py)?;
    if value.is_instance(types.response.bind(py))? {
        return from_response_object(value, value.is_instance(types.json_response.bind(py))?);
    }
    let (body, media_type) = content(value)?;
    let mut response = FerroResponse::new(200).with_body(body);
    if let Some(media_type) = media_type {
        response
            .headers
            .push(("content-type".into(), media_type.into()));
    }
    Ok(response)
}

/// Body bytes plus the media type implied by the content's type.
fn content(value: &Bound<'_, PyAny>) -> Result<(Bytes, Option<&'static str>), ConvertError> {
    let py = value.py();
    if value.is_none() {
        Ok((Bytes::new(), None))
    } else if let Ok(text) = value.cast::<PyString>() {
        Ok((
            Bytes::copy_from_slice(text.to_str()?.as_bytes()),
            Some(TEXT),
        ))
    } else if let Ok(bytes) = value.cast::<PyBytes>() {
        Ok((Bytes::copy_from_slice(bytes.as_bytes()), Some(BINARY)))
    } else if let Ok(array) = value.cast::<PyByteArray>() {
        Ok((Bytes::from(array.to_vec()), Some(BINARY)))
    } else if value.cast::<PyMemoryView>().is_ok() {
        let bytes = py.get_type::<PyBytes>().call1((value,))?;
        Ok((
            Bytes::copy_from_slice(bytes.cast::<PyBytes>().map_err(PyErr::from)?.as_bytes()),
            Some(BINARY),
        ))
    } else {
        Ok((Bytes::from(json::encode(value)?), Some(JSON)))
    }
}

fn from_response_object(
    value: &Bound<'_, PyAny>,
    force_json: bool,
) -> Result<FerroResponse, ConvertError> {
    let py = value.py();
    let status: i64 = value.getattr(intern!(py, "status"))?.extract()?;
    let status = u16::try_from(status)
        .ok()
        .filter(|s| (100..=599).contains(s))
        .ok_or(ConvertError::InvalidStatus(status))?;
    let body_value = value.getattr(intern!(py, "content"))?;
    let (body, implied) = if force_json {
        (Bytes::from(json::encode(&body_value)?), Some(JSON))
    } else {
        content(&body_value)?
    };
    let media_type: Option<String> = value.getattr(intern!(py, "media_type"))?.extract()?;
    let headers: Vec<(String, String)> = value.getattr(intern!(py, "headers"))?.extract()?;

    let mut response = FerroResponse::new(status).with_body(body);
    for (name, header_value) in headers {
        if !is_valid_header_name(&name) || !is_valid_header_value(&header_value) {
            return Err(ConvertError::InvalidHeader(name));
        }
        response.headers.push((name, header_value));
    }
    if !response.has_header("content-type")
        && let Some(media_type) = media_type.as_deref().or(implied)
    {
        if !is_valid_header_value(media_type) {
            return Err(ConvertError::InvalidHeader("content-type".into()));
        }
        response
            .headers
            .push(("content-type".into(), media_type.to_string()));
    }
    Ok(response)
}
