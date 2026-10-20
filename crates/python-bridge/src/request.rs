//! The `Request` object handed to Python handlers, plus its query/header views.

use std::sync::{Mutex, OnceLock};

use ferro_core::FerroRequest;
use percent_encoding::percent_decode_str;
use pyo3::exceptions::{PyKeyError, PyStopIteration};
use pyo3::prelude::*;
use pyo3::types::{PyBytes, PyDict, PyIterator, PyList};

use crate::json;
use crate::types::types;

fn decode_component(text: &str, plus_as_space: bool) -> String {
    if plus_as_space && text.contains('+') {
        percent_decode_str(&text.replace('+', " "))
            .decode_utf8_lossy()
            .into_owned()
    } else {
        percent_decode_str(text).decode_utf8_lossy().into_owned()
    }
}

fn parse_query(query: &str) -> Vec<(String, String)> {
    query
        .split('&')
        .filter(|pair| !pair.is_empty())
        .map(|pair| {
            let (key, value) = pair.split_once('=').unwrap_or((pair, ""));
            (decode_component(key, true), decode_component(value, true))
        })
        .collect()
}

/// `application/x-www-form-urlencoded` pairs; invalid UTF-8 is an error (unlike query strings,
/// a form body is data the handler will store).
fn parse_form(body: &[u8]) -> Result<Vec<(String, String)>, String> {
    let text = std::str::from_utf8(body).map_err(|_| "Malformed form body".to_string())?;
    let decode = |part: &str| {
        percent_decode_str(&part.replace('+', " "))
            .decode_utf8()
            .map(|s| s.into_owned())
            .map_err(|_| "Malformed form body: invalid UTF-8".to_string())
    };
    text.split('&')
        .filter(|pair| !pair.is_empty())
        .map(|pair| {
            let (key, value) = pair.split_once('=').unwrap_or((pair, ""));
            Ok((decode(key)?, decode(value)?))
        })
        .collect()
}

fn unique_keys<'a>(items: impl Iterator<Item = &'a str>) -> Vec<&'a str> {
    let mut keys: Vec<&str> = Vec::new();
    for key in items {
        if !keys.contains(&key) {
            keys.push(key);
        }
    }
    keys
}

/// Raises `HTTPException(400, message)`.
fn bad_request(py: Python<'_>, message: String) -> PyErr {
    match types(py).and_then(|t| t.http_exception.bind(py).call1((400, message))) {
        Ok(exception) => PyErr::from_value(exception),
        Err(err) => err,
    }
}

/// An awaitable that is already complete; lets `body()`/`json()` keep an async signature
/// (the body is fully read before the handler runs) without a coroutine per call.
#[pyclass(module = "ferro._ferro", frozen)]
pub(crate) struct Ready {
    value: Mutex<Option<Py<PyAny>>>,
}

impl Ready {
    pub(crate) fn new(value: Py<PyAny>) -> Self {
        Self {
            value: Mutex::new(Some(value)),
        }
    }

    fn finish(&self, py: Python<'_>) -> PyErr {
        let value = self.value.lock().unwrap_or_else(|e| e.into_inner()).take();
        PyStopIteration::new_err((value.unwrap_or_else(|| py.None()),))
    }
}

#[pymethods]
impl Ready {
    fn __await__(slf: Py<Self>) -> Py<Self> {
        slf
    }

    fn __iter__(slf: Py<Self>) -> Py<Self> {
        slf
    }

    fn __next__(&self, py: Python<'_>) -> PyResult<Py<PyAny>> {
        Err(self.finish(py))
    }

    fn send(&self, py: Python<'_>, _value: &Bound<'_, PyAny>) -> PyResult<Py<PyAny>> {
        Err(self.finish(py))
    }
}

/// Multi-valued query parameters. `get` and `[]` return the last value.
#[pyclass(module = "ferro", frozen, sequence)]
pub(crate) struct QueryParams {
    items: Vec<(String, String)>,
}

#[pymethods]
impl QueryParams {
    #[pyo3(signature = (key, default=None))]
    fn get(&self, py: Python<'_>, key: &str, default: Option<Py<PyAny>>) -> Py<PyAny> {
        match self.items.iter().rev().find(|(k, _)| k == key) {
            Some((_, value)) => value
                .into_pyobject(py)
                .map(|v| v.into_any().unbind())
                .unwrap(),
            None => default.unwrap_or_else(|| py.None()),
        }
    }

    fn getlist(&self, key: &str) -> Vec<String> {
        self.items
            .iter()
            .filter(|(k, _)| k == key)
            .map(|(_, v)| v.clone())
            .collect()
    }

    fn items(&self) -> Vec<(String, String)> {
        self.items.clone()
    }

    fn keys(&self) -> Vec<String> {
        unique_keys(self.items.iter().map(|(k, _)| k.as_str()))
            .into_iter()
            .map(str::to_string)
            .collect()
    }

    fn __getitem__(&self, key: &str) -> PyResult<String> {
        self.items
            .iter()
            .rev()
            .find(|(k, _)| k == key)
            .map(|(_, v)| v.clone())
            .ok_or_else(|| PyKeyError::new_err(key.to_string()))
    }

    fn __contains__(&self, key: &str) -> bool {
        self.items.iter().any(|(k, _)| k == key)
    }

    fn __len__(&self) -> usize {
        unique_keys(self.items.iter().map(|(k, _)| k.as_str())).len()
    }

    fn __iter__<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyIterator>> {
        PyList::new(py, self.keys())?.try_iter()
    }

    fn __repr__(&self) -> String {
        format!("QueryParams({:?})", self.items)
    }
}

/// Case-insensitive request headers. `get` and `[]` return the first value.
#[pyclass(module = "ferro", frozen, sequence)]
pub(crate) struct Headers {
    /// Names are lower-case.
    items: Vec<(String, String)>,
}

impl Headers {
    fn first(&self, name: &str) -> Option<&str> {
        let name = name.to_ascii_lowercase();
        self.items
            .iter()
            .find(|(n, _)| *n == name)
            .map(|(_, v)| v.as_str())
    }
}

#[pymethods]
impl Headers {
    #[pyo3(signature = (name, default=None))]
    fn get(&self, py: Python<'_>, name: &str, default: Option<Py<PyAny>>) -> Py<PyAny> {
        match self.first(name) {
            Some(value) => value
                .into_pyobject(py)
                .map(|v| v.into_any().unbind())
                .unwrap(),
            None => default.unwrap_or_else(|| py.None()),
        }
    }

    fn getlist(&self, name: &str) -> Vec<String> {
        let name = name.to_ascii_lowercase();
        self.items
            .iter()
            .filter(|(n, _)| *n == name)
            .map(|(_, v)| v.clone())
            .collect()
    }

    fn items(&self) -> Vec<(String, String)> {
        self.items.clone()
    }

    fn keys(&self) -> Vec<String> {
        unique_keys(self.items.iter().map(|(k, _)| k.as_str()))
            .into_iter()
            .map(str::to_string)
            .collect()
    }

    fn __getitem__(&self, name: &str) -> PyResult<String> {
        self.first(name)
            .map(str::to_string)
            .ok_or_else(|| PyKeyError::new_err(name.to_string()))
    }

    fn __contains__(&self, name: &str) -> bool {
        self.first(name).is_some()
    }

    fn __len__(&self) -> usize {
        unique_keys(self.items.iter().map(|(k, _)| k.as_str())).len()
    }

    fn __iter__<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyIterator>> {
        PyList::new(py, self.keys())?.try_iter()
    }

    fn __repr__(&self) -> String {
        format!("Headers({:?})", self.items)
    }
}

#[pyclass(module = "ferro", name = "Request", frozen)]
pub(crate) struct Request {
    inner: FerroRequest,
    query: OnceLock<Py<QueryParams>>,
    headers: OnceLock<Py<Headers>>,
    state: OnceLock<Py<PyAny>>,
}

impl Request {
    pub(crate) fn new(inner: FerroRequest) -> Self {
        Self {
            inner,
            query: OnceLock::new(),
            headers: OnceLock::new(),
            state: OnceLock::new(),
        }
    }

    fn state_attribute(&self, py: Python<'_>, name: &str) -> PyResult<Py<PyAny>> {
        let state = self.state(py)?;
        state
            .bind(py)
            .getattr(name)
            .map(Bound::unbind)
            .map_err(|_| {
                pyo3::exceptions::PyAttributeError::new_err(format!(
                    "request.{name} needs the auth middleware: call app.add_auth()"
                ))
            })
    }

    fn header(&self, name: &str) -> Option<&str> {
        self.inner
            .headers
            .iter()
            .find(|(n, _)| n == name)
            .map(|(_, v)| v.as_str())
    }
}

#[pymethods]
impl Request {
    #[getter]
    fn method(&self) -> &str {
        &self.inner.method
    }

    /// The decoded path.
    #[getter]
    fn path(&self) -> String {
        decode_component(&self.inner.raw_path, false)
    }

    #[getter]
    fn raw_path(&self) -> &str {
        &self.inner.raw_path
    }

    #[getter]
    fn query_string(&self) -> &str {
        &self.inner.query_string
    }

    #[getter]
    fn query(&self, py: Python<'_>) -> PyResult<Py<QueryParams>> {
        if let Some(query) = self.query.get() {
            return Ok(query.clone_ref(py));
        }
        let query = Py::new(
            py,
            QueryParams {
                items: parse_query(&self.inner.query_string),
            },
        )?;
        Ok(self.query.get_or_init(|| query).clone_ref(py))
    }

    #[getter]
    fn path_params<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyDict>> {
        let params = PyDict::new(py);
        for (name, value) in &self.inner.path_params {
            params.set_item(name, value)?;
        }
        Ok(params)
    }

    #[getter]
    fn headers(&self, py: Python<'_>) -> PyResult<Py<Headers>> {
        if let Some(headers) = self.headers.get() {
            return Ok(headers.clone_ref(py));
        }
        let headers = Py::new(
            py,
            Headers {
                items: self.inner.headers.clone(),
            },
        )?;
        Ok(self.headers.get_or_init(|| headers).clone_ref(py))
    }

    /// Request cookies (`name -> value`; the last value wins for repeated names).
    #[getter]
    fn cookies<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, pyo3::types::PyDict>> {
        let dict = pyo3::types::PyDict::new(py);
        for (_, value) in self.inner.headers.iter().filter(|(n, _)| n == "cookie") {
            for (key, item) in ferro_core::security::cookies::parse_cookies(value) {
                dict.set_item(key, item)?;
            }
        }
        Ok(dict)
    }

    /// A per-request namespace for middleware and handlers (`request.state.user = ...`).
    #[getter]
    fn state(&self, py: Python<'_>) -> PyResult<Py<PyAny>> {
        if let Some(state) = self.state.get() {
            return Ok(state.clone_ref(py));
        }
        let namespace = py
            .import("types")?
            .getattr("SimpleNamespace")?
            .call0()?
            .unbind();
        Ok(self.state.get_or_init(|| namespace).clone_ref(py))
    }

    /// The current user, set by the auth middleware (`app.add_auth()`).
    #[getter]
    fn user(&self, py: Python<'_>) -> PyResult<Py<PyAny>> {
        self.state_attribute(py, "user")
    }

    /// The session data (a dict-like object), set by the auth middleware.
    #[getter]
    fn session(&self, py: Python<'_>) -> PyResult<Py<PyAny>> {
        self.state_attribute(py, "session")
    }

    #[getter]
    fn client(&self) -> Option<(String, u16)> {
        self.inner
            .client
            .map(|addr| (addr.ip().to_string(), addr.port()))
    }

    #[getter]
    fn request_id(&self) -> &str {
        &self.inner.request_id
    }

    /// `await request.body()` → the raw body as `bytes`.
    fn body(&self, py: Python<'_>) -> Ready {
        Ready::new(PyBytes::new(py, &self.inner.body).into_any().unbind())
    }

    /// `await request.form()` → the fields of an `application/x-www-form-urlencoded` body, as a
    /// mapping with `get`/`getlist` (repeated fields keep every value). Raises
    /// `HTTPException(400)` for other content types or malformed bodies.
    fn form(&self, py: Python<'_>) -> PyResult<Ready> {
        let media = self.header("content-type").map(|v| {
            v.split(';')
                .next()
                .unwrap_or("")
                .trim()
                .to_ascii_lowercase()
        });
        match media.as_deref() {
            Some("application/x-www-form-urlencoded") => {}
            None if self.inner.body.is_empty() => {}
            other => {
                return Err(bad_request(
                    py,
                    format!(
                        "Expected a form body (content-type application/x-www-form-urlencoded), got {}",
                        other.unwrap_or("no content type")
                    ),
                ));
            }
        }
        let items = parse_form(&self.inner.body).map_err(|message| bad_request(py, message))?;
        let form = Py::new(py, QueryParams { items })?;
        Ok(Ready::new(form.into_any()))
    }

    /// `await request.json()` → the parsed JSON body. Raises `HTTPException(400)` for a
    /// non-JSON content type, an empty body or invalid JSON.
    fn json(&self, py: Python<'_>) -> PyResult<Ready> {
        if let Some(content_type) = self.header("content-type") {
            let media = content_type
                .split(';')
                .next()
                .unwrap_or("")
                .trim()
                .to_ascii_lowercase();
            if media != "application/json" && !media.ends_with("+json") {
                return Err(bad_request(
                    py,
                    format!("Expected a JSON body (content-type application/json), got {media}"),
                ));
            }
        }
        if self.inner.body.is_empty() {
            return Err(bad_request(
                py,
                "Request body is empty; expected JSON".to_string(),
            ));
        }
        let value = json::decode(py, &self.inner.body)
            .map_err(|err| bad_request(py, format!("Malformed JSON body: {}", err.value(py))))?;
        Ok(Ready::new(value.unbind()))
    }

    fn __repr__(&self) -> String {
        format!("<Request {} {}>", self.inner.method, self.inner.raw_path)
    }
}
