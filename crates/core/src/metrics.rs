//! Lock-free metric primitives and the text exposition format (Feature 011).

use std::fmt::Write as _;
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::Duration;

/// Upper bounds (seconds) of the duration histogram buckets; `+Inf` is implied.
pub const BUCKETS: [f64; 13] = [
    0.001, 0.0025, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0,
];

/// A duration histogram: per-bucket counts (rendered cumulatively), sum and count.
#[derive(Debug, Default)]
pub struct Histogram {
    buckets: [AtomicU64; BUCKETS.len() + 1],
    sum_nanos: AtomicU64,
    count: AtomicU64,
}

impl Histogram {
    pub fn observe(&self, duration: Duration) {
        let seconds = duration.as_secs_f64();
        let index = BUCKETS
            .iter()
            .position(|bound| seconds <= *bound)
            .unwrap_or(BUCKETS.len());
        self.buckets[index].fetch_add(1, Ordering::Relaxed);
        let nanos = u64::try_from(duration.as_nanos()).unwrap_or(u64::MAX);
        self.sum_nanos.fetch_add(nanos, Ordering::Relaxed);
        self.count.fetch_add(1, Ordering::Relaxed);
    }

    pub fn count(&self) -> u64 {
        self.count.load(Ordering::Relaxed)
    }

    /// Appends `<name>_bucket`, `<name>_sum` and `<name>_count`; `labels` is either empty or
    /// `key="value",...` (already escaped).
    pub fn render(&self, out: &mut String, name: &str, labels: &str) {
        let separator = if labels.is_empty() { "" } else { "," };
        let mut cumulative = 0;
        for (index, bucket) in self.buckets.iter().enumerate() {
            cumulative += bucket.load(Ordering::Relaxed);
            let bound = BUCKETS
                .get(index)
                .map_or_else(|| "+Inf".to_string(), |b| b.to_string());
            let _ = writeln!(
                out,
                "{name}_bucket{{{labels}{separator}le=\"{bound}\"}} {cumulative}"
            );
        }
        let sum = self.sum_nanos.load(Ordering::Relaxed) as f64 / 1e9;
        let braces = if labels.is_empty() {
            String::new()
        } else {
            format!("{{{labels}}}")
        };
        let _ = writeln!(out, "{name}_sum{braces} {sum}");
        let _ = writeln!(out, "{name}_count{braces} {}", self.count());
    }
}

/// Escapes a label value: backslash, double quote and newline.
pub fn escape_label(value: &str) -> String {
    value
        .replace('\\', "\\\\")
        .replace('"', "\\\"")
        .replace('\n', "\\n")
}

/// Appends a `# TYPE` line.
pub fn type_line(out: &mut String, name: &str, kind: &str) {
    let _ = writeln!(out, "# TYPE {name} {kind}");
}
