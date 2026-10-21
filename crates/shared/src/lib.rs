//! State shared by the worker processes of one `ferro run --processes N` server (Feature 015):
//! a fixed table of atomic words in a memory-mapped file, used for rate limits.
//!
//! This is the only Ferro crate with `unsafe` code: mapping the file and viewing it as atomic
//! words, and mapping the temporary file of a large request body ([`MappedFile`], Feature 026).
//! Every other crate forbids `unsafe`.
//!
//! Rate limits use GCRA (the generic cell rate algorithm): a client's whole state is one
//! "theoretical arrival time" (TAT), so a request is one compare-and-swap on one word, and no
//! process can leave a lock held when it dies. With emission interval `T = period / count`, a
//! request at `now` is allowed when `max(tat, now) + T - now <= period` — a token bucket holding
//! `count` tokens and refilling `count` per `period`, like the in-process limiter.

use std::collections::hash_map::{DefaultHasher, RandomState};
use std::fmt;
use std::fs::{File, OpenOptions};
use std::hash::{BuildHasher, Hash, Hasher};
use std::io;
use std::net::IpAddr;
use std::path::Path;
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use memmap2::MmapMut;

const MAGIC: u64 = u64::from_le_bytes(*b"FERROSHM");
const VERSION: u64 = 1;
/// Header words: magic, version, creation time (µs since the Unix epoch), two secret words,
/// slot count, two reserved.
const HEADER: usize = 8;
/// Slots of the rate table (a power of two): 512 KiB.
pub const RATE_SLOTS: usize = 1 << 16;
/// Slots looked at per client: its own entry or a free one must be among them.
const PROBES: usize = 8;
/// A slot packs a 16-bit key fingerprint over a 48-bit TAT in µs since creation (8.9 years).
const TIME_BITS: u32 = 48;
const TIME_MASK: u64 = (1 << TIME_BITS) - 1;

/// A mapped file viewed as atomic 64-bit words.
struct Words {
    _map: MmapMut,
    ptr: *const AtomicU64,
    len: usize,
}

// SAFETY: the words are only read and written through atomic operations, which are safe to share
// between threads; the mapping itself is owned and never remapped.
unsafe impl Send for Words {}
// SAFETY: as above.
unsafe impl Sync for Words {}

impl Words {
    fn map(file: &File) -> io::Result<Self> {
        // SAFETY: the file lives in a directory private to this server; other processes modify
        // it only through the same atomic word operations, never by resizing or plain writes.
        let mut map = unsafe { MmapMut::map_mut(file)? };
        if map.len() % 8 != 0 || map.len() < HEADER * 8 {
            return Err(io::Error::new(
                io::ErrorKind::InvalidData,
                "shared state file has an invalid size",
            ));
        }
        let len = map.len() / 8;
        // Mappings are page-aligned, so the pointer is aligned for `AtomicU64`.
        let ptr = map.as_mut_ptr().cast::<AtomicU64>().cast_const();
        Ok(Self {
            _map: map,
            ptr,
            len,
        })
    }

    fn all(&self) -> &[AtomicU64] {
        // SAFETY: `ptr` points to `len` initialized, 8-byte-aligned words of the mapping owned by
        // `self`, valid for as long as `self`; `AtomicU64` has the size and alignment of `u64`
        // and allows shared mutation.
        unsafe { std::slice::from_raw_parts(self.ptr, self.len) }
    }
}

fn now_us() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_or(0, |d| u64::try_from(d.as_micros()).unwrap_or(u64::MAX))
}

/// Creates the shared state file at `path` (which must not exist), with a fresh secret.
pub fn create(path: &Path) -> io::Result<()> {
    let mut options = OpenOptions::new();
    options.read(true).write(true).create_new(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        // Only this user's processes may read or change the limits.
        options.mode(0o600);
    }
    let file = options.open(path)?;
    file.set_len(((HEADER + RATE_SLOTS) * 8) as u64)?;
    let words = Words::map(&file)?;
    let header = words.all();
    let random = RandomState::new();
    header[2].store(now_us(), Ordering::Relaxed);
    header[3].store(random.hash_one(1u64), Ordering::Relaxed);
    header[4].store(random.hash_one(2u64), Ordering::Relaxed);
    header[5].store(RATE_SLOTS as u64, Ordering::Relaxed);
    header[1].store(VERSION, Ordering::Relaxed);
    header[0].store(MAGIC, Ordering::Release);
    Ok(())
}

/// Rate-limit state shared by the processes that opened the same file.
pub struct RateTable {
    words: Words,
    base_us: u64,
    secret: [u64; 2],
}

impl fmt::Debug for RateTable {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("RateTable")
            .field("slots", &(self.words.len - HEADER))
            .finish_non_exhaustive()
    }
}

enum Decision {
    Allow(u64),
    Refuse(Duration),
}

fn unpack(word: u64) -> (u16, u64) {
    ((word >> TIME_BITS) as u16, word & TIME_MASK)
}

fn pack(fingerprint: u16, tat: u64) -> u64 {
    (u64::from(fingerprint) << TIME_BITS) | (tat & TIME_MASK)
}

impl RateTable {
    /// Opens a file made by [`create`].
    pub fn open(path: &Path) -> io::Result<Self> {
        let file = OpenOptions::new().read(true).write(true).open(path)?;
        let words = Words::map(&file)?;
        let header = words.all();
        let invalid = |what: &str| io::Error::new(io::ErrorKind::InvalidData, what.to_string());
        if header[0].load(Ordering::Acquire) != MAGIC {
            return Err(invalid("not a Ferro shared state file"));
        }
        if header[1].load(Ordering::Relaxed) != VERSION {
            return Err(invalid("shared state file from another Ferro version"));
        }
        let slots = header[5].load(Ordering::Relaxed);
        if !slots.is_power_of_two() || slots as usize + HEADER != words.len {
            return Err(invalid("shared state file has an invalid layout"));
        }
        let base_us = header[2].load(Ordering::Relaxed);
        let secret = [
            header[3].load(Ordering::Relaxed),
            header[4].load(Ordering::Relaxed),
        ];
        Ok(Self {
            words,
            base_us,
            secret,
        })
    }

    fn slots(&self) -> &[AtomicU64] {
        &self.words.all()[HEADER..]
    }

    fn hash(&self, scope: u32, client: IpAddr) -> u64 {
        // SipHash with the per-run secret as the first input: the same in every process of this
        // server, unpredictable to clients.
        let mut hasher = DefaultHasher::new();
        self.secret.hash(&mut hasher);
        scope.hash(&mut hasher);
        client.hash(&mut hasher);
        hasher.finish()
    }

    /// Takes one request from `client`'s budget of `count` per `period` in `scope`.
    /// `Some(Ok(()))`: allowed; `Some(Err(retry_after))`: refused; `None`: no slot was free for
    /// this client (the caller falls back to its own limiter).
    pub fn check(
        &self,
        scope: u32,
        client: IpAddr,
        count: u32,
        period: Duration,
    ) -> Option<Result<(), Duration>> {
        self.check_at(scope, client, count, period, now_us())
    }

    /// [`Self::check`] at a given time (µs since the Unix epoch), for tests.
    pub fn check_at(
        &self,
        scope: u32,
        client: IpAddr,
        count: u32,
        period: Duration,
        now_unix_us: u64,
    ) -> Option<Result<(), Duration>> {
        let now = now_unix_us.saturating_sub(self.base_us) & TIME_MASK;
        let period_us = u64::try_from(period.as_micros())
            .unwrap_or(TIME_MASK)
            .min(TIME_MASK / 2);
        let interval = (period_us / u64::from(count.max(1))).max(1);
        let decide = |tat: u64| {
            let tat = tat.max(now);
            let next = tat + interval;
            if next - now > period_us {
                let wait_us = next - period_us - now;
                Decision::Refuse(Duration::from_secs(wait_us.div_ceil(1_000_000).max(1)))
            } else {
                Decision::Allow(next)
            }
        };
        let hash = self.hash(scope, client);
        let fingerprint = ((hash >> TIME_BITS) as u16).max(1);
        let slots = self.slots();
        let mask = slots.len() - 1;
        let start = hash as usize & mask;
        'attempt: for _attempt in 0..4 {
            // The client's own entry first, so a free slot earlier in the window never hides it.
            for offset in 0..PROBES {
                let slot = &slots[(start + offset) & mask];
                let mut current = slot.load(Ordering::Acquire);
                loop {
                    let (owner, tat) = unpack(current);
                    if current == 0 || owner != fingerprint {
                        break;
                    }
                    match decide(tat) {
                        Decision::Refuse(wait) => return Some(Err(wait)),
                        Decision::Allow(next) => match slot.compare_exchange_weak(
                            current,
                            pack(fingerprint, next),
                            Ordering::AcqRel,
                            Ordering::Acquire,
                        ) {
                            Ok(_) => return Some(Ok(())),
                            Err(actual) => current = actual,
                        },
                    }
                }
            }
            // Then an empty or expired slot (an expired entry is a full bucket: nothing to keep).
            // Another process may claim this client's entry meanwhile (its first requests arrive
            // everywhere at once): finding it, or losing a claim, starts over so the client never
            // gets a second entry and a second budget.
            for offset in 0..PROBES {
                let slot = &slots[(start + offset) & mask];
                let current = slot.load(Ordering::Acquire);
                let (owner, tat) = unpack(current);
                if current != 0 && owner == fingerprint {
                    continue 'attempt;
                }
                if current != 0 && tat > now {
                    continue;
                }
                let Decision::Allow(next) = decide(now) else {
                    unreachable!("a full bucket always has a token");
                };
                match slot.compare_exchange(
                    current,
                    pack(fingerprint, next),
                    Ordering::AcqRel,
                    Ordering::Acquire,
                ) {
                    Ok(_) => return Some(Ok(())),
                    Err(_) => continue 'attempt,
                }
            }
        }
        None
    }

    /// Slots holding a client that is not back to a full budget at `now_unix_us`.
    pub fn occupied_at(&self, now_unix_us: u64) -> usize {
        let now = now_unix_us.saturating_sub(self.base_us) & TIME_MASK;
        self.slots()
            .iter()
            .filter(|slot| {
                let current = slot.load(Ordering::Relaxed);
                current != 0 && unpack(current).1 > now
            })
            .count()
    }
}

/// A read-only map of a private temporary file (a spooled request body, Feature 026), with the
/// file it maps. Fields drop in order: unmap, then close.
#[derive(Debug)]
pub struct MappedFile {
    map: memmap2::Mmap,
    _file: File,
}

impl MappedFile {
    /// Maps `file`, which must be an unnamed temporary file only this process writes, and which
    /// is no longer written: nothing can then change or truncate it under the map. An empty
    /// file cannot be mapped (an error).
    pub fn map_private(file: File) -> io::Result<Self> {
        // SAFETY: the caller guarantees the file is private to this process and no longer
        // written, so the mapped bytes cannot change while the map exists.
        let map = unsafe { memmap2::Mmap::map(&file)? };
        Ok(Self { map, _file: file })
    }
}

impl AsRef<[u8]> for MappedFile {
    fn as_ref(&self) -> &[u8] {
        &self.map
    }
}
