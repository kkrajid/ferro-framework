//! Feature 011 US1: liveness and readiness probes answered by the engine.

mod common;

use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::{Arc, Mutex};
use std::time::Duration;

use common::{get, request};
use ferro_core::{Dispatcher, FerroRequest, FerroResponse, LocalBoxFuture, Method, Settings};
use ferro_http::{RunningServer, start};
use ferro_router::Router;
use serde_json::json;

#[derive(Default)]
struct ProbeDispatcher {
    dispatched: AtomicUsize,
    readiness_calls: AtomicUsize,
    failing: Mutex<Vec<&'static str>>,
}

impl Dispatcher for ProbeDispatcher {
    fn dispatch(&self, request: FerroRequest) -> LocalBoxFuture<FerroResponse> {
        self.dispatched.fetch_add(1, Ordering::SeqCst);
        Box::pin(async move {
            FerroResponse::new(200).with_body(format!("routed {}", request.raw_path))
        })
    }

    fn readiness(&self) -> LocalBoxFuture<Vec<&'static str>> {
        self.readiness_calls.fetch_add(1, Ordering::SeqCst);
        let failing = self.failing.lock().unwrap().clone();
        Box::pin(async move { failing })
    }
}

fn router() -> Router {
    let mut builder = Router::builder();
    builder.add(&[Method::Get], "/hello", 0).unwrap();
    builder.add(&[Method::Get], "/{page}", 1).unwrap();
    builder.build()
}

fn serve(settings: Settings) -> (RunningServer, Arc<ProbeDispatcher>) {
    let dispatcher = Arc::new(ProbeDispatcher::default());
    let settings = Settings {
        port: 0,
        workers: 1,
        ..settings
    };
    let server = start(settings, router(), dispatcher.clone()).expect("server starts");
    (server, dispatcher)
}

fn stop(server: RunningServer) {
    server.stop(true);
    assert!(server.wait(Some(Duration::from_secs(10))), "server stops");
}

#[test]
fn liveness_answers_without_dispatching() {
    let (server, dispatcher) = serve(Settings::default());
    let response = get(server.local_addr(), "/healthz");
    assert_eq!(response.status, 200);
    assert_eq!(response.json(), json!({"status": "ok"}));
    assert_eq!(response.header("content-type"), Some("application/json"));
    assert_eq!(response.header("cache-control"), Some("no-store"));
    assert!(response.header("x-request-id").is_some());
    assert!(
        response.header("x-content-type-options").is_some(),
        "security headers apply"
    );
    assert_eq!(dispatcher.dispatched.load(Ordering::SeqCst), 0);
    assert_eq!(dispatcher.readiness_calls.load(Ordering::SeqCst), 0);
    stop(server);
}

#[test]
fn readiness_follows_the_dispatcher_and_the_drain() {
    let (server, dispatcher) = serve(Settings::default());
    let addr = server.local_addr();

    let ready = get(addr, "/readyz");
    assert_eq!(ready.status, 200);
    assert_eq!(ready.json(), json!({"status": "ready"}));

    *dispatcher.failing.lock().unwrap() = vec!["database"];
    let failing = get(addr, "/readyz");
    assert_eq!(failing.status, 503);
    assert_eq!(failing.header("retry-after"), Some("1"));
    assert_eq!(
        failing.json(),
        json!({"status": "not ready", "failing": ["database"]})
    );

    server.begin_drain();
    let both = get(addr, "/readyz").json();
    assert_eq!(
        both,
        json!({"status": "not ready", "failing": ["server", "database"]})
    );
    let routed = get(addr, "/hello");
    assert_eq!(routed.status, 200, "draining keeps serving requests");
    assert_eq!(
        get(addr, "/healthz").status,
        200,
        "liveness is unaffected by the drain"
    );

    dispatcher.failing.lock().unwrap().clear();
    assert_eq!(
        get(addr, "/readyz").json(),
        json!({"status": "not ready", "failing": ["server"]})
    );
    assert_eq!(dispatcher.dispatched.load(Ordering::SeqCst), 1);
    stop(server);
}

#[test]
fn probes_answer_head_and_refuse_other_methods() {
    let (server, dispatcher) = serve(Settings::default());
    let addr = server.local_addr();
    let head = request(addr, "HEAD", "/healthz");
    assert_eq!(head.status, 200);
    assert!(head.body.is_empty());
    let post = request(addr, "POST", "/readyz");
    assert_eq!(post.status, 405);
    assert_eq!(post.header("allow"), Some("GET, HEAD"));
    assert_eq!(dispatcher.dispatched.load(Ordering::SeqCst), 0);
    stop(server);
}

#[test]
fn disabled_and_moved_probes() {
    let (server, dispatcher) = serve(Settings {
        health_path: String::new(),
        ready_path: "/-/ready".into(),
        ..Settings::default()
    });
    let addr = server.local_addr();
    let routed = get(addr, "/healthz");
    assert_eq!(routed.status, 200);
    assert_eq!(
        routed.text(),
        "routed /healthz",
        "a disabled probe's path is an ordinary path"
    );
    assert_eq!(get(addr, "/-/ready").json(), json!({"status": "ready"}));
    assert_eq!(get(addr, "/readyz").text(), "routed /readyz");
    assert_eq!(dispatcher.dispatched.load(Ordering::SeqCst), 2);
    stop(server);
}

#[test]
fn probes_bypass_rate_limits() {
    let (server, _) = serve(Settings {
        rate_limit: Some("1/minute".into()),
        ..Settings::default()
    });
    let addr = server.local_addr();
    for _ in 0..5 {
        assert_eq!(get(addr, "/healthz").status, 200);
        assert_eq!(get(addr, "/readyz").status, 200);
    }
    assert_eq!(get(addr, "/hello").status, 200);
    assert_eq!(get(addr, "/hello").status, 429, "routes stay rate limited");
    stop(server);
}
