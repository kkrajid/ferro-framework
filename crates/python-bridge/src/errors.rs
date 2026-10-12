//! Maps handler failures to error envelopes (FR-015–FR-018) and logs unexpected ones.

use ferro_core::headers::{is_valid_header_name, is_valid_header_value};
use ferro_core::{DebugInfo, ErrorDetail, ErrorEnvelope, FerroResponse};
use pyo3::intern;
use pyo3::prelude::*;
use pyo3::types::{PyDict, PyList, PyString};

use crate::bridge::CallError;
use crate::convert::ConvertError;
use crate::types::types;

pub(crate) fn from_call_error(
    py: Python<'_>,
    error: CallError,
    request_id: &str,
    handler: &str,
    debug: bool,
) -> FerroResponse {
    match error {
        CallError::Cancelled => unavailable(request_id),
        CallError::Raised(err) => from_exception(py, err, request_id, handler, debug),
    }
}

fn unavailable(request_id: &str) -> FerroResponse {
    ErrorEnvelope::new(503, request_id)
        .with_header("retry-after", "1")
        .into_response()
}

fn from_exception(
    py: Python<'_>,
    err: PyErr,
    request_id: &str,
    handler: &str,
    debug: bool,
) -> FerroResponse {
    let Ok(types) = types(py) else {
        return internal_error(py, Some(&err), None, request_id, handler, debug);
    };
    let value = err.value(py);
    let is = |class: &Py<pyo3::types::PyType>| value.is_instance(class.bind(py)).unwrap_or(false);
    if is(&types.http_exception) {
        match http_exception(value, request_id, is(&types.validation_error)) {
            Ok(response) => response,
            Err(inner) => internal_error(py, Some(&inner), None, request_id, handler, debug),
        }
    } else if is(&types.cancelled_error) {
        unavailable(request_id)
    } else if crate::orm_values::orm_types(py).is_ok_and(|orm| is(&orm.database_unavailable_error))
    {
        // An outage, not a bug: no traceback; the database's message stays in the log.
        tracing::warn!(
            target: "ferro.handler",
            request_id = %request_id,
            handler = %handler,
            "database unavailable: {value}"
        );
        unavailable(request_id)
    } else {
        internal_error(py, Some(&err), None, request_id, handler, debug)
    }
}

fn http_exception(
    value: &Bound<'_, pyo3::exceptions::PyBaseException>,
    request_id: &str,
    is_validation: bool,
) -> PyResult<FerroResponse> {
    let py = value.py();
    let status: u16 = value.getattr(intern!(py, "status"))?.extract()?;
    let status = if (400..=599).contains(&status) {
        status
    } else {
        500
    };
    let message: String = value.getattr(intern!(py, "message"))?.str()?.to_string();
    let mut envelope = ErrorEnvelope::new(status, request_id).with_message(message);
    if is_validation {
        let details = value.getattr(intern!(py, "details"))?;
        let details = details.cast::<PyList>()?;
        let mut items = Vec::with_capacity(details.len());
        for detail in details.iter() {
            let detail = detail.cast::<PyDict>()?;
            let field = |key: &str| -> PyResult<String> {
                Ok(match detail.get_item(key)? {
                    Some(v) => v.str()?.to_string(),
                    None => String::new(),
                })
            };
            items.push(ErrorDetail {
                param: field("param")?,
                location: field("location")?,
                problem: field("problem")?,
            });
        }
        envelope = envelope.with_details(items);
    }
    let headers = value.getattr(intern!(py, "headers"))?;
    let headers = headers.cast::<PyDict>()?;
    for (name, header_value) in headers.iter() {
        let name: String = name.extract()?;
        let header_value: String = header_value.str()?.to_string();
        if !is_valid_header_name(&name) || !is_valid_header_value(&header_value) {
            return Err(pyo3::exceptions::PyValueError::new_err(format!(
                "HTTPException has an invalid header {name:?}"
            )));
        }
        envelope = envelope.with_header(name, header_value);
    }
    Ok(envelope.into_response())
}

pub(crate) fn from_convert_error(
    py: Python<'_>,
    error: ConvertError,
    request_id: &str,
    handler: &str,
    debug: bool,
) -> FerroResponse {
    match error {
        ConvertError::Python(err) => {
            internal_error(py, Some(&err), None, request_id, handler, debug)
        }
        other => {
            let message = other.message(py);
            internal_error(
                py,
                None,
                Some(("TypeError", message)),
                request_id,
                handler,
                debug,
            )
        }
    }
}

/// Logs the failure with its traceback and returns a 500 envelope; debug detail is added only in
/// debug mode (FR-017, FR-018).
fn internal_error(
    py: Python<'_>,
    err: Option<&PyErr>,
    plain: Option<(&str, String)>,
    request_id: &str,
    handler: &str,
    debug: bool,
) -> FerroResponse {
    let (type_name, message, traceback) = match (err, plain) {
        (Some(err), _) => describe(py, err),
        (None, Some((type_name, message))) => (type_name.to_string(), message, Vec::new()),
        (None, None) => ("Exception".to_string(), String::new(), Vec::new()),
    };
    let what = if err.is_some() {
        "raised"
    } else {
        "returned an unusable value:"
    };
    tracing::error!(
        target: "ferro.handler",
        request_id = %request_id,
        handler = %handler,
        "handler {handler} {what} {type_name}: {message}\n{}",
        traceback.join("\n")
    );
    let mut envelope = ErrorEnvelope::new(500, request_id);
    if debug {
        envelope = envelope.with_debug(DebugInfo {
            type_name,
            exception: message,
            traceback,
        });
    }
    envelope.into_response()
}

fn describe(py: Python<'_>, err: &PyErr) -> (String, String, Vec<String>) {
    let value = err.value(py);
    let type_name = value
        .get_type()
        .qualname()
        .map(|n| n.to_string())
        .unwrap_or_else(|_| "Exception".to_string());
    let message = value.str().map(|s| s.to_string()).unwrap_or_default();
    let traceback = types(py)
        .and_then(|t| t.format_exception.bind(py).call1((value,)))
        .and_then(|lines| {
            let mut out = Vec::new();
            for chunk in lines.try_iter()? {
                let chunk = chunk?;
                let text = chunk.cast::<PyString>()?.to_str()?.to_string();
                out.extend(text.trim_end_matches('\n').split('\n').map(str::to_string));
            }
            Ok(out)
        })
        .unwrap_or_default();
    (type_name, message, traceback)
}
