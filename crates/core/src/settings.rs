//! Resolved server settings. Python resolves precedence (CLI > env > App > default); Rust
//! validates again before starting the engine.

use std::fmt;
use std::time::Duration;

/// Largest accepted request body limit (1 GiB).
pub const MAX_BODY_SIZE_LIMIT: usize = 1 << 30;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum LogLevel {
    Trace,
    Debug,
    Info,
    Warning,
    Error,
}

impl LogLevel {
    pub fn parse(value: &str) -> Option<Self> {
        match value.trim().to_ascii_lowercase().as_str() {
            "trace" => Some(Self::Trace),
            "debug" => Some(Self::Debug),
            "info" => Some(Self::Info),
            "warning" | "warn" => Some(Self::Warning),
            "error" => Some(Self::Error),
            _ => None,
        }
    }

    /// Name understood by log filters.
    pub fn as_filter(self) -> &'static str {
        match self {
            Self::Trace => "trace",
            Self::Debug => "debug",
            Self::Info => "info",
            Self::Warning => "warn",
            Self::Error => "error",
        }
    }
}

#[derive(Debug, Clone, PartialEq)]
pub struct Settings {
    pub host: String,
    pub port: u16,
    pub workers: usize,
    pub debug: bool,
    pub max_body_size: usize,
    pub request_timeout: Duration,
    pub shutdown_timeout: Duration,
    pub header_timeout: Duration,
    pub max_connections: usize,
    pub log_level: LogLevel,
    /// Refuse cross-origin unsafe requests (Feature 006).
    pub csrf: bool,
    pub trusted_origins: Vec<String>,
    pub trusted_proxies: Vec<String>,
    /// Global rate limit (`"100/minute"`), per client address.
    pub rate_limit: Option<String>,
    pub hsts: bool,
    pub hsts_max_age: u64,
    pub content_security_policy: Option<String>,
    pub cors: Option<CorsSettings>,
    /// Liveness probe address; empty disables it (Feature 011).
    pub health_path: String,
    /// Readiness probe address; empty disables it.
    pub ready_path: String,
    /// Serve metrics at `metrics_path` to clients in `metrics_allow`.
    pub metrics: bool,
    pub metrics_path: String,
    /// IP addresses or CIDR ranges allowed to read the metrics.
    pub metrics_allow: Vec<String>,
    /// PEM certificate chain (leaf first) and private key files. Set together, the listener
    /// speaks TLS and offers HTTP/2 (Feature 014).
    pub tls_cert: Option<String>,
    pub tls_key: Option<String>,
    /// Internal (Feature 015): the rate table shared by the worker processes of one server, and
    /// the folder where they publish their metrics. Set by the multi-process supervisor.
    pub shared_state: Option<String>,
    pub metrics_dir: Option<String>,
}

/// CORS configuration as given by the application.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CorsSettings {
    pub allow_origins: Vec<String>,
    pub allow_methods: Vec<String>,
    pub allow_headers: Vec<String>,
    pub expose_headers: Vec<String>,
    pub allow_credentials: bool,
    pub max_age: u32,
}

impl Default for Settings {
    fn default() -> Self {
        Self {
            host: "127.0.0.1".to_string(),
            port: 8000,
            workers: std::thread::available_parallelism().map_or(1, |n| n.get()),
            debug: false,
            max_body_size: 1024 * 1024,
            request_timeout: Duration::from_secs(30),
            shutdown_timeout: Duration::from_secs(30),
            header_timeout: Duration::from_secs(5),
            max_connections: 10_000,
            log_level: LogLevel::Info,
            csrf: true,
            trusted_origins: Vec::new(),
            trusted_proxies: Vec::new(),
            rate_limit: None,
            hsts: false,
            hsts_max_age: 31_536_000,
            content_security_policy: None,
            cors: None,
            health_path: "/healthz".to_string(),
            ready_path: "/readyz".to_string(),
            metrics: false,
            metrics_path: "/metrics".to_string(),
            metrics_allow: vec!["127.0.0.1".to_string(), "::1".to_string()],
            tls_cert: None,
            tls_key: None,
            shared_state: None,
            metrics_dir: None,
        }
    }
}

impl Settings {
    pub fn validate(&self) -> Result<(), SettingsError> {
        if self.host.trim().is_empty() || self.host.contains(char::is_whitespace) {
            return Err(SettingsError::new(
                "host",
                "must be a non-empty IP address or hostname",
            ));
        }
        check("workers", self.workers, 1, 1024)?;
        check("max_body_size", self.max_body_size, 1, MAX_BODY_SIZE_LIMIT)?;
        check_secs("request_timeout", self.request_timeout, 0.1, 3600.0)?;
        check_secs("shutdown_timeout", self.shutdown_timeout, 0.0, 3600.0)?;
        check_secs("header_timeout", self.header_timeout, 0.1, 300.0)?;
        check("max_connections", self.max_connections, 1, 1_000_000)?;
        self.validate_addresses()?;
        self.validate_tls()?;
        crate::security::Policy::new(self, Vec::new())?;
        Ok(())
    }

    /// Both TLS files or neither; the files themselves are read when the server starts.
    fn validate_tls(&self) -> Result<(), SettingsError> {
        for (name, value) in [("tls_cert", &self.tls_cert), ("tls_key", &self.tls_key)] {
            if value.as_deref().is_some_and(|path| path.trim().is_empty()) {
                return Err(SettingsError::new(name, "must be a file path"));
            }
        }
        match (&self.tls_cert, &self.tls_key) {
            (Some(_), None) => Err(SettingsError::new(
                "tls_key",
                "must be set together with tls_cert",
            )),
            (None, Some(_)) => Err(SettingsError::new(
                "tls_cert",
                "must be set together with tls_key",
            )),
            _ => Ok(()),
        }
    }

    pub fn tls(&self) -> bool {
        self.tls_cert.is_some() && self.tls_key.is_some()
    }

    /// Probe and metrics addresses (Feature 011): well-formed and distinct.
    fn validate_addresses(&self) -> Result<(), SettingsError> {
        check_path("health_path", &self.health_path)?;
        check_path("ready_path", &self.ready_path)?;
        if !self.ready_path.is_empty() && same_path(&self.ready_path, &self.health_path) {
            return Err(SettingsError::new(
                "ready_path",
                "must differ from health_path",
            ));
        }
        if self.metrics {
            if self.metrics_path.is_empty() {
                return Err(SettingsError::new(
                    "metrics_path",
                    "must not be empty when metrics are on",
                ));
            }
            check_path("metrics_path", &self.metrics_path)?;
            for (name, other) in [
                ("health_path", &self.health_path),
                ("ready_path", &self.ready_path),
            ] {
                if same_path(&self.metrics_path, other) {
                    return Err(SettingsError::new(
                        "metrics_path",
                        format!("must differ from {name}"),
                    ));
                }
            }
        }
        crate::security::proxy::TrustedProxies::parse(&self.metrics_allow)
            .map_err(|message| SettingsError::new("metrics_allow", message))?;
        Ok(())
    }
}

/// An absolute path without whitespace or route parameters; empty is allowed (disabled).
fn check_path(setting: &'static str, path: &str) -> Result<(), SettingsError> {
    if path.is_empty() {
        return Ok(());
    }
    if !path.starts_with('/')
        || path
            .chars()
            .any(|c| c.is_whitespace() || matches!(c, '{' | '}' | '?' | '#'))
    {
        return Err(SettingsError::new(
            setting,
            format!(
                "must be empty or a path starting with '/' (no spaces, braces, ? or #), got {path:?}"
            ),
        ));
    }
    Ok(())
}

/// Equal paths, ignoring a trailing slash; empty (disabled) paths never match.
pub fn same_path(a: &str, b: &str) -> bool {
    let trim = |p: &str| -> String {
        let trimmed = p.trim_end_matches('/');
        if trimmed.is_empty() {
            "/".to_string()
        } else {
            trimmed.to_string()
        }
    };
    !a.is_empty() && !b.is_empty() && trim(a) == trim(b)
}

fn check(setting: &'static str, value: usize, min: usize, max: usize) -> Result<(), SettingsError> {
    if (min..=max).contains(&value) {
        Ok(())
    } else {
        Err(SettingsError::new(
            setting,
            format!("must be between {min} and {max}, got {value}"),
        ))
    }
}

fn check_secs(
    setting: &'static str,
    value: Duration,
    min: f64,
    max: f64,
) -> Result<(), SettingsError> {
    let secs = value.as_secs_f64();
    if (min..=max).contains(&secs) {
        Ok(())
    } else {
        Err(SettingsError::new(
            setting,
            format!("must be between {min} and {max} seconds, got {secs}"),
        ))
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SettingsError {
    pub setting: &'static str,
    pub message: String,
}

impl SettingsError {
    pub fn new(setting: &'static str, message: impl Into<String>) -> Self {
        Self {
            setting,
            message: message.into(),
        }
    }
}

impl fmt::Display for SettingsError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "invalid setting `{}`: {}", self.setting, self.message)
    }
}

impl std::error::Error for SettingsError {}
