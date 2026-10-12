//! Feature 011 US5: opt-in metrics in the text exposition format.

mod common;

use std::sync::Arc;
use std::time::Duration;

use common::{get, request};
use ferro_core::{Dispatcher, FerroRequest, FerroResponse, LocalBoxFuture, Method, Settings};
use ferro_http::{RunningServer, start};
use ferro_router::Router;

struct MetricsDispatcher;

impl Dispatcher for MetricsDispatcher {
    fn dispatch(&self, request: FerroRequest) -> LocalBoxFuture<FerroResponse> {
        Box::pin(async move {
            match request.route_id {
                2 => FerroResponse::new(500),
                _ => FerroResponse::new(200).with_body("ok"),
            }
        })
    }

    fn write_metrics(&self, out: &mut String) {
        out.push_str("# TYPE test_extra gauge\ntest_extra 7\n");
    }
}

fn router() -> Router {
    let mut builder = Router::builder();
    builder.add(&[Method::Get], "/items/{id}", 0).unwrap();
    builder.add(&[Method::Post], "/items", 1).unwrap();
    builder.add(&[Method::Get], "/boom", 2).unwrap();
    builder.build()
}

fn serve(settings: Settings) -> RunningServer {
    let settings = Settings {
        port: 0,
        workers: 1,
        ..settings
    };
    start(settings, router(), Arc::new(MetricsDispatcher)).expect("server starts")
}

fn stop(server: RunningServer) {
    server.stop(true);
    assert!(server.wait(Some(Duration::from_secs(10))));
}

fn value(text: &str, series: &str) -> Option<f64> {
    text.lines()
        .find_map(|line| line.strip_prefix(series)?.strip_prefix(' '))
        .map(|v| v.trim().parse().unwrap())
}

#[test]
fn metrics_are_off_by_default() {
    let server = serve(Settings::default());
    let response = get(server.local_addr(), "/metrics");
    assert_eq!(response.status, 404);
    stop(server);
}

#[test]
fn requests_are_counted_by_route_template_method_and_status_class() {
    let server = serve(Settings {
        metrics: true,
        ..Settings::default()
    });
    let addr = server.local_addr();
    for id in 1..=3 {
        assert_eq!(get(addr, &format!("/items/{id}")).status, 200);
    }
    assert_eq!(request(addr, "POST", "/items").status, 200);
    assert_eq!(get(addr, "/boom").status, 500);
    assert_eq!(get(addr, "/nope").status, 404);
    assert_eq!(get(addr, "/also-nope?x=1").status, 404);
    assert_eq!(get(addr, "/healthz").status, 200);

    let response = get(addr, "/metrics");
    assert_eq!(response.status, 200);
    assert_eq!(
        response.header("content-type"),
        Some("text/plain; version=0.0.4; charset=utf-8")
    );
    let text = response.text();
    let total = "ferro_http_requests_total";
    assert_eq!(
        value(
            &text,
            &format!(r#"{total}{{route="/items/{{id}}",method="GET",status="2xx"}}"#)
        ),
        Some(3.0)
    );
    assert_eq!(
        value(
            &text,
            &format!(r#"{total}{{route="/items",method="POST",status="2xx"}}"#)
        ),
        Some(1.0)
    );
    assert_eq!(
        value(
            &text,
            &format!(r#"{total}{{route="/boom",method="GET",status="5xx"}}"#)
        ),
        Some(1.0)
    );
    assert_eq!(
        value(
            &text,
            &format!(r#"{total}{{route="<unmatched>",method="GET",status="4xx"}}"#)
        ),
        Some(2.0),
        "unknown paths share one label: {text}"
    );
    assert!(!text.contains("/nope") && !text.contains("/healthz") && !text.contains("/metrics\""));

    let duration = "ferro_http_request_duration_seconds";
    let labels = r#"route="/items/{id}",method="GET""#;
    assert_eq!(
        value(&text, &format!("{duration}_count{{{labels}}}")),
        Some(3.0)
    );
    assert_eq!(
        value(
            &text,
            &format!(r#"{duration}_bucket{{{labels},le="+Inf"}}"#)
        ),
        Some(3.0)
    );
    let buckets: Vec<f64> = text
        .lines()
        .filter(|l| l.starts_with(&format!("{duration}_bucket{{{labels}")))
        .map(|l| l.rsplit(' ').next().unwrap().parse().unwrap())
        .collect();
    assert_eq!(buckets.len(), 14);
    assert!(
        buckets.windows(2).all(|w| w[0] <= w[1]),
        "cumulative: {buckets:?}"
    );

    assert_eq!(value(&text, "ferro_http_requests_in_flight"), Some(0.0));
    assert!(text.contains("ferro_build_info{version=\""));
    assert!(value(&text, "ferro_process_start_time_seconds").unwrap() > 1.7e9);
    assert_eq!(
        value(&text, "test_extra"),
        Some(7.0),
        "dispatcher series are included"
    );
    stop(server);
}

#[test]
fn metrics_are_refused_to_clients_outside_the_allow_list() {
    let server = serve(Settings {
        metrics: true,
        metrics_allow: vec!["10.0.0.0/8".into()],
        ..Settings::default()
    });
    let response = get(server.local_addr(), "/metrics");
    assert_eq!(response.status, 404);
    assert!(!response.text().contains("ferro_"));
    stop(server);
}

#[test]
fn metrics_path_is_configurable() {
    let server = serve(Settings {
        metrics: true,
        metrics_path: "/-/metrics".into(),
        ..Settings::default()
    });
    let addr = server.local_addr();
    assert_eq!(get(addr, "/-/metrics").status, 200);
    assert_eq!(get(addr, "/metrics").status, 404);
    stop(server);
}
