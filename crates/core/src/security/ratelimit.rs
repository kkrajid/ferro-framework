//! Per-client token buckets (research R5).

use std::collections::HashMap;
use std::hash::{BuildHasher, RandomState};
use std::net::IpAddr;
use std::sync::Mutex;
use std::sync::atomic::{AtomicBool, Ordering};
use std::time::{Duration, Instant};

use ferro_shared::RateTable;

/// `count` requests per `period` (`"100/minute"`).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct RateSpec {
    pub count: u32,
    pub period: Duration,
}

impl RateSpec {
    pub fn parse(text: &str) -> Result<Self, String> {
        let invalid = || {
            format!(
                "invalid rate limit '{text}': expected N/second, N/minute, N/hour or N/day with \
                 N >= 1"
            )
        };
        let (count, unit) = text.trim().split_once('/').ok_or_else(invalid)?;
        let count: u32 = count.trim().parse().map_err(|_| invalid())?;
        if count == 0 {
            return Err(invalid());
        }
        let seconds = match unit.trim().to_ascii_lowercase().trim_end_matches('s') {
            "second" | "sec" => 1,
            "minute" | "min" => 60,
            "hour" => 3600,
            "day" => 86_400,
            _ => return Err(invalid()),
        };
        Ok(Self {
            count,
            period: Duration::from_secs(seconds),
        })
    }
}

#[derive(Debug)]
struct Bucket {
    tokens: f64,
    updated: Instant,
    period: Duration,
}

#[derive(Debug, Default)]
struct Shard {
    buckets: HashMap<(u32, IpAddr), Bucket>,
    inserts: usize,
    swept: Option<Instant>,
}

impl Shard {
    fn sweep(&mut self, now: Instant) {
        self.inserts = 0;
        self.swept = Some(now);
        self.buckets
            .retain(|_, b| now.saturating_duration_since(b.updated) < b.period);
    }
}

const SHARDS: usize = 16;
/// Buckets per shard at most (65,536 in all); beyond that the least recently used is evicted.
const MAX_BUCKETS_PER_SHARD: usize = 4096;

/// The address a budget belongs to: IPv6 clients by their /64 network (one subscriber usually
/// controls a whole /64, and could otherwise rotate addresses to get fresh budgets).
pub fn client_key(client: IpAddr) -> IpAddr {
    match client {
        IpAddr::V6(v6) => match v6.to_ipv4_mapped() {
            Some(v4) => IpAddr::V4(v4),
            None => {
                let mut segments = v6.segments();
                segments[4..].fill(0);
                IpAddr::V6(segments.into())
            }
        },
        v4 => v4,
    }
}
/// Shards also sweep at least this often, so memory is returned after a burst of clients.
const SWEEP_INTERVAL: Duration = Duration::from_secs(60);

/// Token buckets keyed by `(scope, client)`. A bucket idle for a whole period is full again and
/// therefore equivalent to a missing one, so sweeps drop it; memory follows active clients.
///
/// With a shared table (Feature 015) the buckets live in memory shared by every worker process
/// of the server; the in-process buckets only take clients the shared table has no room for.
#[derive(Debug)]
pub struct Limiter {
    shards: Vec<Mutex<Shard>>,
    hasher: RandomState,
    shared: Option<RateTable>,
    overflowed: AtomicBool,
}

impl Default for Limiter {
    fn default() -> Self {
        Self {
            shards: (0..SHARDS).map(|_| Mutex::default()).collect(),
            hasher: RandomState::new(),
            shared: None,
            overflowed: AtomicBool::new(false),
        }
    }
}

impl Limiter {
    /// A limiter whose budgets are shared through `table` with other processes.
    pub fn shared(table: RateTable) -> Self {
        Self {
            shared: Some(table),
            ..Self::default()
        }
    }

    pub fn is_shared(&self) -> bool {
        self.shared.is_some()
    }

    /// Takes one token; `Err(retry_after)` when the client has none left.
    pub fn check(
        &self,
        scope: u32,
        client: IpAddr,
        spec: RateSpec,
        now: Instant,
    ) -> Result<(), Duration> {
        let client = client_key(client);
        if let Some(table) = &self.shared {
            match table.check(scope, client, spec.count, spec.period) {
                Some(result) => return result,
                None => {
                    if !self.overflowed.swap(true, Ordering::Relaxed) {
                        tracing::warn!(
                            target: "ferro.server",
                            "the shared rate-limit table is full; clients without a slot are \
                             limited per process"
                        );
                    }
                }
            }
        }
        let key = (scope, client);
        let index = (self.hasher.hash_one(key) as usize) % SHARDS;
        let mut shard = self.shards[index].lock().unwrap_or_else(|e| e.into_inner());
        let count = f64::from(spec.count);
        let period = spec.period.as_secs_f64();
        if !shard.buckets.contains_key(&key) {
            shard.inserts += 1;
            if shard.inserts >= 64.max(shard.buckets.len() / 4) {
                shard.sweep(now);
            }
            if shard.buckets.len() >= MAX_BUCKETS_PER_SHARD {
                shard.sweep(now);
                if shard.buckets.len() >= MAX_BUCKETS_PER_SHARD
                    && let Some(oldest) = shard
                        .buckets
                        .iter()
                        .min_by_key(|(_, b)| b.updated)
                        .map(|(k, _)| *k)
                {
                    shard.buckets.remove(&oldest);
                }
            }
        }
        if shard
            .swept
            .is_none_or(|t| now.saturating_duration_since(t) >= SWEEP_INTERVAL)
        {
            shard.sweep(now);
        }
        let bucket = shard.buckets.entry(key).or_insert(Bucket {
            tokens: count,
            updated: now,
            period: spec.period,
        });
        let elapsed = now.saturating_duration_since(bucket.updated).as_secs_f64();
        bucket.tokens = (bucket.tokens + elapsed * count / period).min(count);
        bucket.updated = now;
        if bucket.tokens >= 1.0 {
            bucket.tokens -= 1.0;
            return Ok(());
        }
        let wait = (1.0 - bucket.tokens) * period / count;
        Err(Duration::from_secs((wait.ceil() as u64).max(1)))
    }

    /// Number of buckets held (for tests and metrics).
    pub fn len(&self) -> usize {
        self.shards
            .iter()
            .map(|s| s.lock().unwrap_or_else(|e| e.into_inner()).buckets.len())
            .sum()
    }

    pub fn is_empty(&self) -> bool {
        self.len() == 0
    }
}
