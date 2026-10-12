//! Metrics across the worker processes of `ferro run --processes N` (Feature 015): each worker
//! publishes its metrics text to a shared folder every second; `/metrics` merges the live text of
//! the answering worker with the other workers' snapshots.

use std::collections::HashMap;
use std::fs;
use std::io;
use std::path::{Path, PathBuf};
use std::time::{Duration, SystemTime, UNIX_EPOCH};

/// How often a worker publishes its numbers.
pub(crate) const INTERVAL: Duration = Duration::from_secs(1);
/// A snapshot older than this comes from a worker that is gone: its gauges no longer count.
const LIVE_FOR_MS: u64 = 5_000;
const HEADER: &str = "# ferro-snapshot ";

fn unix_ms() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_or(0, |d| u64::try_from(d.as_millis()).unwrap_or(u64::MAX))
}

#[derive(Debug)]
pub(crate) struct Snapshots {
    dir: PathBuf,
    file: PathBuf,
    temp: PathBuf,
}

impl Snapshots {
    pub(crate) fn new(dir: &str) -> io::Result<Self> {
        let dir = PathBuf::from(dir);
        fs::create_dir_all(&dir)?;
        let pid = std::process::id();
        let name = format!("worker-{pid}-{}.prom", unix_ms());
        Ok(Self {
            file: dir.join(&name),
            temp: dir.join(format!(".{name}.tmp")),
            dir,
        })
    }

    /// Replaces this worker's snapshot (written aside, then renamed over it).
    pub(crate) fn publish(&self, text: &str) -> io::Result<()> {
        fs::write(&self.temp, format!("{HEADER}{}\n{text}", unix_ms()))?;
        fs::rename(&self.temp, &self.file)
    }

    /// `own` (this worker's live text) merged with every other worker's snapshot.
    pub(crate) fn merged(&self, own: &str) -> String {
        let now = unix_ms();
        let mut others = Vec::new();
        let Ok(entries) = fs::read_dir(&self.dir) else {
            return own.to_string();
        };
        for entry in entries.flatten() {
            let path = entry.path();
            if path == self.file || path.extension().is_none_or(|e| e != "prom") {
                continue;
            }
            if let Some(text) = read(&path) {
                let written = text
                    .lines()
                    .next()
                    .and_then(|line| line.strip_prefix(HEADER))
                    .and_then(|ms| ms.trim().parse::<u64>().ok())
                    .unwrap_or(0);
                others.push((text, now.saturating_sub(written) <= LIVE_FOR_MS));
            }
        }
        merge(own, &others)
    }
}

fn read(path: &Path) -> Option<String> {
    // A snapshot being replaced can be briefly unreadable on some systems; try again once.
    fs::read_to_string(path)
        .or_else(|_| {
            std::thread::sleep(Duration::from_millis(5));
            fs::read_to_string(path)
        })
        .ok()
}

#[derive(Clone, Copy)]
enum Rule {
    Sum,
    /// Gauges of live workers only.
    LiveSum,
    Min,
    Max,
}

fn rule(family: &str) -> Rule {
    match family {
        "ferro_http_requests_in_flight" => Rule::LiveSum,
        "ferro_process_start_time_seconds" => Rule::Min,
        "ferro_build_info" => Rule::Max,
        _ => Rule::Sum,
    }
}

struct Family {
    name: String,
    kind: String,
    /// Series keys (`name{labels}`) in first-seen order.
    order: Vec<String>,
    values: HashMap<String, f64>,
}

/// Merges exposition texts: `own` is live; `others` are `(text, live)`. Families and series keep
/// the order in which they first appear (own text first).
pub(crate) fn merge(own: &str, others: &[(String, bool)]) -> String {
    let mut families: Vec<Family> = Vec::new();
    let mut index: HashMap<String, usize> = HashMap::new();
    for (text, live) in
        std::iter::once((own, true)).chain(others.iter().map(|(t, l)| (t.as_str(), *l)))
    {
        let mut current: Option<usize> = None;
        for line in text.lines() {
            if let Some(rest) = line.strip_prefix("# TYPE ") {
                let mut parts = rest.split_whitespace();
                let (Some(name), Some(kind)) = (parts.next(), parts.next()) else {
                    continue;
                };
                let slot = *index.entry(name.to_string()).or_insert_with(|| {
                    families.push(Family {
                        name: name.to_string(),
                        kind: kind.to_string(),
                        order: Vec::new(),
                        values: HashMap::new(),
                    });
                    families.len() - 1
                });
                current = Some(slot);
                continue;
            }
            if line.starts_with('#') || line.trim().is_empty() {
                continue;
            }
            let (Some(slot), Some((key, value))) = (current, line.rsplit_once(' ')) else {
                continue;
            };
            let Ok(value) = value.parse::<f64>() else {
                continue;
            };
            let family = &mut families[slot];
            let rule = rule(&family.name);
            if matches!(rule, Rule::LiveSum) && !live {
                continue;
            }
            match family.values.get_mut(key) {
                Some(total) => {
                    *total = match rule {
                        Rule::Sum | Rule::LiveSum => *total + value,
                        Rule::Min => total.min(value),
                        Rule::Max => total.max(value),
                    }
                }
                None => {
                    family.order.push(key.to_string());
                    family.values.insert(key.to_string(), value);
                }
            }
        }
    }
    let mut out = String::with_capacity(own.len() + 256);
    for family in &families {
        out.push_str(&format!("# TYPE {} {}\n", family.name, family.kind));
        for key in &family.order {
            out.push_str(&format!("{key} {}\n", number(family.values[key])));
        }
    }
    out
}

fn number(value: f64) -> String {
    if value.fract() == 0.0 && value.abs() < 1e15 {
        format!("{value:.0}")
    } else {
        value.to_string()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const A: &str = "# TYPE ferro_http_requests_total counter\n\
        ferro_http_requests_total{route=\"/a\",method=\"GET\",status=\"2xx\"} 3\n\
        # TYPE ferro_http_request_duration_seconds histogram\n\
        ferro_http_request_duration_seconds_bucket{route=\"/a\",method=\"GET\",le=\"0.1\"} 3\n\
        ferro_http_request_duration_seconds_bucket{route=\"/a\",method=\"GET\",le=\"+Inf\"} 3\n\
        ferro_http_request_duration_seconds_sum{route=\"/a\",method=\"GET\"} 0.25\n\
        ferro_http_request_duration_seconds_count{route=\"/a\",method=\"GET\"} 3\n\
        # TYPE ferro_http_requests_in_flight gauge\n\
        ferro_http_requests_in_flight 1\n\
        # TYPE ferro_process_start_time_seconds gauge\n\
        ferro_process_start_time_seconds 1700000100.5\n\
        # TYPE ferro_build_info gauge\n\
        ferro_build_info{version=\"0.1.0\"} 1\n";

    const B: &str = "# ferro-snapshot 1\n\
        # TYPE ferro_http_requests_total counter\n\
        ferro_http_requests_total{route=\"/a\",method=\"GET\",status=\"2xx\"} 4\n\
        ferro_http_requests_total{route=\"/b\",method=\"POST\",status=\"4xx\"} 2\n\
        # TYPE ferro_http_requests_in_flight gauge\n\
        ferro_http_requests_in_flight 5\n\
        # TYPE ferro_process_start_time_seconds gauge\n\
        ferro_process_start_time_seconds 1700000000\n\
        # TYPE ferro_build_info gauge\n\
        ferro_build_info{version=\"0.1.0\"} 1\n";

    #[test]
    fn sums_counters_and_keeps_series_of_other_workers() {
        let merged = merge(A, &[(B.to_string(), true)]);
        assert!(
            merged.contains(
                "ferro_http_requests_total{route=\"/a\",method=\"GET\",status=\"2xx\"} 7\n"
            )
        );
        assert!(merged.contains(
            "ferro_http_requests_total{route=\"/b\",method=\"POST\",status=\"4xx\"} 2\n"
        ));
        assert!(merged.contains(
            "ferro_http_request_duration_seconds_sum{route=\"/a\",method=\"GET\"} 0.25\n"
        ));
        assert!(merged.contains("ferro_http_requests_in_flight 6\n"));
        assert!(merged.contains("ferro_process_start_time_seconds 1700000000\n"));
        assert!(merged.contains("ferro_build_info{version=\"0.1.0\"} 1\n"));
        assert_eq!(
            merged
                .matches("# TYPE ferro_http_requests_total counter")
                .count(),
            1
        );
        // Families keep their order; the other worker's extra series follow the own ones.
        let a = merged.find("route=\"/a\",method=\"GET\",status").unwrap();
        let b = merged.find("route=\"/b\"").unwrap();
        assert!(a < b && b < merged.find("# TYPE ferro_http_request_duration").unwrap());
    }

    #[test]
    fn gauges_of_dead_workers_are_dropped_but_counters_stay() {
        let merged = merge(A, &[(B.to_string(), false)]);
        assert!(merged.contains("ferro_http_requests_in_flight 1\n"));
        assert!(merged.contains("status=\"2xx\"} 7\n"));
    }

    #[test]
    fn snapshots_round_trip_through_files() {
        let dir = std::env::temp_dir().join(format!("ferro-snap-{}", unix_ms()));
        let path = dir.to_string_lossy().into_owned();
        let one = Snapshots::new(&path).unwrap();
        // Same process, so give the second writer a different file name.
        let two = Snapshots {
            dir: dir.clone(),
            file: dir.join("worker-0-0.prom"),
            temp: dir.join(".worker-0-0.prom.tmp"),
        };
        two.publish(B.trim_start_matches("# ferro-snapshot 1\n"))
            .unwrap();
        let merged = one.merged(A);
        assert!(merged.contains("status=\"2xx\"} 7\n"), "{merged}");
        assert!(
            merged.contains("ferro_http_requests_in_flight 6\n"),
            "fresh snapshot is live"
        );
        fs::remove_dir_all(dir).unwrap();
    }
}
