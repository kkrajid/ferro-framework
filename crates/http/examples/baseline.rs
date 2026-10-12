//! Rust-only baseline: the same hello-world response without Python, through the same adapter.
//! Used to measure the ceiling the Python path is compared against (constitution VIII).
//!
//! `cargo run --release -p ferro-http --example baseline -- 8079 4`

use std::sync::Arc;

use ferro_core::{
    Dispatcher, FerroRequest, FerroResponse, LocalBoxFuture, LogLevel, Method, Settings,
};
use ferro_router::Router;

struct Hello;

impl Dispatcher for Hello {
    fn dispatch(&self, _request: FerroRequest) -> LocalBoxFuture<FerroResponse> {
        Box::pin(async { FerroResponse::json(200, &br#"{"message":"Hello from Rust"}"#[..]) })
    }
}

fn main() {
    let mut args = std::env::args().skip(1);
    let port = args.next().and_then(|p| p.parse().ok()).unwrap_or(8079);
    let workers = args.next().and_then(|w| w.parse().ok()).unwrap_or(4);
    let mut builder = Router::builder();
    builder.add(&[Method::Get], "/", 0).expect("route");
    let settings = Settings {
        port,
        workers,
        log_level: LogLevel::Error,
        ..Settings::default()
    };
    let server = ferro_http::start(settings, builder.build(), Arc::new(Hello)).expect("start");
    eprintln!("baseline listening on {}", server.local_addr());
    server.wait(None);
}
