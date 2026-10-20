//! Requests in flight and what a shutdown did with them (Feature 011 FR-006).

use std::sync::Arc;
use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};

use tokio::sync::Notify;

/// Counts for the shutdown summary, final once [`crate::RunningServer::wait`] returns `true`.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub struct ShutdownStats {
    /// Requests being handled when the stop was requested.
    pub at_stop: usize,
    /// Requests that completed after the stop was requested.
    pub finished: usize,
    /// Requests dropped unfinished after the stop was requested (`shutdown_timeout` expired).
    pub cut_off: usize,
}

#[derive(Default)]
pub(crate) struct Lifecycle {
    in_flight: AtomicUsize,
    stopping: AtomicBool,
    at_stop: AtomicUsize,
    finished: AtomicUsize,
    cut_off: AtomicUsize,
    /// Woken when the stop begins: WebSockets close and streams end (Feature 018).
    stop_signal: Notify,
    sockets: Arc<AtomicUsize>,
    streams: Arc<AtomicUsize>,
}

/// Counts one open WebSocket or stream while it lives.
pub(crate) struct Open(Arc<AtomicUsize>);

impl Open {
    fn new(counter: &Arc<AtomicUsize>) -> Self {
        counter.fetch_add(1, Ordering::AcqRel);
        Self(counter.clone())
    }
}

impl Drop for Open {
    fn drop(&mut self) {
        self.0.fetch_sub(1, Ordering::AcqRel);
    }
}

impl Lifecycle {
    pub(crate) fn enter(&self) -> InFlight<'_> {
        self.in_flight.fetch_add(1, Ordering::AcqRel);
        InFlight {
            lifecycle: self,
            completed: false,
        }
    }

    pub(crate) fn in_flight(&self) -> usize {
        self.in_flight.load(Ordering::Acquire)
    }

    /// Starts counting: requests that end from now on belong to the shutdown.
    pub(crate) fn begin_stop(&self) {
        if !self.stopping.swap(true, Ordering::AcqRel) {
            self.at_stop.store(self.in_flight(), Ordering::Release);
        }
        self.stop_signal.notify_waiters();
    }

    /// Completes once the stop has begun.
    pub(crate) async fn stopped(&self) {
        loop {
            let mut notified = std::pin::pin!(self.stop_signal.notified());
            notified.as_mut().enable();
            if self.stopping() {
                return;
            }
            notified.await;
        }
    }

    pub(crate) fn socket_opened(&self) -> Open {
        Open::new(&self.sockets)
    }

    pub(crate) fn stream_opened(&self) -> Open {
        Open::new(&self.streams)
    }

    /// `(open WebSockets, open streams)`.
    pub(crate) fn open_connections(&self) -> (usize, usize) {
        (
            self.sockets.load(Ordering::Acquire),
            self.streams.load(Ordering::Acquire),
        )
    }

    pub(crate) fn stopping(&self) -> bool {
        self.stopping.load(Ordering::Acquire)
    }

    pub(crate) fn stats(&self) -> ShutdownStats {
        ShutdownStats {
            at_stop: self.at_stop.load(Ordering::Acquire),
            finished: self.finished.load(Ordering::Acquire),
            cut_off: self.cut_off.load(Ordering::Acquire),
        }
    }
}

/// Held for the whole handling of one request; dropping it unfinished means the request was
/// abandoned (client gone, or cut off by the shutdown).
pub(crate) struct InFlight<'a> {
    lifecycle: &'a Lifecycle,
    completed: bool,
}

impl InFlight<'_> {
    pub(crate) fn complete(mut self) {
        self.completed = true;
    }
}

impl Drop for InFlight<'_> {
    fn drop(&mut self) {
        self.lifecycle.in_flight.fetch_sub(1, Ordering::AcqRel);
        if self.lifecycle.stopping() {
            let counter = if self.completed {
                &self.lifecycle.finished
            } else {
                &self.lifecycle.cut_off
            };
            counter.fetch_add(1, Ordering::AcqRel);
        }
    }
}
