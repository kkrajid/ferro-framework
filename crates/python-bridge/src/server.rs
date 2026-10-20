//! `Server`: builds the router from Python route records and runs the engine.

use std::sync::{Arc, Mutex};
use std::time::Duration;

use ferro_core::security::ratelimit::RateSpec;
use ferro_core::security::{Policy, RouteOptions};
use ferro_core::{CorsSettings, LogLevel, Method, Settings};
use ferro_http::{RunningServer, StartError};
use ferro_router::{RouteError, Router};
use pyo3::exceptions::{PyOSError, PyRuntimeError, PyValueError};
use pyo3::prelude::*;
use pyo3::types::PyDict;

use crate::bridge::{Bridge, Endpoint, Executor};
use crate::dispatch::PyDispatcher;

/// Validates a route path; returns its parameter names.
#[pyfunction]
pub(crate) fn parse_path(path: &str) -> PyResult<Vec<String>> {
    ferro_router::parse_path(path)
        .map(|pattern| {
            pattern
                .param_names()
                .into_iter()
                .map(str::to_string)
                .collect()
        })
        .map_err(|err| PyValueError::new_err(err.to_string()))
}

/// `(methods, path, endpoint, name, rate_limit, csrf)`.
type RouteRecord = (
    Vec<String>,
    String,
    Py<PyAny>,
    String,
    Option<String>,
    bool,
    bool,
);

/// Everything `start` needs, built by `new`.
type Pending = (Router, Vec<RouteOptions>, Arc<PyDispatcher>);

#[pyclass(module = "ferro._ferro", frozen)]
pub(crate) struct Server {
    settings: Settings,
    pending: Mutex<Option<Pending>>,
    running: Mutex<Option<Arc<RunningServer>>>,
}

#[pymethods]
impl Server {
    #[new]
    fn new(
        py: Python<'_>,
        routes: Vec<RouteRecord>,
        settings: &Bound<'_, PyDict>,
        event_loop: Py<PyAny>,
    ) -> PyResult<Self> {
        let settings = settings_from_dict(settings)?;
        settings
            .validate()
            .map_err(|err| PyValueError::new_err(err.to_string()))?;
        ferro_http::check_tls(&settings).map_err(|err| PyValueError::new_err(err.to_string()))?;

        let mut builder = Router::builder();
        let mut endpoints: Vec<Endpoint> = Vec::with_capacity(routes.len());
        let mut options: Vec<RouteOptions> = Vec::with_capacity(routes.len());
        for (route_id, (methods, path, func, name, rate_limit, csrf, websocket)) in
            routes.into_iter().enumerate()
        {
            let rate_limit = rate_limit
                .as_deref()
                .map(RateSpec::parse)
                .transpose()
                .map_err(|err| PyValueError::new_err(format!("route {path} ({name}): {err}")))?;
            options.push(RouteOptions {
                csrf,
                rate_limit,
                websocket,
            });
            let methods: Vec<Method> = methods.iter().map(|m| Method::parse(m)).collect();
            builder.add(&methods, &path, route_id).map_err(|err| {
                let message = match &err {
                    RouteError::Duplicate {
                        existing_route_id, ..
                    } => format!(
                        "{err}: handler {name} conflicts with {}",
                        endpoints[*existing_route_id].name
                    ),
                    _ => format!("{err} (handler {name})"),
                };
                PyValueError::new_err(message)
            })?;
            endpoints.push(Endpoint { func, name });
        }
        Policy::new(&settings, options.clone())
            .map_err(|err| PyValueError::new_err(err.to_string()))?;
        let executor = Executor::new(event_loop, endpoints, settings.debug);
        let dispatcher = PyDispatcher {
            bridge: Bridge::new(py, executor)?,
            database_probe: Arc::default(),
        };
        Ok(Self {
            settings,
            pending: Mutex::new(Some((builder.build(), options, Arc::new(dispatcher)))),
            running: Mutex::new(None),
        })
    }

    /// Binds and starts serving; returns the bound `(host, port)`. With `listener` (a socket
    /// handle/descriptor detached from a Python socket), serves on that listening socket instead
    /// of binding: worker processes of `ferro run --processes N` share one socket.
    #[pyo3(signature = (listener=None))]
    fn start(&self, py: Python<'_>, listener: Option<u64>) -> PyResult<(String, u16)> {
        let (router, options, dispatcher) = self
            .pending
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .take()
            .ok_or_else(|| PyRuntimeError::new_err("server already started"))?;
        let settings = self.settings.clone();
        let adopted = listener.map(adopt_listener).transpose()?;
        let running = py
            .detach(move || match adopted {
                Some(listener) => {
                    ferro_http::start_on(settings, listener, router, options, dispatcher)
                }
                None => ferro_http::start_with(settings, router, options, dispatcher),
            })
            .map_err(|err| match err {
                StartError::Bind { .. } => PyOSError::new_err(err.to_string()),
                StartError::Settings(_) => PyValueError::new_err(err.to_string()),
                StartError::Engine(_) => PyRuntimeError::new_err(err.to_string()),
            })?;
        let addr = running.local_addr();
        *self.running.lock().unwrap_or_else(|e| e.into_inner()) = Some(Arc::new(running));
        Ok((addr.ip().to_string(), addr.port()))
    }

    /// Waits for the server to stop; returns `True` once stopped, `False` on timeout.
    #[pyo3(signature = (timeout=None))]
    fn wait(&self, py: Python<'_>, timeout: Option<f64>) -> PyResult<bool> {
        let running = self.running()?;
        let timeout = timeout
            .map(|t| Duration::try_from_secs_f64(t.max(0.0)))
            .transpose()
            .map_err(|err| PyValueError::new_err(err.to_string()))?;
        Ok(py.detach(move || running.wait(timeout)))
    }

    /// Readiness reports not ready from now on while requests are still served (drain).
    fn begin_drain(&self) -> PyResult<()> {
        self.running()?.begin_drain();
        Ok(())
    }

    /// Requests shutdown; returns immediately.
    #[pyo3(signature = (graceful=true))]
    fn stop(&self, py: Python<'_>, graceful: bool) -> PyResult<()> {
        let running = self.running()?;
        py.detach(move || running.stop(graceful));
        Ok(())
    }
}

impl Server {
    fn running(&self) -> PyResult<Arc<RunningServer>> {
        self.running
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .clone()
            .ok_or_else(|| PyRuntimeError::new_err("server not started"))
    }
}

/// Creates the state shared by the worker processes of one server (Feature 015) at `path`,
/// which must not exist yet.
#[pyfunction]
pub(crate) fn create_shared_state(path: std::path::PathBuf) -> PyResult<()> {
    ferro_shared::create(&path)
        .map_err(|err| PyOSError::new_err(format!("cannot create '{}': {err}", path.display())))
}

/// Reads and checks TLS certificate and key files (Feature 014); `ValueError` names the
/// setting and the file. The multi-process supervisor calls it before binding.
#[pyfunction]
#[pyo3(signature = (cert, key))]
pub(crate) fn check_tls(cert: Option<String>, key: Option<String>) -> PyResult<()> {
    let settings = Settings {
        tls_cert: cert,
        tls_key: key,
        ..Settings::default()
    };
    ferro_http::check_tls(&settings).map_err(|err| PyValueError::new_err(err.to_string()))
}

/// Takes ownership of a listening socket handed over by Python (`socket.detach()`).
#[cfg(windows)]
fn adopt_listener(handle: u64) -> PyResult<std::net::TcpListener> {
    use std::os::windows::io::FromRawSocket;
    // SAFETY: Python detached this socket (`socket.detach()` after `socket.fromshare`), so the
    // handle is a valid, open listening socket that nothing else owns or will close.
    Ok(unsafe { std::net::TcpListener::from_raw_socket(handle) })
}

/// Takes ownership of a listening socket handed over by Python (`socket.detach()`).
#[cfg(unix)]
fn adopt_listener(fd: u64) -> PyResult<std::net::TcpListener> {
    use std::os::unix::io::{FromRawFd, RawFd};
    let fd = RawFd::try_from(fd)
        .ok()
        .filter(|fd| *fd > 2)
        .ok_or_else(|| PyValueError::new_err(format!("invalid socket descriptor {fd}")))?;
    // SAFETY: Python detached this inherited descriptor (`socket.detach()`), so it is a valid,
    // open listening socket that nothing else owns or will close.
    Ok(unsafe { std::net::TcpListener::from_raw_fd(fd) })
}

fn settings_from_dict(values: &Bound<'_, PyDict>) -> PyResult<Settings> {
    let mut settings = Settings::default();
    for (key, value) in values.iter() {
        let key: String = key.extract()?;
        let invalid = |e: PyErr| PyValueError::new_err(format!("invalid setting `{key}`: {e}"));
        let seconds = |value: &Bound<'_, PyAny>| -> PyResult<Duration> {
            let secs: f64 = value.extract()?;
            Duration::try_from_secs_f64(secs).map_err(|e| PyValueError::new_err(e.to_string()))
        };
        match key.as_str() {
            "host" => settings.host = value.extract().map_err(invalid)?,
            "port" => settings.port = value.extract().map_err(invalid)?,
            "workers" => settings.workers = value.extract().map_err(invalid)?,
            "debug" => settings.debug = value.extract().map_err(invalid)?,
            "max_body_size" => settings.max_body_size = value.extract().map_err(invalid)?,
            "max_connections" => settings.max_connections = value.extract().map_err(invalid)?,
            "request_timeout" => settings.request_timeout = seconds(&value).map_err(invalid)?,
            "shutdown_timeout" => settings.shutdown_timeout = seconds(&value).map_err(invalid)?,
            "header_timeout" => settings.header_timeout = seconds(&value).map_err(invalid)?,
            "log_level" => {
                let level: String = value.extract().map_err(invalid)?;
                settings.log_level = LogLevel::parse(&level).ok_or_else(|| {
                    PyValueError::new_err(format!("invalid setting `log_level`: {level:?}"))
                })?;
            }
            "csrf" => settings.csrf = value.extract().map_err(invalid)?,
            "trusted_origins" => settings.trusted_origins = value.extract().map_err(invalid)?,
            "trusted_proxies" => settings.trusted_proxies = value.extract().map_err(invalid)?,
            "rate_limit" => settings.rate_limit = value.extract().map_err(invalid)?,
            "hsts" => settings.hsts = value.extract().map_err(invalid)?,
            "hsts_max_age" => settings.hsts_max_age = value.extract().map_err(invalid)?,
            "hsts_include_subdomains" => {
                settings.hsts_include_subdomains = value.extract().map_err(invalid)?;
            }
            "hsts_preload" => settings.hsts_preload = value.extract().map_err(invalid)?,
            "allowed_hosts" => settings.allowed_hosts = value.extract().map_err(invalid)?,
            "https_redirect" => settings.https_redirect = value.extract().map_err(invalid)?,
            "content_security_policy" => {
                settings.content_security_policy = value.extract().map_err(invalid)?;
            }
            "health_path" => settings.health_path = value.extract().map_err(invalid)?,
            "ready_path" => settings.ready_path = value.extract().map_err(invalid)?,
            "metrics" => settings.metrics = value.extract().map_err(invalid)?,
            "metrics_path" => settings.metrics_path = value.extract().map_err(invalid)?,
            "metrics_allow" => settings.metrics_allow = value.extract().map_err(invalid)?,
            "tls_cert" => settings.tls_cert = value.extract().map_err(invalid)?,
            "tls_key" => settings.tls_key = value.extract().map_err(invalid)?,
            "shared_state" => settings.shared_state = value.extract().map_err(invalid)?,
            "metrics_dir" => settings.metrics_dir = value.extract().map_err(invalid)?,
            "cors" => {
                settings.cors = if value.is_none() {
                    None
                } else {
                    let cors = value.cast::<PyDict>().map_err(|e| invalid(e.into()))?;
                    let get = |key: &str| -> PyResult<Bound<'_, PyAny>> {
                        cors.get_item(key)?.ok_or_else(|| {
                            PyValueError::new_err(format!("invalid setting `cors`: missing {key}"))
                        })
                    };
                    Some(CorsSettings {
                        allow_origins: get("allow_origins")?.extract()?,
                        allow_methods: get("allow_methods")?.extract()?,
                        allow_headers: get("allow_headers")?.extract()?,
                        expose_headers: get("expose_headers")?.extract()?,
                        allow_credentials: get("allow_credentials")?.extract()?,
                        max_age: get("max_age")?.extract()?,
                    })
                };
            }
            _ => return Err(PyValueError::new_err(format!("unknown setting `{key}`"))),
        }
    }
    Ok(settings)
}
