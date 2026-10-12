//! End-to-end tests of the HTTP adapter with a Rust-only dispatcher and a raw TCP client.

use std::io::{ErrorKind, Read, Write};
use std::net::{SocketAddr, TcpStream};
use std::sync::Arc;
use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};
use std::thread;
use std::time::{Duration, Instant};

use ferro_core::{Dispatcher, FerroRequest, FerroResponse, LocalBoxFuture, Method, Settings};
use ferro_http::{RunningServer, StartError, start};
use ferro_router::Router;

const HELLO: usize = 0;
const SLOW: usize = 1;
const HANG: usize = 2;
const UPLOAD: usize = 3;
const HEADERS: usize = 4;
const SHORT: usize = 5;

#[derive(Default)]
struct TestDispatcher {
    calls: AtomicUsize,
    hang_dropped: Arc<AtomicBool>,
}

struct DropFlag(Arc<AtomicBool>);

impl Drop for DropFlag {
    fn drop(&mut self) {
        self.0.store(true, Ordering::SeqCst);
    }
}

impl Dispatcher for TestDispatcher {
    fn dispatch(&self, request: FerroRequest) -> LocalBoxFuture<FerroResponse> {
        self.calls.fetch_add(1, Ordering::SeqCst);
        let flag = DropFlag(self.hang_dropped.clone());
        Box::pin(async move {
            match request.route_id {
                HELLO => {
                    let params: Vec<String> = request
                        .path_params
                        .iter()
                        .map(|(k, v)| format!("{k}={v}"))
                        .collect();
                    FerroResponse::json(
                        200,
                        format!(
                            r#"{{"method":"{}","params":"{}","query":"{}"}}"#,
                            request.method,
                            params.join(","),
                            request.query_string
                        ),
                    )
                }
                SLOW => {
                    tokio::time::sleep(Duration::from_millis(400)).await;
                    FerroResponse::new(200).with_body("slow done")
                }
                HANG => {
                    let _flag = flag;
                    tokio::time::sleep(Duration::from_secs(30)).await;
                    FerroResponse::new(200)
                }
                UPLOAD => FerroResponse::new(200).with_body(request.body.len().to_string()),
                HEADERS => FerroResponse::new(200)
                    .with_header("referrer-policy", "no-referrer")
                    .with_header("x-request-id", "spoofed")
                    .with_header("set-cookie", "a=1")
                    .with_header("set-cookie", "b=2"),
                SHORT => {
                    tokio::time::sleep(Duration::from_millis(600)).await;
                    FerroResponse::new(200).with_body(request.body.len().to_string())
                }
                _ => FerroResponse::new(500),
            }
        })
    }
}

fn test_router() -> Router {
    let mut builder = Router::builder();
    builder.add(&[Method::Get], "/hello", HELLO).unwrap();
    builder.add(&[Method::Get], "/slow", SLOW).unwrap();
    builder.add(&[Method::Get], "/hang", HANG).unwrap();
    builder.add(&[Method::Post], "/upload", UPLOAD).unwrap();
    builder.add(&[Method::Get], "/headers", HEADERS).unwrap();
    builder.add(&[Method::Get], "/items/{id}", HELLO).unwrap();
    builder.add(&[Method::Post], "/short", SHORT).unwrap();
    builder.build()
}

fn settings() -> Settings {
    Settings {
        port: 0,
        workers: 2,
        request_timeout: Duration::from_millis(300),
        shutdown_timeout: Duration::from_secs(5),
        max_body_size: 64,
        ..Settings::default()
    }
}

fn serve(settings: Settings) -> (RunningServer, Arc<TestDispatcher>) {
    let dispatcher = Arc::new(TestDispatcher::default());
    let server = start(settings, test_router(), dispatcher.clone()).expect("server starts");
    (server, dispatcher)
}

#[derive(Debug)]
struct Raw {
    status: u16,
    headers: Vec<(String, String)>,
    body: Vec<u8>,
}

impl Raw {
    fn header(&self, name: &str) -> Option<&str> {
        self.headers
            .iter()
            .find(|(n, _)| n.eq_ignore_ascii_case(name))
            .map(|(_, v)| v.as_str())
    }

    fn headers_named(&self, name: &str) -> Vec<&str> {
        self.headers
            .iter()
            .filter(|(n, _)| n.eq_ignore_ascii_case(name))
            .map(|(_, v)| v.as_str())
            .collect()
    }

    fn text(&self) -> String {
        String::from_utf8_lossy(&self.body).into_owned()
    }
}

fn parse(raw: &[u8]) -> Raw {
    let text = raw;
    let split = text
        .windows(4)
        .position(|w| w == b"\r\n\r\n")
        .expect("complete head");
    let head = String::from_utf8_lossy(&text[..split]).into_owned();
    let body = text[split + 4..].to_vec();
    let mut lines = head.split("\r\n");
    let status_line = lines.next().unwrap();
    let status = status_line.split(' ').nth(1).unwrap().parse().unwrap();
    let headers = lines
        .filter_map(|l| l.split_once(':'))
        .map(|(n, v)| (n.trim().to_ascii_lowercase(), v.trim().to_string()))
        .collect();
    Raw {
        status,
        headers,
        body,
    }
}

fn send(addr: SocketAddr, request: &[u8]) -> Raw {
    let mut stream = TcpStream::connect(addr).unwrap();
    stream
        .set_read_timeout(Some(Duration::from_secs(10)))
        .unwrap();
    stream.write_all(request).unwrap();
    let mut buf = Vec::new();
    match stream.read_to_end(&mut buf) {
        Ok(_) => {}
        Err(e) if e.kind() == ErrorKind::ConnectionReset && !buf.is_empty() => {}
        Err(e) => panic!("read failed: {e}"),
    }
    parse(&buf)
}

fn get(addr: SocketAddr, path: &str) -> Raw {
    send(
        addr,
        format!("GET {path} HTTP/1.1\r\nHost: test\r\nConnection: close\r\n\r\n").as_bytes(),
    )
}

fn json(raw: &Raw) -> serde_json::Value {
    serde_json::from_slice(&raw.body).expect("JSON body")
}

fn stop(server: RunningServer) {
    server.stop(true);
    assert!(server.wait(Some(Duration::from_secs(10))), "server stops");
}

#[test]
fn hello_has_security_headers_and_request_id() {
    let (server, _) = serve(settings());
    let response = get(server.local_addr(), "/hello?x=1");
    assert_eq!(response.status, 200);
    assert_eq!(response.header("content-type"), Some("application/json"));
    assert_eq!(json(&response)["method"], "GET");
    assert_eq!(json(&response)["query"], "x=1");
    assert_eq!(response.header("x-content-type-options"), Some("nosniff"));
    assert_eq!(
        response.header("referrer-policy"),
        Some("strict-origin-when-cross-origin")
    );
    assert!(response.header("server").is_none());
    let id = response.header("x-request-id").unwrap();
    assert_eq!(id.len(), 32);
    stop(server);
}

#[test]
fn valid_request_id_is_propagated() {
    let (server, _) = serve(settings());
    let response = send(
        server.local_addr(),
        b"GET /hello HTTP/1.1\r\nHost: t\r\nx-request-id: trace-42\r\nConnection: close\r\n\r\n",
    );
    assert_eq!(response.header("x-request-id"), Some("trace-42"));
    stop(server);
}

#[test]
fn path_params_are_passed_to_dispatcher() {
    let (server, _) = serve(settings());
    let response = get(server.local_addr(), "/items/a%20b");
    assert_eq!(json(&response)["params"], "id=a b");
    stop(server);
}

#[test]
fn handler_headers_override_defaults_but_not_request_id() {
    let (server, _) = serve(settings());
    let response = get(server.local_addr(), "/headers");
    assert_eq!(response.header("referrer-policy"), Some("no-referrer"));
    assert_ne!(response.header("x-request-id"), Some("spoofed"));
    assert_eq!(response.headers_named("set-cookie"), vec!["a=1", "b=2"]);
    stop(server);
}

#[test]
fn not_found_uses_envelope() {
    let (server, dispatcher) = serve(settings());
    let response = get(server.local_addr(), "/missing");
    assert_eq!(response.status, 404);
    let body = json(&response);
    assert_eq!(body["error"]["status"], 404);
    assert_eq!(body["error"]["message"], "Not Found");
    assert_eq!(
        body["error"]["request_id"],
        response.header("x-request-id").unwrap()
    );
    assert_eq!(dispatcher.calls.load(Ordering::SeqCst), 0);
    stop(server);
}

#[test]
fn method_not_allowed_lists_allow() {
    let (server, _) = serve(settings());
    let response = send(
        server.local_addr(),
        b"DELETE /hello HTTP/1.1\r\nHost: t\r\nConnection: close\r\n\r\n",
    );
    assert_eq!(response.status, 405);
    assert_eq!(response.header("allow"), Some("GET, HEAD"));
    assert_eq!(json(&response)["error"]["status"], 405);
    stop(server);
}

#[test]
fn trailing_slash_redirect_keeps_query() {
    let (server, _) = serve(settings());
    let response = get(server.local_addr(), "/hello/?a=1&b=2");
    assert_eq!(response.status, 308);
    assert_eq!(response.header("location"), Some("/hello?a=1&b=2"));
    stop(server);
}

#[test]
fn head_has_headers_but_no_body() {
    let (server, _) = serve(settings());
    let response = send(
        server.local_addr(),
        b"HEAD /hello HTTP/1.1\r\nHost: t\r\nConnection: close\r\n\r\n",
    );
    assert_eq!(response.status, 200);
    assert_eq!(response.header("content-type"), Some("application/json"));
    assert!(response.body.is_empty());
    stop(server);
}

#[test]
fn declared_oversized_body_is_rejected_before_dispatch() {
    let (server, dispatcher) = serve(settings());
    let response = send(
        server.local_addr(),
        b"POST /upload HTTP/1.1\r\nHost: t\r\nContent-Length: 1000000\r\nConnection: close\r\n\r\n",
    );
    assert_eq!(response.status, 413);
    assert_eq!(json(&response)["error"]["status"], 413);
    assert_eq!(dispatcher.calls.load(Ordering::SeqCst), 0);
    stop(server);
}

#[test]
fn chunked_oversized_body_is_rejected_before_dispatch() {
    let (server, dispatcher) = serve(settings());
    let chunk = "x".repeat(40);
    let request = format!(
        "POST /upload HTTP/1.1\r\nHost: t\r\nTransfer-Encoding: chunked\r\nConnection: close\r\n\r\n\
         28\r\n{chunk}\r\n28\r\n{chunk}\r\n0\r\n\r\n"
    );
    let response = send(server.local_addr(), request.as_bytes());
    assert_eq!(response.status, 413);
    assert_eq!(dispatcher.calls.load(Ordering::SeqCst), 0);
    stop(server);
}

#[test]
fn body_within_limit_is_dispatched() {
    let (server, _) = serve(settings());
    let response = send(
        server.local_addr(),
        b"POST /upload HTTP/1.1\r\nHost: t\r\nContent-Length: 5\r\nConnection: close\r\n\r\nhello",
    );
    assert_eq!(response.status, 200);
    assert_eq!(response.text(), "5");
    stop(server);
}

#[test]
fn timeout_returns_503_and_abandons_dispatch() {
    let (server, dispatcher) = serve(settings());
    let started = Instant::now();
    let response = get(server.local_addr(), "/hang");
    assert_eq!(response.status, 503);
    assert_eq!(response.header("retry-after"), Some("1"));
    assert!(started.elapsed() < Duration::from_secs(5));
    assert!(
        dispatcher.hang_dropped.load(Ordering::SeqCst),
        "dispatch future dropped"
    );
    // The server keeps serving.
    assert_eq!(get(server.local_addr(), "/hello").status, 200);
    stop(server);
}

#[test]
fn graceful_stop_completes_in_flight_requests() {
    let (server, _) = serve(Settings {
        request_timeout: Duration::from_secs(5),
        ..settings()
    });
    let addr = server.local_addr();
    let in_flight = thread::spawn(move || get(addr, "/slow"));
    thread::sleep(Duration::from_millis(150));
    server.stop(true);
    let response = in_flight.join().unwrap();
    assert_eq!(response.status, 200);
    assert_eq!(response.text(), "slow done");
    assert!(server.wait(Some(Duration::from_secs(10))));
    // The listener closes shortly after the server reports stopped.
    let deadline = Instant::now() + Duration::from_secs(5);
    while TcpStream::connect_timeout(&addr, Duration::from_millis(300)).is_ok() {
        assert!(
            Instant::now() < deadline,
            "listener still accepting after stop"
        );
        thread::sleep(Duration::from_millis(100));
    }
}

#[test]
fn shutdown_statistics_count_finished_requests() {
    let (server, _) = serve(Settings {
        request_timeout: Duration::from_secs(5),
        ..settings()
    });
    let addr = server.local_addr();
    let in_flight = thread::spawn(move || get(addr, "/slow"));
    thread::sleep(Duration::from_millis(150));
    server.stop(true);
    assert_eq!(in_flight.join().unwrap().status, 200);
    assert!(server.wait(Some(Duration::from_secs(10))));
    let stats = server.shutdown_stats();
    assert_eq!(
        (stats.at_stop, stats.finished, stats.cut_off),
        (1, 1, 0),
        "{stats:?}"
    );
}

#[test]
fn shutdown_statistics_count_requests_cut_off_after_the_timeout() {
    let (server, dispatcher) = serve(Settings {
        request_timeout: Duration::from_secs(20),
        shutdown_timeout: Duration::from_secs(1),
        ..settings()
    });
    let addr = server.local_addr();
    let _client = thread::spawn(move || {
        let mut stream = TcpStream::connect(addr).unwrap();
        stream
            .write_all(
                b"GET /hang HTTP/1.1
Host: test

",
            )
            .unwrap();
        let mut buf = Vec::new();
        let _ = stream.read_to_end(&mut buf);
    });
    thread::sleep(Duration::from_millis(200));
    let stopped = Instant::now();
    server.stop(true);
    assert!(server.wait(Some(Duration::from_secs(10))));
    assert!(
        stopped.elapsed() < Duration::from_secs(4),
        "bounded by shutdown_timeout"
    );
    let stats = server.shutdown_stats();
    assert_eq!(
        (stats.at_stop, stats.finished, stats.cut_off),
        (1, 0, 1),
        "{stats:?}"
    );
    assert!(
        dispatcher.hang_dropped.load(Ordering::SeqCst),
        "the cut-off handler was dropped"
    );
}

#[test]
fn requests_ended_before_the_stop_are_not_counted() {
    let (server, _) = serve(settings());
    let addr = server.local_addr();
    assert_eq!(get(addr, "/hello").status, 200);
    {
        // A client that gives up at once.
        let mut stream = TcpStream::connect(addr).unwrap();
        stream
            .write_all(
                b"GET /slow HTTP/1.1
Host: test

",
            )
            .unwrap();
    }
    thread::sleep(Duration::from_millis(700));
    server.stop(true);
    assert!(server.wait(Some(Duration::from_secs(10))));
    let stats = server.shutdown_stats();
    assert_eq!(
        (stats.at_stop, stats.finished, stats.cut_off),
        (0, 0, 0),
        "{stats:?}"
    );
}

#[test]
fn slow_bodies_get_408_and_the_connection_closes() {
    let (server, dispatcher) = serve(settings());
    let mut stream = TcpStream::connect(server.local_addr()).unwrap();
    stream
        .set_read_timeout(Some(Duration::from_secs(5)))
        .unwrap();
    let started = Instant::now();
    stream
        .write_all(b"POST /upload HTTP/1.1\r\nHost: test\r\nContent-Length: 10\r\n\r\nab")
        .unwrap();
    let mut buf = Vec::new();
    let _ = stream.read_to_end(&mut buf);
    let response = parse(&buf);
    assert_eq!(response.status, 408);
    assert_eq!(response.header("connection"), Some("close"));
    assert!(
        started.elapsed() < Duration::from_secs(2),
        "bounded by request_timeout"
    );
    assert_eq!(
        dispatcher.calls.load(Ordering::SeqCst),
        0,
        "the handler never ran"
    );
    stop(server);
}

#[test]
fn body_time_counts_against_the_request_timeout() {
    let (server, _) = serve(Settings {
        request_timeout: Duration::from_secs(1),
        ..settings()
    });
    let addr = server.local_addr();
    // The body arrives after 600 ms; the 600 ms handler then overruns the 1 s deadline.
    let mut stream = TcpStream::connect(addr).unwrap();
    stream
        .set_read_timeout(Some(Duration::from_secs(5)))
        .unwrap();
    stream
        .write_all(b"POST /short HTTP/1.1\r\nHost: test\r\nConnection: close\r\nContent-Length: 2\r\n\r\na")
        .unwrap();
    thread::sleep(Duration::from_millis(600));
    stream.write_all(b"b").unwrap();
    let mut buf = Vec::new();
    let _ = stream.read_to_end(&mut buf);
    assert_eq!(parse(&buf).status, 503);
    // The same handler with the body sent at once finishes in time.
    let fast = send(
        addr,
        b"POST /short HTTP/1.1\r\nHost: test\r\nConnection: close\r\nContent-Length: 2\r\n\r\nab",
    );
    assert_eq!(fast.status, 200);
    assert_eq!(fast.text(), "2");
    stop(server);
}

#[test]
fn a_client_disconnect_cancels_the_handler() {
    let (server, dispatcher) = serve(Settings {
        request_timeout: Duration::from_secs(20),
        ..settings()
    });
    {
        let mut stream = TcpStream::connect(server.local_addr()).unwrap();
        stream
            .write_all(b"GET /hang HTTP/1.1\r\nHost: test\r\n\r\n")
            .unwrap();
        thread::sleep(Duration::from_millis(200));
    } // the client goes away while the handler runs
    let deadline = Instant::now() + Duration::from_secs(3);
    while !dispatcher.hang_dropped.load(Ordering::SeqCst) {
        assert!(
            Instant::now() < deadline,
            "the handler of a vanished client keeps running"
        );
        thread::sleep(Duration::from_millis(20));
    }
    stop(server);
}

#[test]
fn wait_times_out_while_running() {
    let (server, _) = serve(settings());
    assert!(!server.wait(Some(Duration::from_millis(50))));
    stop(server);
}

#[test]
fn slow_header_clients_are_cut_off() {
    let (server, _) = serve(Settings {
        header_timeout: Duration::from_millis(200),
        ..settings()
    });
    let mut stream = TcpStream::connect(server.local_addr()).unwrap();
    stream
        .set_read_timeout(Some(Duration::from_secs(5)))
        .unwrap();
    stream
        .write_all(b"GET /hello HTTP/1.1\r\nHost: t\r\n")
        .unwrap();
    let started = Instant::now();
    let mut buf = Vec::new();
    let _ = stream.read_to_end(&mut buf);
    assert!(
        started.elapsed() < Duration::from_secs(4),
        "connection closed by server"
    );
    if !buf.is_empty() {
        assert_eq!(parse(&buf).status, 408);
    }
    stop(server);
}

#[test]
fn connection_limit_defers_extra_connections() {
    let (server, _) = serve(Settings {
        workers: 1,
        max_connections: 1,
        header_timeout: Duration::from_secs(3),
        ..settings()
    });
    let addr = server.local_addr();
    let mut holder = TcpStream::connect(addr).unwrap();
    holder
        .write_all(b"GET /hello HTTP/1.1\r\nHost: t\r\n")
        .unwrap();
    thread::sleep(Duration::from_millis(200));

    let mut waiting = TcpStream::connect(addr).unwrap();
    waiting
        .set_read_timeout(Some(Duration::from_millis(500)))
        .unwrap();
    waiting
        .write_all(b"GET /hello HTTP/1.1\r\nHost: t\r\nConnection: close\r\n\r\n")
        .unwrap();
    let mut first = [0u8; 1];
    let early = waiting.read(&mut first);
    assert!(
        early.is_err(),
        "second connection must not be served while the limit is reached"
    );

    holder.write_all(b"Connection: close\r\n\r\n").unwrap();
    let mut sink = Vec::new();
    holder.read_to_end(&mut sink).unwrap();
    drop(holder);

    waiting
        .set_read_timeout(Some(Duration::from_secs(5)))
        .unwrap();
    let mut buf = Vec::new();
    waiting.read_to_end(&mut buf).unwrap();
    assert_eq!(parse(&buf).status, 200);
    stop(server);
}

#[test]
fn port_in_use_is_a_bind_error() {
    let (server, _) = serve(settings());
    let port = server.local_addr().port();
    let err = start(
        Settings { port, ..settings() },
        test_router(),
        Arc::new(TestDispatcher::default()),
    )
    .expect_err("second bind fails");
    assert!(matches!(err, StartError::Bind { .. }), "{err:?}");
    assert!(err.to_string().contains(&port.to_string()));
    stop(server);
}

#[test]
fn invalid_settings_are_rejected() {
    let err = start(
        Settings {
            workers: 0,
            ..settings()
        },
        test_router(),
        Arc::new(TestDispatcher::default()),
    )
    .expect_err("invalid settings");
    assert!(matches!(err, StartError::Settings(_)));
}

#[test]
fn error_bodies_do_not_name_the_engine() {
    let (server, _) = serve(settings());
    for path in ["/missing", "/hang", "/hello/"] {
        let response = get(server.local_addr(), path);
        let text = format!("{:?}{}", response.headers, response.text()).to_lowercase();
        for engine in ["actix", "tokio", "pyo3", "hyper"] {
            assert!(!text.contains(engine), "{path} leaks {engine}: {text}");
        }
    }
    stop(server);
}
