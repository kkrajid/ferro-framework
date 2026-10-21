//! Request bodies kept on disk once they pass a size (Feature 026).
//!
//! A body up to `spool_after` bytes stays in memory. A larger one (an upload) is written to an
//! unnamed temporary file as it arrives and then memory-mapped: the parsers and handlers still
//! see one contiguous `Bytes`, but its pages belong to the file, so the kernel can drop them
//! under memory pressure instead of the process holding the whole body. The file is deleted
//! by the operating system when the last view of the body is dropped.

use std::fs::File;
use std::io::{self, Write};

use bytes::{Bytes, BytesMut};

/// Bodies larger than this are spooled to disk (when the route allows such bodies at all).
pub(crate) const SPOOL_AFTER: usize = 1024 * 1024;
/// Spooled data is written in blocks of this size, off the event loop.
const BLOCK: usize = 256 * 1024;

pub(crate) struct Collector {
    buffer: BytesMut,
    file: Option<File>,
    len: usize,
    spool_after: usize,
}

impl Collector {
    /// `declared` is the request's `content-length`, when it sent one.
    pub(crate) fn new(declared: Option<usize>, spool_after: usize) -> io::Result<Self> {
        let spool = declared.is_some_and(|len| len > spool_after);
        let capacity = if spool { BLOCK } else { declared.unwrap_or(0) };
        Ok(Self {
            buffer: BytesMut::with_capacity(capacity),
            file: if spool { Some(temporary_file()?) } else { None },
            len: 0,
            spool_after,
        })
    }

    pub(crate) fn len(&self) -> usize {
        self.len
    }

    pub(crate) async fn push(&mut self, chunk: &[u8]) -> io::Result<()> {
        self.len += chunk.len();
        self.buffer.extend_from_slice(chunk);
        if self.file.is_none() && self.len > self.spool_after {
            self.file = Some(temporary_file()?);
        }
        if self.file.is_some() && self.buffer.len() >= BLOCK {
            self.flush().await?;
        }
        Ok(())
    }

    /// The whole body: the memory buffer, or the mapped file.
    pub(crate) async fn finish(mut self) -> io::Result<Bytes> {
        if self.file.is_none() {
            return Ok(self.buffer.freeze());
        }
        self.flush().await?;
        let file = self.file.take().expect("spooling");
        // The file is unnamed, only this process holds it, and writing has ended.
        let map = blocking(move || ferro_shared::MappedFile::map_private(file)).await?;
        Ok(Bytes::from_owner(map))
    }

    async fn flush(&mut self) -> io::Result<()> {
        let mut file = self.file.take().expect("spooling");
        let block = self.buffer.split().freeze();
        let file = blocking(move || {
            file.write_all(&block)?;
            Ok(file)
        })
        .await?;
        self.file = Some(file);
        Ok(())
    }
}

/// An unnamed file in the system's temporary directory (`TMPDIR`, `TEMP`); creating it is
/// one quick system call, made on the event loop.
fn temporary_file() -> io::Result<File> {
    tempfile::tempfile()
}

/// File work off the event loop.
async fn blocking<T: Send + 'static>(
    work: impl FnOnce() -> io::Result<T> + Send + 'static,
) -> io::Result<T> {
    tokio::task::spawn_blocking(work)
        .await
        .map_err(|_| io::Error::other("spool task failed"))?
}

#[cfg(test)]
mod tests {
    use super::*;

    fn run<T>(future: impl std::future::Future<Output = T>) -> T {
        tokio::runtime::Builder::new_current_thread()
            .build()
            .unwrap()
            .block_on(future)
    }

    fn collect(chunks: &[&[u8]], declared: Option<usize>, spool_after: usize) -> (Bytes, bool) {
        run(async {
            let mut collector = Collector::new(declared, spool_after).unwrap();
            for chunk in chunks {
                collector.push(chunk).await.unwrap();
            }
            let spooled = collector.file.is_some();
            (collector.finish().await.unwrap(), spooled)
        })
    }

    #[test]
    fn small_bodies_stay_in_memory() {
        let (body, spooled) = collect(&[b"hello ", b"world"], Some(11), 64);
        assert_eq!(&body[..], b"hello world");
        assert!(!spooled);
    }

    #[test]
    fn a_body_passing_the_size_is_spooled_and_mapped() {
        let block: Vec<u8> = (0..=255u8).cycle().take(100_000).collect();
        let chunks: Vec<&[u8]> = block.chunks(7_000).collect();
        // Without content-length: starts in memory, moves to the file on the way.
        let (body, spooled) = collect(&chunks, None, 10_000);
        assert!(spooled);
        assert_eq!(&body[..], &block[..]);
        // A declared large body goes to the file from the start.
        let (body, spooled) = collect(&chunks, Some(block.len()), 10_000);
        assert!(spooled);
        assert_eq!(&body[..], &block[..]);
        // Slices keep the map alive after the body is dropped.
        let tail = body.slice(99_000..);
        drop(body);
        assert_eq!(&tail[..], &block[99_000..]);
    }

    #[test]
    fn spooling_spans_several_blocks() {
        let data = vec![7u8; BLOCK * 3 + 11];
        let (body, spooled) = collect(&[&data[..BLOCK + 5], &data[BLOCK + 5..]], None, 1);
        assert!(spooled);
        assert_eq!(body.len(), data.len());
        assert!(body.iter().all(|&b| b == 7));
    }
}
