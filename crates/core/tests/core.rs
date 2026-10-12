use std::time::Duration;

use ferro_core::envelope::{DebugInfo, ErrorDetail, ErrorEnvelope, reason_phrase};
use ferro_core::headers::{is_valid_header_name, is_valid_header_value, security_headers};
use ferro_core::request_id;
use ferro_core::{FerroResponse, LogLevel, Method, Settings};
use serde_json::{Value, json};

fn body_json(response: &FerroResponse) -> Value {
    serde_json::from_slice(&response.body).expect("body is JSON")
}

#[test]
fn default_settings_are_safe_and_valid() {
    let settings = Settings::default();
    assert_eq!(settings.host, "127.0.0.1");
    assert_eq!(settings.port, 8000);
    assert!(settings.workers >= 1);
    assert!(!settings.debug);
    assert_eq!(settings.max_body_size, 1024 * 1024);
    assert_eq!(settings.request_timeout, Duration::from_secs(30));
    assert_eq!(settings.shutdown_timeout, Duration::from_secs(30));
    assert_eq!(settings.header_timeout, Duration::from_secs(5));
    assert_eq!(settings.max_connections, 10_000);
    assert_eq!(settings.log_level, LogLevel::Info);
    settings.validate().expect("defaults validate");
}

#[test]
fn settings_validation_names_the_setting() {
    let cases: Vec<(&str, Settings)> = vec![
        (
            "workers",
            Settings {
                workers: 0,
                ..Settings::default()
            },
        ),
        (
            "workers",
            Settings {
                workers: 5000,
                ..Settings::default()
            },
        ),
        (
            "max_body_size",
            Settings {
                max_body_size: 0,
                ..Settings::default()
            },
        ),
        (
            "request_timeout",
            Settings {
                request_timeout: Duration::from_millis(50),
                ..Settings::default()
            },
        ),
        (
            "header_timeout",
            Settings {
                header_timeout: Duration::from_secs(301),
                ..Settings::default()
            },
        ),
        (
            "max_connections",
            Settings {
                max_connections: 0,
                ..Settings::default()
            },
        ),
        (
            "host",
            Settings {
                host: String::new(),
                ..Settings::default()
            },
        ),
    ];
    for (name, settings) in cases {
        let err = settings.validate().expect_err(name);
        assert_eq!(err.setting, name);
        assert!(err.to_string().contains(name), "{err}");
    }
}

#[test]
fn probe_and_metrics_settings_validate() {
    let defaults = Settings::default();
    assert_eq!(defaults.health_path, "/healthz");
    assert_eq!(defaults.ready_path, "/readyz");
    assert!(!defaults.metrics);
    assert_eq!(defaults.metrics_path, "/metrics");
    assert_eq!(defaults.metrics_allow, vec!["127.0.0.1", "::1"]);
    let cases: Vec<(&str, Settings)> = vec![
        (
            "health_path",
            Settings {
                health_path: "healthz".into(),
                ..Settings::default()
            },
        ),
        (
            "ready_path",
            Settings {
                ready_path: "/a b".into(),
                ..Settings::default()
            },
        ),
        (
            "ready_path",
            Settings {
                ready_path: "/healthz".into(),
                ..Settings::default()
            },
        ),
        (
            "metrics_path",
            Settings {
                metrics: true,
                metrics_path: "/readyz".into(),
                ..Settings::default()
            },
        ),
        (
            "metrics_path",
            Settings {
                metrics: true,
                metrics_path: String::new(),
                ..Settings::default()
            },
        ),
        (
            "metrics_allow",
            Settings {
                metrics_allow: vec!["nope".into()],
                ..Settings::default()
            },
        ),
    ];
    for (name, settings) in cases {
        let err = settings.validate().expect_err(name);
        assert_eq!(err.setting, name);
    }
    Settings {
        health_path: String::new(),
        ready_path: String::new(),
        metrics_path: "/readyz".into(),
        ..Settings::default()
    }
    .validate()
    .expect("disabled probes and an unused metrics path are valid");
}

#[test]
fn log_level_parsing() {
    assert_eq!(LogLevel::parse("warning"), Some(LogLevel::Warning));
    assert_eq!(LogLevel::parse("WARN"), Some(LogLevel::Warning));
    assert_eq!(LogLevel::parse("trace"), Some(LogLevel::Trace));
    assert_eq!(LogLevel::parse("verbose"), None);
    assert_eq!(LogLevel::Error.as_filter(), "error");
}

#[test]
fn methods_parse_case_sensitively() {
    assert_eq!(Method::parse("GET"), Method::Get);
    assert_eq!(Method::parse("PATCH"), Method::Patch);
    assert_eq!(Method::parse("get"), Method::Other);
    assert_eq!(Method::parse("PROPFIND"), Method::Other);
    assert_eq!(Method::Delete.as_str(), "DELETE");
}

#[test]
fn request_id_is_propagated_when_valid() {
    assert_eq!(
        request_id::accept_or_generate(Some("abc-123_X.y")),
        "abc-123_X.y"
    );
}

#[test]
fn request_id_is_generated_when_missing_or_invalid() {
    let long = "a".repeat(129);
    for incoming in [
        None,
        Some(""),
        Some("bad id!"),
        Some("x\ny"),
        Some(long.as_str()),
    ] {
        let id = request_id::accept_or_generate(incoming);
        assert_eq!(id.len(), 32, "{incoming:?} -> {id}");
        assert!(id.chars().all(|c| c.is_ascii_hexdigit()));
    }
    assert_ne!(request_id::generate(), request_id::generate());
}

#[test]
fn envelope_minimal_shape() {
    let envelope = ErrorEnvelope::new(404, "rid-1");
    let value: Value = serde_json::from_slice(&envelope.to_json()).unwrap();
    assert_eq!(
        value,
        json!({"error": {"status": 404, "message": "Not Found", "request_id": "rid-1"}})
    );
}

#[test]
fn envelope_with_details_and_debug() {
    let envelope = ErrorEnvelope::new(422, "rid")
        .with_message("Validation failed")
        .with_details(vec![ErrorDetail {
            param: "id".into(),
            location: "path".into(),
            problem: "expected int".into(),
        }]);
    let value: Value = serde_json::from_slice(&envelope.to_json()).unwrap();
    assert_eq!(value["error"]["message"], "Validation failed");
    assert_eq!(value["error"]["details"][0]["param"], "id");
    assert_eq!(value["error"]["details"][0]["location"], "path");

    let debug = ErrorEnvelope::new(500, "rid").with_debug(DebugInfo {
        type_name: "ValueError".into(),
        exception: "boom".into(),
        traceback: vec!["line 1".into()],
    });
    let value: Value = serde_json::from_slice(&debug.to_json()).unwrap();
    assert_eq!(value["error"]["message"], "Internal Server Error");
    assert_eq!(value["error"]["type"], "ValueError");
    assert_eq!(value["error"]["exception"], "boom");
    assert_eq!(value["error"]["traceback"][0], "line 1");
}

#[test]
fn envelope_becomes_json_response() {
    let response = ErrorEnvelope::new(405, "rid").into_response();
    assert_eq!(response.status, 405);
    assert_eq!(response.header("content-type"), Some("application/json"));
    assert_eq!(body_json(&response)["error"]["status"], 405);
}

#[test]
fn reason_phrases() {
    assert_eq!(reason_phrase(200), "OK");
    assert_eq!(reason_phrase(413), "Content Too Large");
    assert_eq!(reason_phrase(503), "Service Unavailable");
    assert_eq!(reason_phrase(299), "Unknown Status");
}

#[test]
fn security_header_set() {
    let headers = security_headers();
    assert!(headers.contains(&("x-content-type-options", "nosniff")));
    assert!(headers.contains(&("referrer-policy", "strict-origin-when-cross-origin")));
    assert!(headers.iter().all(|(name, _)| *name != "server"));
}

#[test]
fn header_validation() {
    assert!(is_valid_header_name("X-Custom-Header"));
    assert!(!is_valid_header_name("bad header"));
    assert!(!is_valid_header_name(""));
    assert!(!is_valid_header_name("bad:name"));
    assert!(is_valid_header_value("text/plain; charset=utf-8"));
    assert!(!is_valid_header_value("a\r\nset-cookie: x"));
    assert!(!is_valid_header_value("nul\0"));
}

#[test]
fn response_helpers() {
    let response = FerroResponse::new(201)
        .with_header("location", "/products/1")
        .with_body(b"{}".to_vec());
    assert_eq!(response.status, 201);
    assert_eq!(response.header("Location"), Some("/products/1"));
    assert_eq!(&response.body[..], b"{}");
    assert!(FerroResponse::new(204).body.is_empty());
}
