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

// -- WebSocket client (Feature 018) ------------------------------------------------------------

pub const OP_TEXT: u8 = 0x1;
pub const OP_BINARY: u8 = 0x2;
pub const OP_CLOSE: u8 = 0x8;
pub const OP_PING: u8 = 0x9;
pub const OP_PONG: u8 = 0xA;

/// A minimal RFC 6455 client: masked frames out, unfragmented frames in.
pub struct WsClient {
    pub stream: TcpStream,
}

/// Opens a WebSocket; `Err` carries the HTTP response when the handshake is refused.
pub fn ws_connect(addr: SocketAddr, path: &str, extra_headers: &str) -> Result<WsClient, Raw> {
    let mut stream = TcpStream::connect(addr).unwrap();
    stream
        .set_read_timeout(Some(Duration::from_secs(10)))
        .unwrap();
    let request = format!(
        "GET {path} HTTP/1.1\r\nHost: test\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n\
         Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\nSec-WebSocket-Version: 13\r\n\
         {extra_headers}\r\n"
    );
    stream.write_all(request.as_bytes()).unwrap();
    let mut head = Vec::new();
    let mut byte = [0u8; 1];
    while !head.ends_with(b"\r\n\r\n") {
        match stream.read(&mut byte) {
            Ok(0) => break,
            Ok(_) => head.push(byte[0]),
            Err(e) => panic!("handshake read failed: {e}"),
        }
    }
    let status: u16 = String::from_utf8_lossy(&head)
        .split(' ')
        .nth(1)
        .and_then(|s| s.parse().ok())
        .unwrap_or(0);
    if status == 101 {
        return Ok(WsClient { stream });
    }
    let mut rest = Vec::new();
    let _ = stream.read_to_end(&mut rest);
    head.extend(rest);
    Err(parse(&head))
}

impl WsClient {
    pub fn send(&mut self, opcode: u8, payload: &[u8]) {
        let mut frame = vec![0x80 | opcode];
        let mask = [0x12, 0x34, 0x56, 0x78];
        match payload.len() {
            n if n < 126 => frame.push(0x80 | n as u8),
            n if n < 65_536 => {
                frame.push(0x80 | 126);
                frame.extend_from_slice(&(n as u16).to_be_bytes());
            }
            n => {
                frame.push(0x80 | 127);
                frame.extend_from_slice(&(n as u64).to_be_bytes());
            }
        }
        frame.extend_from_slice(&mask);
        frame.extend(payload.iter().enumerate().map(|(i, b)| b ^ mask[i % 4]));
        self.stream.write_all(&frame).unwrap();
    }

    pub fn send_text(&mut self, text: &str) {
        self.send(OP_TEXT, text.as_bytes());
    }

    pub fn send_close(&mut self, code: u16, reason: &str) {
        let mut payload = code.to_be_bytes().to_vec();
        payload.extend_from_slice(reason.as_bytes());
        self.send(OP_CLOSE, &payload);
    }

    /// The next frame; `None` when the connection ended.
    pub fn recv(&mut self) -> Option<(u8, Vec<u8>)> {
        let mut header = [0u8; 2];
        self.stream.read_exact(&mut header).ok()?;
        let opcode = header[0] & 0x0F;
        let mut len = u64::from(header[1] & 0x7F);
        if len == 126 {
            let mut extended = [0u8; 2];
            self.stream.read_exact(&mut extended).ok()?;
            len = u64::from(u16::from_be_bytes(extended));
        } else if len == 127 {
            let mut extended = [0u8; 8];
            self.stream.read_exact(&mut extended).ok()?;
            len = u64::from_be_bytes(extended);
        }
        let mut payload = vec![0u8; len as usize];
        self.stream.read_exact(&mut payload).ok()?;
        Some((opcode, payload))
    }

    /// The next data or close frame, answering pings on the way.
    pub fn recv_message(&mut self) -> Option<(u8, Vec<u8>)> {
        loop {
            let (opcode, payload) = self.recv()?;
            match opcode {
                OP_PING => self.send(OP_PONG, &payload),
                OP_PONG => {}
                _ => return Some((opcode, payload)),
            }
        }
    }

    /// Code and reason of a close frame.
    pub fn close_code(payload: &[u8]) -> (u16, String) {
        if payload.len() < 2 {
            return (1005, String::new());
        }
        (
            u16::from_be_bytes([payload[0], payload[1]]),
            String::from_utf8_lossy(&payload[2..]).into_owned(),
        )
    }
}
