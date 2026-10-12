//! Engine-independent core types for Ferro.
//!
//! Nothing in this crate may depend on the HTTP engine, the async runtime or the Python bridge
//! (constitution principle II). Engine adapters convert to and from these types.

#![forbid(unsafe_code)]

pub mod dispatch;
pub mod envelope;
pub mod headers;
pub mod method;
pub mod metrics;
pub mod passwords;
pub mod request;
pub mod request_id;
pub mod response;
pub mod security;
pub mod settings;

pub use dispatch::{Dispatcher, LocalBoxFuture};
pub use envelope::{DebugInfo, ErrorDetail, ErrorEnvelope};
pub use method::Method;
pub use request::FerroRequest;
pub use response::FerroResponse;
pub use settings::{CorsSettings, LogLevel, Settings, SettingsError};
