//! Raw-TCP HTTP client helpers shared by the engine's integration tests.
#![allow(dead_code)]

use std::io::{ErrorKind, Read, Write};
use std::net::{SocketAddr, TcpStream};
use std::time::Duration;

#[derive(Debug)]
pub struct Raw {
    pub status: u16,
    pub headers: Vec<(String, String)>,
    pub body: Vec<u8>,
}

impl Raw {
    pub fn header(&self, name: &str) -> Option<&str> {
        self.headers
            .iter()
            .find(|(n, _)| n.eq_ignore_ascii_case(name))
            .map(|(_, v)| v.as_str())
    }

    pub fn text(&self) -> String {
        String::from_utf8_lossy(&self.body).into_owned()
    }

    pub fn json(&self) -> serde_json::Value {
        serde_json::from_slice(&self.body).expect("JSON body")
    }
}

pub fn parse(raw: &[u8]) -> Raw {
    let split = raw
        .windows(4)
        .position(|w| w == b"\r\n\r\n")
        .expect("complete head");
    let head = String::from_utf8_lossy(&raw[..split]).into_owned();
    let body = raw[split + 4..].to_vec();
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

pub fn send(addr: SocketAddr, request: &[u8]) -> Raw {
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

pub fn request(addr: SocketAddr, method: &str, path: &str) -> Raw {
    send(
        addr,
        format!("{method} {path} HTTP/1.1\r\nHost: test\r\nConnection: close\r\n\r\n").as_bytes(),
    )
}

pub fn get(addr: SocketAddr, path: &str) -> Raw {
    request(addr, "GET", path)
}
