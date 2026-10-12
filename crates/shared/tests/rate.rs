//! Feature 015 US1: the shared GCRA rate table.

use std::net::IpAddr;
use std::path::PathBuf;
use std::sync::Arc;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use ferro_shared::{RateTable, create};

fn table() -> (tempfile::TempDir, PathBuf) {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("rates.shm");
    create(&path).unwrap();
    (dir, path)
}

fn now_us() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap()
        .as_micros() as u64
}

fn ip(text: &str) -> IpAddr {
    text.parse().unwrap()
}

const SECOND: u64 = 1_000_000;

#[test]
fn burst_then_refill_like_a_token_bucket() {
    let (_dir, path) = table();
    let rates = RateTable::open(&path).unwrap();
    let t0 = now_us();
    let minute = Duration::from_secs(60);
    for _ in 0..10 {
        assert_eq!(
            rates.check_at(0, ip("10.0.0.1"), 10, minute, t0),
            Some(Ok(()))
        );
    }
    // Empty: the next token comes 6 s later.
    assert_eq!(
        rates.check_at(0, ip("10.0.0.1"), 10, minute, t0),
        Some(Err(Duration::from_secs(6)))
    );
    assert_eq!(
        rates.check_at(0, ip("10.0.0.1"), 10, minute, t0 + 5 * SECOND),
        Some(Err(Duration::from_secs(1)))
    );
    assert_eq!(
        rates.check_at(0, ip("10.0.0.1"), 10, minute, t0 + 6 * SECOND),
        Some(Ok(()))
    );
    // Other clients and other scopes have their own budgets.
    assert_eq!(
        rates.check_at(0, ip("10.0.0.2"), 10, minute, t0),
        Some(Ok(()))
    );
    assert_eq!(
        rates.check_at(1, ip("10.0.0.1"), 10, minute, t0),
        Some(Ok(()))
    );
    // Once the client's arrival time has passed, the bucket is full again and the slot free.
    assert_eq!(rates.occupied_at(t0 + 67 * SECOND), 0);
    for _ in 0..10 {
        assert_eq!(
            rates.check_at(0, ip("10.0.0.1"), 10, minute, t0 + 70 * SECOND),
            Some(Ok(()))
        );
    }
}

#[test]
fn handles_opened_separately_share_budgets() {
    let (_dir, path) = table();
    let first = RateTable::open(&path).unwrap();
    let second = RateTable::open(&path).unwrap();
    let t0 = now_us();
    let period = Duration::from_secs(1);
    let client = ip("2001:db8::7");
    let allowed = (0..6)
        .map(|i| {
            let handle = if i % 2 == 0 { &first } else { &second };
            handle.check_at(3, client, 4, period, t0)
        })
        .filter(|r| *r == Some(Ok(())))
        .count();
    assert_eq!(allowed, 4);
}

#[test]
fn concurrent_requests_never_exceed_the_budget() {
    let (_dir, path) = table();
    let allowed = Arc::new(AtomicUsize::new(0));
    let t0 = now_us();
    let threads: Vec<_> = (0..8)
        .map(|_| {
            let path = path.clone();
            let allowed = allowed.clone();
            std::thread::spawn(move || {
                let rates = RateTable::open(&path).unwrap();
                for _ in 0..500 {
                    if rates.check_at(0, ip("192.0.2.1"), 100, Duration::from_secs(3600), t0)
                        == Some(Ok(()))
                    {
                        allowed.fetch_add(1, Ordering::SeqCst);
                    }
                }
            })
        })
        .collect();
    for thread in threads {
        thread.join().unwrap();
    }
    assert_eq!(allowed.load(Ordering::SeqCst), 100);
}

#[test]
fn many_clients_fit() {
    let (_dir, path) = table();
    let rates = RateTable::open(&path).unwrap();
    let t0 = now_us();
    let mut refused_or_full = 0;
    for n in 0..5_000u32 {
        let client = IpAddr::from(n.to_be_bytes());
        if rates.check_at(0, client, 1, Duration::from_secs(60), t0) != Some(Ok(())) {
            refused_or_full += 1;
        }
    }
    // A 16-bit fingerprint collision inside a probe window merges two clients (about one in
    // twenty runs at this load), which makes the second one look spent.
    assert!(
        refused_or_full <= 3,
        "{refused_or_full} of 5,000 clients refused"
    );
    assert!(rates.occupied_at(t0) >= 4_997);
}

#[test]
fn rejects_foreign_files() {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("other");
    std::fs::write(&path, vec![0u8; 4096]).unwrap();
    let err = RateTable::open(&path).unwrap_err().to_string();
    assert!(err.contains("not a Ferro shared state file"), "{err}");
    let (_dir, existing) = table();
    assert!(
        create(&existing).is_err(),
        "never overwrites an existing file"
    );
}
