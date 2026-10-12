//! Rate limiter cost and memory under many clients (Feature 006 SC-004).
//!
//! `cargo run --release -p ferro-core --example limiter_bench`

use std::net::IpAddr;
use std::time::{Duration, Instant};

use ferro_core::security::ratelimit::{Limiter, RateSpec};

fn main() {
    let limiter = Limiter::default();
    let spec = RateSpec::parse("100/minute").expect("spec");
    let clients: Vec<IpAddr> = (0..1000u32)
        .map(|i| IpAddr::from([198, 51, (i >> 8) as u8, i as u8]))
        .collect();
    let checks = 2_000_000usize;
    let start = Instant::now();
    let mut allowed = 0usize;
    for i in 0..checks {
        // Simulated clock: 2 million requests spread over 10 minutes.
        let now = start + Duration::from_micros((i as u64) * 300);
        if limiter
            .check(1, clients[i % clients.len()], spec, now)
            .is_ok()
        {
            allowed += 1;
        }
    }
    let elapsed = start.elapsed();
    let per_check = elapsed.as_nanos() as f64 / checks as f64;
    // A burst of new addresses, then the same 1,000 again: idle buckets must be evicted.
    let later = start + Duration::from_secs(3600);
    for i in 0..100_000u32 {
        let address = IpAddr::from([10, (i >> 16) as u8, (i >> 8) as u8, i as u8]);
        let _ = limiter.check(1, address, spec, later);
    }
    let after_burst = limiter.len();
    let much_later = later + Duration::from_secs(3600);
    for client in &clients {
        let _ = limiter.check(1, *client, spec, much_later);
    }
    println!(
        "checks={checks} clients={} allowed={allowed} ns_per_check={per_check:.0} \
         buckets_after_burst={after_burst} buckets_later={}",
        clients.len(),
        limiter.len()
    );
}
