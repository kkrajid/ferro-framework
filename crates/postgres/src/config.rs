//! `postgresql://` URL parsing (contracts/database-url.md). Ferro consumes its own parameters and
//! hands the rest to the driver's parser. Nothing here ever formats the URL or password.

use std::fmt;
use std::path::PathBuf;
use std::time::Duration;

use ferro_database::DbError;
use tokio_postgres::config::{Host, SslMode};

const DEFAULT_POOL_SIZE: usize = 10;
const DEFAULT_QUEUE_SIZE: usize = 1024;
const SUPPORTED: &str = "sslmode, sslrootcert, pool_size, queue_size, connect_timeout, \
                         options, application_name, max_lifetime, max_idle";

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum TlsMode {
    Disable,
    Prefer,
    Require,
}

#[derive(Clone)]
pub struct PgConfig {
    pub pool_size: usize,
    /// Jobs that may wait for a connection; beyond it new jobs fail at once (Feature 011).
    pub queue_size: usize,
    pub tls: TlsMode,
    pub root_cert: Option<PathBuf>,
    /// A pooled connection older than this is replaced before its next job (Feature 021).
    pub max_lifetime: Option<Duration>,
    /// A pooled connection unused for this long is replaced before its next job.
    pub max_idle: Option<Duration>,
    pub(crate) driver: tokio_postgres::Config,
}

impl fmt::Debug for PgConfig {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("PgConfig")
            .field("target", &self.describe())
            .field("pool_size", &self.pool_size)
            .field("queue_size", &self.queue_size)
            .field("tls", &self.tls)
            .field("max_lifetime", &self.max_lifetime)
            .field("max_idle", &self.max_idle)
            .finish_non_exhaustive()
    }
}

fn config_error(message: impl Into<String>) -> DbError {
    DbError::Other {
        message: message.into(),
    }
}

/// Seconds (a positive number, fractions allowed) for `max_lifetime` / `max_idle`.
fn seconds(key: &str, value: &str) -> Result<Duration, DbError> {
    value
        .parse::<f64>()
        .ok()
        .filter(|s| s.is_finite() && *s > 0.0 && *s <= 1e9)
        .map(Duration::from_secs_f64)
        .ok_or_else(|| config_error(format!("invalid {key} '{value}': expected seconds above 0")))
}

fn percent_decode(text: &str) -> String {
    let bytes = text.as_bytes();
    let mut out = Vec::with_capacity(bytes.len());
    let mut i = 0;
    while i < bytes.len() {
        if bytes[i] == b'%'
            && i + 2 < bytes.len()
            && let Ok(hex) = std::str::from_utf8(&bytes[i + 1..i + 3])
            && let Ok(value) = u8::from_str_radix(hex, 16)
        {
            out.push(value);
            i += 3;
            continue;
        }
        out.push(bytes[i]);
        i += 1;
    }
    String::from_utf8_lossy(&out).into_owned()
}

impl PgConfig {
    pub fn from_url(url: &str) -> Result<Self, DbError> {
        if !(url.starts_with("postgresql://") || url.starts_with("postgres://")) {
            return Err(config_error(
                "invalid PostgreSQL URL: expected postgresql://user:password@host:port/database",
            ));
        }
        let (base, query) = url.split_once('?').unwrap_or((url, ""));
        let mut pool_size = DEFAULT_POOL_SIZE;
        let mut queue_size = DEFAULT_QUEUE_SIZE;
        let mut tls = TlsMode::Prefer;
        let mut root_cert = None;
        let mut max_lifetime = None;
        let mut max_idle = None;
        let mut passthrough = Vec::new();
        for pair in query.split('&').filter(|p| !p.is_empty()) {
            let (key, raw_value) = pair.split_once('=').unwrap_or((pair, ""));
            let value = percent_decode(raw_value);
            match key {
                "pool_size" => {
                    pool_size = value
                        .parse::<usize>()
                        .ok()
                        .filter(|n| (1..=1000).contains(n))
                        .ok_or_else(|| {
                            config_error(format!("invalid pool_size '{value}': expected 1 to 1000"))
                        })?;
                }
                "queue_size" => {
                    queue_size = value
                        .parse::<usize>()
                        .ok()
                        .filter(|n| (1..=1_000_000).contains(n))
                        .ok_or_else(|| {
                            config_error(format!(
                                "invalid queue_size '{value}': expected 1 to 1000000"
                            ))
                        })?;
                }
                "sslmode" => {
                    tls = match value.as_str() {
                        "disable" => TlsMode::Disable,
                        "prefer" => TlsMode::Prefer,
                        "require" | "verify-full" => TlsMode::Require,
                        other => {
                            return Err(config_error(format!(
                                "invalid sslmode '{other}': expected disable, prefer, require \
                                 or verify-full"
                            )));
                        }
                    };
                }
                "sslrootcert" => root_cert = Some(PathBuf::from(value)),
                "max_lifetime" => max_lifetime = Some(seconds(key, &value)?),
                "max_idle" => max_idle = Some(seconds(key, &value)?),
                "connect_timeout" | "options" | "application_name" => passthrough.push(pair),
                other => {
                    return Err(config_error(format!(
                        "unsupported PostgreSQL URL parameter '{other}'; supported: {SUPPORTED}"
                    )));
                }
            }
        }
        let rebuilt = if passthrough.is_empty() {
            base.to_string()
        } else {
            format!("{base}?{}", passthrough.join("&"))
        };
        let mut driver: tokio_postgres::Config = rebuilt
            .parse()
            .map_err(|err| config_error(format!("invalid PostgreSQL URL: {err}")))?;
        driver.ssl_mode(match tls {
            TlsMode::Disable => SslMode::Disable,
            TlsMode::Prefer => SslMode::Prefer,
            TlsMode::Require => SslMode::Require,
        });
        Ok(Self {
            pool_size,
            queue_size,
            tls,
            root_cert,
            max_lifetime,
            max_idle,
            driver,
        })
    }

    pub fn with_pool_size(mut self, pool_size: usize) -> Self {
        self.pool_size = pool_size.clamp(1, 1000);
        self
    }

    /// `host:port/database as user`, for messages. Never includes the password.
    pub fn describe(&self) -> String {
        let host = match self.driver.get_hosts().first() {
            Some(Host::Tcp(host)) => host.clone(),
            #[cfg(unix)]
            Some(Host::Unix(path)) => path.display().to_string(),
            None => "localhost".to_string(),
        };
        let port = self.driver.get_ports().first().copied().unwrap_or(5432);
        let user = self.driver.get_user();
        let database = self.driver.get_dbname().or(user).unwrap_or("postgres");
        match user {
            Some(user) => format!("{host}:{port}/{database} as {user}"),
            None => format!("{host}:{port}/{database}"),
        }
    }
}
