//! One logging pipeline for Rust and Python (constitution: Logging).
//!
//! Only Ferro's own targets are enabled, so engine-internal log lines (which would name the
//! engine) never reach the developer (FR-029). Output is human-readable text or, with
//! `log_format="json"`, one JSON object per line (Feature 011).

use std::fmt;
use std::io::IsTerminal;
use std::sync::OnceLock;

use ferro_core::LogLevel;
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use serde_json::{Map, Value};
use tracing::field::{Field, Visit};
use tracing::{Event, Subscriber};
use tracing_subscriber::fmt::format::Writer;
use tracing_subscriber::fmt::time::{FormatTime, SystemTime};
use tracing_subscriber::fmt::{FmtContext, FormatEvent, FormatFields};
use tracing_subscriber::layer::SubscriberExt;
use tracing_subscriber::registry::LookupSpan;
use tracing_subscriber::util::SubscriberInitExt;
use tracing_subscriber::{EnvFilter, Registry, fmt as layers, reload};

static FILTER: OnceLock<reload::Handle<EnvFilter, Registry>> = OnceLock::new();

fn filter(level: LogLevel) -> EnvFilter {
    EnvFilter::new(format!("off,ferro={}", level.as_filter()))
}

/// Installs (or re-levels) the log pipeline writing to stderr. The format is chosen by the first
/// call in a process.
#[pyfunction]
#[pyo3(signature = (level, format="text"))]
pub(crate) fn init_logging(level: &str, format: &str) -> PyResult<()> {
    let level = LogLevel::parse(level)
        .ok_or_else(|| PyValueError::new_err(format!("invalid log level {level:?}")))?;
    let json = match format {
        "text" => false,
        "json" => true,
        other => {
            return Err(PyValueError::new_err(format!(
                "invalid log format {other:?}"
            )));
        }
    };
    if let Some(handle) = FILTER.get() {
        return handle
            .reload(filter(level))
            .map_err(|err| PyValueError::new_err(err.to_string()));
    }
    let (layer, handle) = reload::Layer::new(filter(level));
    let text = (!json).then(|| {
        layers::layer()
            .with_writer(std::io::stderr)
            .with_ansi(std::io::stderr().is_terminal())
    });
    let structured = json.then(|| {
        layers::layer()
            .with_writer(std::io::stderr)
            .with_ansi(false)
            .event_format(JsonLines)
    });
    let installed = Registry::default()
        .with(layer)
        .with(text)
        .with(structured)
        .try_init()
        .is_ok();
    if installed {
        let _ = FILTER.set(handle);
    }
    Ok(())
}

/// Emits a record from Python's `logging` (see `ferro.log.RustHandler`), with the id of the
/// request being handled when there is one.
#[pyfunction]
#[pyo3(signature = (level, logger, message, request_id=None))]
pub(crate) fn log(level: &str, logger: &str, message: &str, request_id: Option<&str>) {
    macro_rules! emit {
        ($macro:ident) => {
            match request_id {
                Some(id) => tracing::$macro!(
                    target: "ferro.python",
                    logger = %logger,
                    request_id = %id,
                    "{message}"
                ),
                None => tracing::$macro!(target: "ferro.python", logger = %logger, "{message}"),
            }
        };
    }
    match level {
        "DEBUG" => emit!(debug),
        "INFO" => emit!(info),
        "WARNING" => emit!(warn),
        "NOTSET" => emit!(trace),
        _ => emit!(error),
    }
}

/// One JSON object per event: `timestamp`, `level`, `target`, then every field (`message` when
/// the event has one). Numbers and booleans stay JSON numbers and booleans.
struct JsonLines;

impl<S, N> FormatEvent<S, N> for JsonLines
where
    S: Subscriber + for<'a> LookupSpan<'a>,
    N: for<'a> FormatFields<'a> + 'static,
{
    fn format_event(
        &self,
        _ctx: &FmtContext<'_, S, N>,
        mut writer: Writer<'_>,
        event: &Event<'_>,
    ) -> fmt::Result {
        let mut timestamp = String::new();
        SystemTime.format_time(&mut Writer::new(&mut timestamp))?;
        let metadata = event.metadata();
        let mut object = Map::new();
        object.insert("timestamp".into(), Value::String(timestamp));
        object.insert("level".into(), Value::String(metadata.level().to_string()));
        object.insert(
            "target".into(),
            Value::String(metadata.target().to_string()),
        );
        event.record(&mut JsonFields(&mut object));
        writeln!(writer, "{}", Value::Object(object))
    }
}

struct JsonFields<'a>(&'a mut Map<String, Value>);

impl Visit for JsonFields<'_> {
    fn record_debug(&mut self, field: &Field, value: &dyn fmt::Debug) {
        self.0
            .insert(field.name().into(), Value::String(format!("{value:?}")));
    }

    fn record_str(&mut self, field: &Field, value: &str) {
        self.0
            .insert(field.name().into(), Value::String(value.into()));
    }

    fn record_i64(&mut self, field: &Field, value: i64) {
        self.0.insert(field.name().into(), Value::from(value));
    }

    fn record_u64(&mut self, field: &Field, value: u64) {
        self.0.insert(field.name().into(), Value::from(value));
    }

    fn record_bool(&mut self, field: &Field, value: bool) {
        self.0.insert(field.name().into(), Value::Bool(value));
    }

    fn record_f64(&mut self, field: &Field, value: f64) {
        self.0.insert(field.name().into(), Value::from(value));
    }
}
