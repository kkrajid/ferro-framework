//! Feature 018: WebSocket connections and streaming bodies in the engine.

mod common;

use std::io::{Read, Write};
use std::net::TcpStream;
use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use bytes::Bytes;
use common::{OP_BINARY, OP_CLOSE, OP_TEXT, WsClient, get, ws_connect};
use ferro_core::security::RouteOptions;
use ferro_core::security::ratelimit::RateSpec;
use ferro_core::{
    ChunkSource, Dispatcher, FerroRequest, FerroResponse, LocalBoxFuture, Method, Settings,
    SocketEvent, SocketEvents, SocketLink, SocketMessage, StreamAborted,
};
use ferro_http::{RunningServer, start_with};
use ferro_router::Router;

#[derive(Default)]
struct Record {
    closed: Mutex<Vec<(u16, String)>>,
    source_dropped: AtomicBool,
    chunks_made: AtomicUsize,
}

struct App {
    record: Arc<Record>,
}

/// Chunks every 100 ms: `count` of them, or forever with `None`; then fails instead of ending
/// when `fail` is set.
struct Ticker {
    count: Option<usize>,
    fail: bool,
    made: Arc<Record>,
}

impl ChunkSource for Ticker {
    fn next_chunk(&self) -> LocalBoxFuture<Result<Option<Bytes>, StreamAborted>> {
        let record = self.made.clone();
        let count = self.count;
        let fail = self.fail;
        Box::pin(async move {
            let n = record.chunks_made.fetch_add(1, Ordering::SeqCst);
            if count.is_some_and(|c| n >= c) {
                return if fail { Err(StreamAborted) } else { Ok(None) };
            }
            tokio::time::sleep(Duration::from_millis(100)).await;
            Ok(Some(Bytes::from(format!("chunk {n}\n"))))
        })
    }
}

impl Drop for Ticker {
    fn drop(&mut self) {
        self.made.source_dropped.store(true, Ordering::SeqCst);
    }
}

struct Echo {
    link: Arc<SocketLink>,
    record: Arc<Record>,
}

impl SocketEvents for Echo {
    fn event(&self, event: SocketEvent) {
        match event {
            SocketEvent::Message(SocketMessage::Text(text)) => {
                self.link.consumed();
                if text == "close please" {
                    self.link.close(4000, "bye");
                } else {
                    let _ = self.link.send(SocketMessage::Text(format!("echo:{text}")));
                }
            }
            SocketEvent::Message(SocketMessage::Binary(bytes)) => {
                self.link.consumed();
                let _ = self.link.send(SocketMessage::Binary(bytes));
            }
            SocketEvent::Closed { code, reason } => {
                self.record.closed.lock().unwrap().push((code, reason));
            }
            SocketEvent::Drained => {}
        }
    }
}

impl Dispatcher for App {
    fn dispatch(&self, request: FerroRequest) -> LocalBoxFuture<FerroResponse> {
        let record = self.record.clone();
        Box::pin(async move {
            let (count, fail) = match request.raw_path.as_str() {
                "/forever" => (None, false),
                "/failing" => (Some(2), true),
                _ => (Some(5), false),
            };
            FerroResponse::new(200)
                .with_header("content-type", "text/plain")
                .with_stream(Box::new(Ticker {
                    count,
                    fail,
                    made: record,
                }))
        })
    }

    fn open_socket(&self, _request: FerroRequest, link: Arc<SocketLink>) -> Arc<dyn SocketEvents> {
        Arc::new(Echo {
            link,
            record: self.record.clone(),
        })
    }
}

fn serve(settings: Settings, socket_limit: Option<&str>) -> (RunningServer, Arc<Record>) {
    let mut builder = Router::builder();
    builder.add(&[Method::Get], "/ws", 0).unwrap();
    builder.add(&[Method::Get], "/stream", 1).unwrap();
    builder.add(&[Method::Get], "/forever", 2).unwrap();
    builder.add(&[Method::Get], "/failing", 3).unwrap();
    let routes = vec![
        RouteOptions {
            websocket: true,
            rate_limit: socket_limit.map(|l| RateSpec::parse(l).unwrap()),
            ..RouteOptions::default()
        },
        RouteOptions::default(),
        RouteOptions::default(),
        RouteOptions::default(),
    ];
    let record = Arc::new(Record::default());
    let settings = Settings {
        port: 0,
        workers: 1,
        shutdown_timeout: Duration::from_secs(5),
        ..settings
    };
    let server = start_with(
        settings,
        builder.build(),
        routes,
        Arc::new(App {
            record: record.clone(),
        }),
    )
    .expect("server starts");
    (server, record)
}

fn stop(server: RunningServer) {
    server.stop(true);
    assert!(server.wait(Some(Duration::from_secs(15))), "server stops");
}

fn wait_for(what: &str, condition: impl Fn() -> bool) {
    let deadline = Instant::now() + Duration::from_secs(5);
    while !condition() {
        assert!(Instant::now() < deadline, "timed out waiting for {what}");
        std::thread::sleep(Duration::from_millis(20));
    }
}

#[test]
fn echoes_text_and_binary() {
    let (server, _) = serve(Settings::default(), None);
    let mut ws = ws_connect(server.local_addr(), "/ws", "").unwrap();
    for n in 0..100 {
        ws.send_text(&format!("hello {n}"));
        let (opcode, payload) = ws.recv_message().unwrap();
        assert_eq!(opcode, OP_TEXT);
        assert_eq!(payload, format!("echo:hello {n}").as_bytes());
    }
    ws.send(OP_BINARY, &[0, 1, 2, 255]);
    assert_eq!(ws.recv_message().unwrap(), (OP_BINARY, vec![0, 1, 2, 255]));
    stop(server);
}

#[test]
fn plain_requests_to_a_socket_address_get_426() {
    let (server, _) = serve(Settings::default(), None);
    let response = get(server.local_addr(), "/ws");
    assert_eq!(response.status, 426);
    assert_eq!(response.header("upgrade"), Some("websocket"));
    stop(server);
}

#[test]
fn handshakes_from_other_sites_are_refused() {
    let (server, _) = serve(Settings::default(), None);
    let addr = server.local_addr();
    let refused = ws_connect(
        addr,
        "/ws",
        "Origin: https://evil.example\r\nSec-Fetch-Site: cross-site\r\nCookie: sessionid=x\r\n",
    )
    .err()
    .expect("refused");
    assert_eq!(refused.status, 403);
    assert!(
        ws_connect(addr, "/ws", "Origin: http://test\r\n").is_ok(),
        "same origin"
    );
    assert!(ws_connect(addr, "/ws", "").is_ok(), "no browser metadata");
    stop(server);
}

#[test]
fn handshakes_are_rate_limited() {
    let (server, _) = serve(Settings::default(), Some("1/minute"));
    let addr = server.local_addr();
    assert!(ws_connect(addr, "/ws", "").is_ok());
    assert_eq!(ws_connect(addr, "/ws", "").err().unwrap().status, 429);
    stop(server);
}

#[test]
fn oversized_messages_close_with_1009() {
    let (server, record) = serve(
        Settings {
            max_body_size: 1024,
            ..Settings::default()
        },
        None,
    );
    let mut ws = ws_connect(server.local_addr(), "/ws", "").unwrap();
    ws.send_text(&"x".repeat(2000));
    let (opcode, payload) = ws.recv_message().unwrap();
    assert_eq!(opcode, OP_CLOSE);
    assert_eq!(WsClient::close_code(&payload).0, 1009);
    wait_for("the close event", || {
        !record.closed.lock().unwrap().is_empty()
    });
    assert_eq!(record.closed.lock().unwrap()[0].0, 1009);
    stop(server);
}

#[test]
fn either_side_can_close() {
    let (server, record) = serve(Settings::default(), None);
    let addr = server.local_addr();

    let mut ws = ws_connect(addr, "/ws", "").unwrap();
    ws.send_text("close please");
    let (opcode, payload) = ws.recv_message().unwrap();
    assert_eq!(opcode, OP_CLOSE);
    assert_eq!(WsClient::close_code(&payload), (4000, "bye".to_string()));

    let mut ws = ws_connect(addr, "/ws", "").unwrap();
    ws.send_close(1000, "done");
    let (opcode, _) = ws.recv_message().unwrap();
    assert_eq!(opcode, OP_CLOSE, "the server answers the close");
    wait_for("both close events", || {
        record.closed.lock().unwrap().len() == 2
    });
    let closed = record.closed.lock().unwrap().clone();
    assert!(closed.contains(&(4000, "bye".to_string())), "{closed:?}");
    assert!(closed.contains(&(1000, "done".to_string())), "{closed:?}");
    stop(server);
}

#[test]
fn stopping_the_server_closes_sockets_with_1001() {
    let (server, record) = serve(Settings::default(), None);
    let mut ws = ws_connect(server.local_addr(), "/ws", "").unwrap();
    ws.send_text("hi");
    ws.recv_message().unwrap();
    let started = Instant::now();
    server.stop(true);
    let (opcode, payload) = ws.recv_message().unwrap();
    assert_eq!(opcode, OP_CLOSE);
    assert_eq!(WsClient::close_code(&payload).0, 1001);
    assert!(server.wait(Some(Duration::from_secs(15))));
    assert!(
        started.elapsed() < Duration::from_secs(5),
        "{:?}",
        started.elapsed()
    );
    assert_eq!(record.closed.lock().unwrap()[0].0, 1001);
}

/// Reads an HTTP response, returning when each piece of the body arrived.
fn read_timed(stream: &mut TcpStream) -> Vec<(Instant, Vec<u8>)> {
    stream
        .set_read_timeout(Some(Duration::from_secs(10)))
        .unwrap();
    let mut pieces = Vec::new();
    let mut buf = [0u8; 4096];
    loop {
        match stream.read(&mut buf) {
            Ok(0) | Err(_) => break,
            Ok(n) => pieces.push((Instant::now(), buf[..n].to_vec())),
        }
    }
    pieces
}

#[test]
fn streaming_bodies_arrive_piece_by_piece() {
    let (server, record) = serve(Settings::default(), None);
    let mut stream = TcpStream::connect(server.local_addr()).unwrap();
    let started = Instant::now();
    stream
        .write_all(b"GET /stream HTTP/1.1\r\nHost: t\r\nConnection: close\r\n\r\n")
        .unwrap();
    let pieces = read_timed(&mut stream);
    let all: Vec<u8> = pieces.iter().flat_map(|(_, p)| p.clone()).collect();
    let text = String::from_utf8_lossy(&all);
    assert!(text.starts_with("HTTP/1.1 200"), "{text}");
    assert!(text.contains("transfer-encoding: chunked"), "{text}");
    for n in 0..5 {
        assert!(text.contains(&format!("chunk {n}\n")), "{text}");
    }
    assert!(
        text.ends_with("0\r\n\r\n"),
        "a finished body ends with the last chunk"
    );
    let first = pieces
        .iter()
        .find(|(_, p)| String::from_utf8_lossy(p).contains("chunk 0"))
        .unwrap()
        .0;
    let last = pieces
        .iter()
        .find(|(_, p)| String::from_utf8_lossy(p).contains("chunk 4"))
        .unwrap()
        .0;
    assert!(
        last.duration_since(first) >= Duration::from_millis(300),
        "chunks were sent as produced, not at the end"
    );
    assert!(started.elapsed() < Duration::from_secs(5));
    wait_for("the source to be dropped", || {
        record.source_dropped.load(Ordering::SeqCst)
    });
    stop(server);
}

#[test]
fn a_failing_source_aborts_the_body() {
    let (server, record) = serve(Settings::default(), None);
    let mut stream = TcpStream::connect(server.local_addr()).unwrap();
    stream
        .write_all(b"GET /failing HTTP/1.1\r\nHost: t\r\n\r\n")
        .unwrap();
    let started = Instant::now();
    let all: Vec<u8> = read_timed(&mut stream)
        .into_iter()
        .flat_map(|(_, piece)| piece)
        .collect();
    let text = String::from_utf8_lossy(&all);
    assert!(text.contains("chunk 1\n"), "{text}");
    assert!(
        !text.ends_with("0\r\n\r\n"),
        "an aborted body must not look complete: {text}"
    );
    assert!(
        started.elapsed() < Duration::from_secs(5),
        "the connection is closed, not left open"
    );
    wait_for("the source to be dropped", || {
        record.source_dropped.load(Ordering::SeqCst)
    });
    stop(server);
}

#[test]
fn a_client_leaving_drops_the_stream_source() {
    let (server, record) = serve(Settings::default(), None);
    let mut stream = TcpStream::connect(server.local_addr()).unwrap();
    stream
        .write_all(b"GET /forever HTTP/1.1\r\nHost: t\r\n\r\n")
        .unwrap();
    let mut buf = [0u8; 256];
    let mut seen = Vec::new();
    while !String::from_utf8_lossy(&seen).contains("chunk 1") {
        let n = stream.read(&mut buf).unwrap();
        seen.extend_from_slice(&buf[..n]);
    }
    drop(stream);
    wait_for("the source to be dropped", || {
        record.source_dropped.load(Ordering::SeqCst)
    });
    stop(server);
}

#[test]
fn stopping_the_server_ends_streams() {
    let (server, record) = serve(Settings::default(), None);
    let mut stream = TcpStream::connect(server.local_addr()).unwrap();
    stream
        .write_all(b"GET /forever HTTP/1.1\r\nHost: t\r\nConnection: close\r\n\r\n")
        .unwrap();
    let mut buf = [0u8; 256];
    let n = stream.read(&mut buf).unwrap();
    assert!(n > 0);
    let started = Instant::now();
    server.stop(true);
    let rest: Vec<u8> = read_timed(&mut stream)
        .into_iter()
        .flat_map(|(_, piece)| piece)
        .collect();
    assert!(
        !rest.ends_with(b"0\r\n\r\n"),
        "a body cut off by the stop must not look complete"
    );
    assert!(server.wait(Some(Duration::from_secs(15))));
    assert!(
        started.elapsed() < Duration::from_secs(5),
        "{:?}",
        started.elapsed()
    );
    assert!(record.source_dropped.load(Ordering::SeqCst));
}
