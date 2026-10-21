//! Ferro's HTTP engine adapter.
//!
//! This is the only crate that knows which HTTP engine Ferro runs on (constitution principle II).
//! Its public surface uses Ferro types only: [`Settings`], [`Router`], [`Dispatcher`],
//! [`RunningServer`] and [`StartError`].

#![forbid(unsafe_code)]

mod lifecycle;
mod metrics;
mod server;
mod service;
mod snapshots;
mod socket;
mod spool;
mod tls;

pub use lifecycle::ShutdownStats;
pub use server::{RunningServer, StartError, start, start_on, start_with};

/// Reads and checks the TLS files named by the settings without starting anything, so
/// configuration mistakes surface before a socket is bound (Feature 014).
pub fn check_tls(settings: &Settings) -> Result<(), ferro_core::SettingsError> {
    tls::server_config(settings).map(|_| ())
}

#[doc(no_inline)]
pub use ferro_core::{Dispatcher, Settings};
#[doc(no_inline)]
pub use ferro_router::Router;
