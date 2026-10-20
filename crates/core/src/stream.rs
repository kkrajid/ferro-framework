//! Response bodies produced piece by piece (Feature 018): streaming responses and server-sent
//! events. The engine pulls the next chunk only after the client took the previous one.

use std::fmt;
use std::sync::{Arc, Mutex};

use bytes::Bytes;

use crate::LocalBoxFuture;

/// The producer of a streamed body failed: the engine aborts the response (HTTP/1.1: the
/// connection closes without the final chunk; HTTP/2: the stream is reset), so the client
/// cannot mistake a cut-off body for a complete one.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct StreamAborted;

impl fmt::Display for StreamAborted {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str("the response body was aborted")
    }
}

impl std::error::Error for StreamAborted {}

/// Produces a response body chunk by chunk: `Ok(Some(chunk))`, `Ok(None)` at the end, or
/// `Err(StreamAborted)` when the producer failed. Dropping the source before the end (client
/// gone, shutdown) must stop whatever produces the chunks.
pub trait ChunkSource: Send {
    fn next_chunk(&self) -> LocalBoxFuture<Result<Option<Bytes>, StreamAborted>>;
}

/// A streaming body attached to a [`crate::FerroResponse`]; taken once by the engine.
#[derive(Clone)]
pub struct BodyStream(Arc<Mutex<Option<Box<dyn ChunkSource>>>>);

impl BodyStream {
    pub fn new(source: Box<dyn ChunkSource>) -> Self {
        Self(Arc::new(Mutex::new(Some(source))))
    }

    /// The source, the first time it is asked for.
    pub fn take(&self) -> Option<Box<dyn ChunkSource>> {
        self.0.lock().unwrap_or_else(|e| e.into_inner()).take()
    }
}

impl fmt::Debug for BodyStream {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str("BodyStream")
    }
}

impl PartialEq for BodyStream {
    fn eq(&self, other: &Self) -> bool {
        Arc::ptr_eq(&self.0, &other.0)
    }
}

impl Eq for BodyStream {}
