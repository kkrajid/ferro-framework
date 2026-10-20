//! WebSocket connections between the engine and the application (Feature 018).
//!
//! The engine owns the network side; the application (the Python bridge) talks to it through a
//! shared [`SocketLink`] and receives [`SocketEvent`]s through its [`SocketEvents`]. Both
//! directions are bounded: the application learns when its queued messages drained
//! ([`SocketEvent::Drained`]), and the engine stops reading from the client while
//! [`INBOUND_LIMIT`] messages wait for the application.

use std::collections::VecDeque;
use std::sync::Mutex;
use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};

use bytes::Bytes;
use tokio::sync::Notify;

/// Bytes queued for the client above which `send` reports that the caller should wait.
pub const OUTBOUND_LIMIT: usize = 1 << 20;
/// Messages from the client waiting for the application before the engine stops reading.
pub const INBOUND_LIMIT: usize = 32;

/// Close codes used by Ferro (RFC 6455 §7.4).
pub mod close_code {
    pub const NORMAL: u16 = 1000;
    pub const GOING_AWAY: u16 = 1001;
    /// Never sent on the wire: the connection ended without a close frame.
    pub const ABNORMAL: u16 = 1006;
    pub const POLICY: u16 = 1008;
    pub const TOO_BIG: u16 = 1009;
    pub const INTERNAL: u16 = 1011;
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum SocketMessage {
    Text(String),
    Binary(Bytes),
}

impl SocketMessage {
    pub fn len(&self) -> usize {
        match self {
            Self::Text(text) => text.len(),
            Self::Binary(bytes) => bytes.len(),
        }
    }

    pub fn is_empty(&self) -> bool {
        self.len() == 0
    }
}

/// What the application asks the engine to do.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum SocketCommand {
    Send(SocketMessage),
    Close { code: u16, reason: String },
}

/// What the engine tells the application.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum SocketEvent {
    Message(SocketMessage),
    /// Queued outgoing messages dropped below half of [`OUTBOUND_LIMIT`].
    Drained,
    /// The connection ended (client, network, server shutdown, or the application's close).
    Closed {
        code: u16,
        reason: String,
    },
}

/// The application's side of a connection; called on engine threads, so it must hand events
/// over without blocking.
pub trait SocketEvents: Send + Sync {
    fn event(&self, event: SocketEvent);
}

/// The connection has closed (or is closing): nothing more can be sent.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct SocketClosed;

impl std::fmt::Display for SocketClosed {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str("the WebSocket connection is closed")
    }
}

impl std::error::Error for SocketClosed {}

/// Shared state of one connection.
#[derive(Debug, Default)]
pub struct SocketLink {
    commands: Mutex<VecDeque<SocketCommand>>,
    command_ready: Notify,
    queued_bytes: AtomicUsize,
    waiting_for_drain: AtomicBool,
    unconsumed: AtomicUsize,
    consumed: Notify,
    closing: AtomicBool,
    closed: AtomicBool,
}

impl SocketLink {
    pub fn new() -> Self {
        Self::default()
    }

    fn push(&self, command: SocketCommand) {
        self.commands
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .push_back(command);
        self.command_ready.notify_one();
    }

    // -- application side ----------------------------------------------------------------------

    /// Queues a message. `Ok(true)` when more than [`OUTBOUND_LIMIT`] bytes are queued: the
    /// caller should wait for [`SocketEvent::Drained`] before sending more.
    pub fn send(&self, message: SocketMessage) -> Result<bool, SocketClosed> {
        if self.closing.load(Ordering::Acquire) || self.closed.load(Ordering::Acquire) {
            return Err(SocketClosed);
        }
        let size = message.len();
        let queued = self.queued_bytes.fetch_add(size, Ordering::AcqRel) + size;
        self.push(SocketCommand::Send(message));
        let over = queued > OUTBOUND_LIMIT;
        if over {
            self.waiting_for_drain.store(true, Ordering::Release);
        }
        Ok(over)
    }

    /// Asks the engine to close the connection (once; later calls do nothing).
    pub fn close(&self, code: u16, reason: impl Into<String>) {
        if self.closing.swap(true, Ordering::AcqRel) || self.closed.load(Ordering::Acquire) {
            return;
        }
        self.push(SocketCommand::Close {
            code,
            reason: reason.into(),
        });
    }

    /// The application took one message delivered by [`SocketEvent::Message`].
    pub fn consumed(&self) {
        let mut current = self.unconsumed.load(Ordering::Acquire);
        while current > 0 {
            match self.unconsumed.compare_exchange_weak(
                current,
                current - 1,
                Ordering::AcqRel,
                Ordering::Acquire,
            ) {
                Ok(_) => break,
                Err(actual) => current = actual,
            }
        }
        self.consumed.notify_one();
    }

    pub fn is_closed(&self) -> bool {
        self.closing.load(Ordering::Acquire) || self.closed.load(Ordering::Acquire)
    }

    // -- engine side ---------------------------------------------------------------------------

    /// The next command from the application (waits until there is one).
    pub async fn next_command(&self) -> SocketCommand {
        loop {
            if let Some(command) = self
                .commands
                .lock()
                .unwrap_or_else(|e| e.into_inner())
                .pop_front()
            {
                return command;
            }
            self.command_ready.notified().await;
        }
    }

    /// `bytes` of a queued message were written; `true` when the application should hear
    /// [`SocketEvent::Drained`].
    pub fn sent(&self, bytes: usize) -> bool {
        let left = self
            .queued_bytes
            .fetch_sub(bytes, Ordering::AcqRel)
            .saturating_sub(bytes);
        left <= OUTBOUND_LIMIT / 2 && self.waiting_for_drain.swap(false, Ordering::AcqRel)
    }

    /// A message was handed to the application.
    pub fn received(&self) {
        self.unconsumed.fetch_add(1, Ordering::AcqRel);
    }

    /// Waits while [`INBOUND_LIMIT`] messages wait for the application.
    pub async fn room_for_inbound(&self) {
        while self.unconsumed.load(Ordering::Acquire) >= INBOUND_LIMIT {
            self.consumed.notified().await;
        }
    }

    /// The connection is over; sends fail from now on.
    pub fn mark_closed(&self) {
        self.closed.store(true, Ordering::Release);
    }

    /// Bytes queued for the client (for tests and metrics).
    pub fn queued_bytes(&self) -> usize {
        self.queued_bytes.load(Ordering::Acquire)
    }
}

/// Used when a dispatcher has no WebSocket support: events are ignored.
#[derive(Debug, Default)]
pub struct IgnoreEvents;

impl SocketEvents for IgnoreEvents {
    fn event(&self, _event: SocketEvent) {}
}
