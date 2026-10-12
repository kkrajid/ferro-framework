//! Feature 017: regressions for the security review's engine findings.

mod common;

use std::sync::Arc;
use std::time::Duration;

use common::send;
use ferro_core::{Dispatcher, FerroRequest, FerroResponse, LocalBoxFuture, Method, Settings};
use ferro_http::{RunningServer, start};
use ferro_router::Router;

struct Client;

impl Dispatcher for Client {
    fn dispatch(&self, request: FerroRequest) -> LocalBoxFuture<FerroResponse> {
        Box::pin(async move {
            let client = request
                .client
                .map(|c| c.ip().to_string())
                .unwrap_or_default();
            FerroResponse::new(200).with_body(client)
        })
    }
}

fn serve(settings: Settings) -> RunningServer {
    let mut builder = Router::builder();
    builder.add(&[Method::Get], "/whoami", 0).unwrap();
    builder.add(&[Method::Get], "/{slug}", 1).unwrap();
    let settings = Settings {
        port: 0,
        workers: 1,
        ..settings
    };
    start(settings, builder.build(), Arc::new(Client)).expect("server starts")
}

fn stop(server: RunningServer) {
    server.stop(true);
    assert!(server.wait(Some(Duration::from_secs(10))));
}

#[test]
fn a_client_cannot_hide_behind_its_own_forwarded_for_line() {
    // The proxy at 127.0.0.1 is trusted; it added its own header line after the client's.
    let server = serve(Settings {
        trusted_proxies: vec!["127.0.0.1".into()],
        ..Settings::default()
    });
    let response = send(
        server.local_addr(),
        b"GET /whoami HTTP/1.1\r\nHost: t\r\nX-Forwarded-For: 10.9.9.9\r\n\
          X-Forwarded-For: 203.0.113.5\r\nConnection: close\r\n\r\n",
    );
    assert_eq!(response.status, 200);
    assert_eq!(response.text(), "203.0.113.5");
    stop(server);
}

#[test]
fn backslash_paths_never_redirect_off_site() {
    let server = serve(Settings::default());
    let response = send(
        server.local_addr(),
        b"GET /\\evil.example/ HTTP/1.1\r\nHost: t\r\nConnection: close\r\n\r\n",
    );
    assert!(
        response
            .header("location")
            .is_none_or(|l| !l.starts_with("/\\")),
        "{} {:?}",
        response.status,
        response.header("location")
    );
    stop(server);
}
