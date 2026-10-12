//! The single JSON error shape used for every framework-generated error (FR-016).

use serde_json::{Map, Value};

use crate::FerroResponse;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ErrorDetail {
    pub param: String,
    pub location: String,
    pub problem: String,
}

/// Exception detail, included only in debug mode (FR-018).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct DebugInfo {
    pub type_name: String,
    pub exception: String,
    pub traceback: Vec<String>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ErrorEnvelope {
    pub status: u16,
    pub message: String,
    pub request_id: String,
    pub details: Option<Vec<ErrorDetail>>,
    pub debug: Option<DebugInfo>,
    pub headers: Vec<(String, String)>,
}

impl ErrorEnvelope {
    pub fn new(status: u16, request_id: impl Into<String>) -> Self {
        Self {
            status,
            message: reason_phrase(status).to_string(),
            request_id: request_id.into(),
            details: None,
            debug: None,
            headers: Vec::new(),
        }
    }

    pub fn with_message(mut self, message: impl Into<String>) -> Self {
        self.message = message.into();
        self
    }

    pub fn with_details(mut self, details: Vec<ErrorDetail>) -> Self {
        self.details = Some(details);
        self
    }

    pub fn with_debug(mut self, debug: DebugInfo) -> Self {
        self.debug = Some(debug);
        self
    }

    pub fn with_header(mut self, name: impl Into<String>, value: impl Into<String>) -> Self {
        self.headers.push((name.into(), value.into()));
        self
    }

    pub fn to_json(&self) -> Vec<u8> {
        let mut error = Map::new();
        error.insert("status".into(), Value::from(self.status));
        error.insert("message".into(), Value::from(self.message.as_str()));
        error.insert("request_id".into(), Value::from(self.request_id.as_str()));
        if let Some(details) = &self.details {
            let items = details
                .iter()
                .map(|d| {
                    let mut item = Map::new();
                    item.insert("param".into(), Value::from(d.param.as_str()));
                    item.insert("location".into(), Value::from(d.location.as_str()));
                    item.insert("problem".into(), Value::from(d.problem.as_str()));
                    Value::Object(item)
                })
                .collect();
            error.insert("details".into(), Value::Array(items));
        }
        if let Some(debug) = &self.debug {
            error.insert("type".into(), Value::from(debug.type_name.as_str()));
            error.insert("exception".into(), Value::from(debug.exception.as_str()));
            error.insert(
                "traceback".into(),
                Value::Array(
                    debug
                        .traceback
                        .iter()
                        .map(|l| Value::from(l.as_str()))
                        .collect(),
                ),
            );
        }
        let mut root = Map::new();
        root.insert("error".into(), Value::Object(error));
        serde_json::to_vec(&Value::Object(root)).expect("envelope serialization cannot fail")
    }

    pub fn into_response(self) -> FerroResponse {
        let body = self.to_json();
        let mut response = FerroResponse::json(self.status, body);
        response.headers.extend(self.headers);
        response
    }
}

/// RFC 9110 reason phrases for the statuses Ferro and applications commonly use.
pub fn reason_phrase(status: u16) -> &'static str {
    match status {
        100 => "Continue",
        101 => "Switching Protocols",
        200 => "OK",
        201 => "Created",
        202 => "Accepted",
        204 => "No Content",
        301 => "Moved Permanently",
        302 => "Found",
        303 => "See Other",
        304 => "Not Modified",
        307 => "Temporary Redirect",
        308 => "Permanent Redirect",
        400 => "Bad Request",
        401 => "Unauthorized",
        403 => "Forbidden",
        404 => "Not Found",
        405 => "Method Not Allowed",
        406 => "Not Acceptable",
        408 => "Request Timeout",
        409 => "Conflict",
        410 => "Gone",
        411 => "Length Required",
        412 => "Precondition Failed",
        413 => "Content Too Large",
        414 => "URI Too Long",
        415 => "Unsupported Media Type",
        422 => "Unprocessable Content",
        429 => "Too Many Requests",
        500 => "Internal Server Error",
        501 => "Not Implemented",
        502 => "Bad Gateway",
        503 => "Service Unavailable",
        504 => "Gateway Timeout",
        _ => "Unknown Status",
    }
}
