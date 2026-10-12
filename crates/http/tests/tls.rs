//! Feature 014: TLS on the listener, ALPN (HTTP/2 offered), start-up validation of the files.

mod common;

use std::io::{ErrorKind, Read, Write};
use std::net::{SocketAddr, TcpStream};
use std::path::Path;
use std::sync::Arc;
use std::time::Duration;

use common::{Raw, parse};
use ferro_core::{Dispatcher, FerroRequest, FerroResponse, LocalBoxFuture, Method, Settings};
use ferro_http::{RunningServer, check_tls, start};
use ferro_router::Router;
use rustls::pki_types::{CertificateDer, ServerName};

struct Echo;

impl Dispatcher for Echo {
    fn dispatch(&self, request: FerroRequest) -> LocalBoxFuture<FerroResponse> {
        Box::pin(async move {
            let host = request
                .headers
                .iter()
                .find(|(n, _)| n == "host")
                .map(|(_, v)| v.clone())
                .unwrap_or_default();
            FerroResponse::new(200).with_body(format!(
                "{} {} host={host}",
                request.method, request.raw_path
            ))
        })
    }
}

fn router() -> Router {
    let mut builder = Router::builder();
    builder
        .add(&[Method::Get, Method::Post], "/echo", 0)
        .unwrap();
    builder.build()
}

struct Files {
    dir: tempfile::TempDir,
    cert_der: CertificateDer<'static>,
}

impl Files {
    fn path(&self, name: &str) -> String {
        self.dir.path().join(name).to_string_lossy().into_owned()
    }
}

/// A self-signed certificate for `localhost` (`cert.pem`, `key.pem`) and a second key
/// (`other.pem`) that does not match it.
fn files() -> Files {
    let dir = tempfile::tempdir().unwrap();
    let issued = rcgen::generate_simple_self_signed(vec!["localhost".to_string()]).unwrap();
    let other = rcgen::generate_simple_self_signed(vec!["other".to_string()]).unwrap();
    let write = |name: &str, text: String| std::fs::write(dir.path().join(name), text).unwrap();
    write("cert.pem", issued.cert.pem());
    write("key.pem", issued.signing_key.serialize_pem());
    write("other.pem", other.signing_key.serialize_pem());
    write("junk.pem", "not a pem file\n".to_string());
    Files {
        cert_der: issued.cert.der().clone(),
        dir,
    }
}

fn tls_settings(files: &Files) -> Settings {
    Settings {
        port: 0,
        workers: 1,
        tls_cert: Some(files.path("cert.pem")),
        tls_key: Some(files.path("key.pem")),
        ..Settings::default()
    }
}

fn serve(settings: Settings) -> RunningServer {
    start(settings, router(), Arc::new(Echo)).expect("server starts")
}

fn stop(server: RunningServer) {
    server.stop(true);
    assert!(server.wait(Some(Duration::from_secs(10))), "server stops");
}

fn client(files: &Files, alpn: &[&[u8]]) -> Arc<rustls::ClientConfig> {
    let mut roots = rustls::RootCertStore::empty();
    roots.add(files.cert_der.clone()).unwrap();
    let provider = Arc::new(rustls::crypto::ring::default_provider());
    let mut config = rustls::ClientConfig::builder_with_provider(provider)
        .with_safe_default_protocol_versions()
        .unwrap()
        .with_root_certificates(roots)
        .with_no_client_auth();
    config.alpn_protocols = alpn.iter().map(|p| p.to_vec()).collect();
    Arc::new(config)
}

fn connect(
    addr: SocketAddr,
    config: Arc<rustls::ClientConfig>,
) -> rustls::StreamOwned<rustls::ClientConnection, TcpStream> {
    let tcp = TcpStream::connect(addr).unwrap();
    tcp.set_read_timeout(Some(Duration::from_secs(10))).unwrap();
    let name = ServerName::try_from("localhost").unwrap();
    let connection = rustls::ClientConnection::new(config, name).unwrap();
    let mut stream = rustls::StreamOwned::new(connection, tcp);
    while stream.conn.is_handshaking() {
        stream.conn.complete_io(&mut stream.sock).unwrap();
    }
    stream
}

fn https(addr: SocketAddr, files: &Files, request: &str) -> Raw {
    let mut stream = connect(addr, client(files, &[b"http/1.1"]));
    assert_eq!(stream.conn.alpn_protocol(), Some(&b"http/1.1"[..]));
    stream.write_all(request.as_bytes()).unwrap();
    let mut buf = Vec::new();
    match stream.read_to_end(&mut buf) {
        Ok(_) => {}
        // A server may close without close_notify after `connection: close`.
        Err(e)
            if matches!(
                e.kind(),
                ErrorKind::UnexpectedEof | ErrorKind::ConnectionReset
            ) => {}
        Err(e) => panic!("read failed: {e}"),
    }
    parse(&buf)
}

#[test]
fn serves_https() {
    let files = files();
    let server = serve(tls_settings(&files));
    let addr = server.local_addr();
    let response = https(
        addr,
        &files,
        "GET /echo HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n",
    );
    assert_eq!(response.status, 200);
    assert_eq!(response.text(), "GET /echo host=localhost");
    let probe = https(
        addr,
        &files,
        "GET /healthz HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n",
    );
    assert_eq!(probe.status, 200);
    stop(server);
}

#[test]
fn offers_http2_through_alpn() {
    let files = files();
    let server = serve(tls_settings(&files));
    let stream = connect(server.local_addr(), client(&files, &[b"h2", b"http/1.1"]));
    assert_eq!(stream.conn.alpn_protocol(), Some(&b"h2"[..]));
    drop(stream);
    stop(server);
}

#[test]
fn same_origin_posts_are_https() {
    let files = files();
    let server = serve(tls_settings(&files));
    let addr = server.local_addr();
    let post = |origin: &str| {
        https(
            addr,
            &files,
            &format!(
                "POST /echo HTTP/1.1\r\nHost: localhost:{port}\r\nOrigin: {origin}\r\n\
                 Content-Length: 0\r\nConnection: close\r\n\r\n",
                port = addr.port()
            ),
        )
    };
    assert_eq!(
        post(&format!("https://localhost:{}", addr.port())).status,
        200
    );
    assert_eq!(
        post(&format!("http://localhost:{}", addr.port())).status,
        403
    );
    stop(server);
}

#[test]
fn plain_http_on_a_tls_port_fails_alone() {
    let files = files();
    let server = serve(tls_settings(&files));
    let addr = server.local_addr();
    let mut plain = TcpStream::connect(addr).unwrap();
    plain
        .set_read_timeout(Some(Duration::from_secs(10)))
        .unwrap();
    plain
        .write_all(b"GET /echo HTTP/1.1\r\nHost: localhost\r\n\r\n")
        .unwrap();
    let mut buf = Vec::new();
    let _ = plain.read_to_end(&mut buf);
    assert!(!buf.starts_with(b"HTTP/1.1 200"), "no plain answer");
    let response = https(
        addr,
        &files,
        "GET /echo HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n",
    );
    assert_eq!(response.status, 200, "TLS clients are still served");
    stop(server);
}

fn tls_error(files: &Files, cert: Option<&str>, key: Option<&str>) -> String {
    let settings = Settings {
        tls_cert: cert.map(|name| files.path(name)),
        tls_key: key.map(|name| files.path(name)),
        ..Settings::default()
    };
    let checked = check_tls(&settings).unwrap_err().to_string();
    let started = start(
        Settings {
            port: 0,
            ..settings
        },
        router(),
        Arc::new(Echo),
    )
    .unwrap_err()
    .to_string();
    assert_eq!(checked, started);
    checked
}

#[test]
fn misconfiguration_is_reported_before_serving() {
    let files = files();
    let only_cert = tls_error(&files, Some("cert.pem"), None);
    assert!(
        only_cert.contains("`tls_key`") && only_cert.contains("together"),
        "{only_cert}"
    );
    let only_key = tls_error(&files, None, Some("key.pem"));
    assert!(only_key.contains("`tls_cert`"), "{only_key}");
    let missing = tls_error(&files, Some("missing.pem"), Some("key.pem"));
    assert!(
        missing.contains("`tls_cert`") && missing.contains("missing.pem"),
        "{missing}"
    );
    let junk = tls_error(&files, Some("junk.pem"), Some("key.pem"));
    assert!(junk.contains("no PEM certificate"), "{junk}");
    let no_key = tls_error(&files, Some("cert.pem"), Some("cert.pem"));
    assert!(
        no_key.contains("`tls_key`") && no_key.contains("no private key"),
        "{no_key}"
    );
    let mismatch = tls_error(&files, Some("cert.pem"), Some("other.pem"));
    assert!(mismatch.contains("does not match"), "{mismatch}");
    let key_text = std::fs::read_to_string(Path::new(&files.path("other.pem"))).unwrap();
    let secret_line = key_text.lines().nth(1).unwrap();
    for message in [&only_cert, &missing, &junk, &no_key, &mismatch] {
        assert!(
            !message.contains(secret_line),
            "key material leaked: {message}"
        );
    }
    assert!(check_tls(&tls_settings(&files)).is_ok());
}
