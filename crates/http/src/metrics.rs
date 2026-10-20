//! Request metrics (Feature 011 US5): fixed arrays allocated at start, atomic adds only on the
//! request path, rendered in the text exposition format.

use std::fmt::Write as _;
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use ferro_core::Dispatcher;
use ferro_core::metrics::{Histogram, escape_label, type_line};
use ferro_core::security::proxy::TrustedProxies;
use ferro_router::Router;

const METHODS: [&str; 8] = [
    "GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "other",
];
const CLASSES: [&str; 5] = ["1xx", "2xx", "3xx", "4xx", "5xx"];
const UNMATCHED: &str = "<unmatched>";

pub(crate) struct Metrics {
    pub(crate) path: String,
    allow: TrustedProxies,
    /// Escaped route template per slot; the last slot is for unmatched paths.
    labels: Vec<Option<String>>,
    requests: Vec<AtomicU64>,
    durations: Vec<Histogram>,
    started_at: f64,
    /// Present in a worker of `--processes N`: other workers' numbers to merge (Feature 015).
    pub(crate) snapshots: Option<crate::snapshots::Snapshots>,
}

impl Metrics {
    pub(crate) fn new(router: &Router, path: String, allow: TrustedProxies) -> Self {
        let slots = router.route_slots();
        let mut labels: Vec<Option<String>> = (0..slots)
            .map(|id| router.template(id).map(escape_label))
            .collect();
        labels.push(Some(UNMATCHED.to_string()));
        let routes = labels.len();
        Self {
            path,
            allow,
            labels,
            requests: (0..routes * METHODS.len() * CLASSES.len())
                .map(|_| AtomicU64::new(0))
                .collect(),
            durations: (0..routes * METHODS.len())
                .map(|_| Histogram::default())
                .collect(),
            started_at: SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .map_or(0.0, |d| d.as_secs_f64()),
            snapshots: None,
        }
    }

    /// The metrics response: this process's numbers, plus the other workers' when the server
    /// runs several processes.
    pub(crate) fn respond(
        &self,
        lifecycle: &crate::lifecycle::Lifecycle,
        dispatcher: &dyn Dispatcher,
    ) -> String {
        let own = self.render(lifecycle, dispatcher);
        match &self.snapshots {
            Some(snapshots) => snapshots.merged(&own),
            None => own,
        }
    }

    pub(crate) fn allows(&self, client: Option<std::net::IpAddr>) -> bool {
        client.is_some_and(|ip| self.allow.is_trusted(ip))
    }

    pub(crate) fn record(
        &self,
        route: Option<usize>,
        method: &str,
        status: u16,
        elapsed: Duration,
    ) {
        let unmatched = self.labels.len() - 1;
        let slot = route.filter(|r| *r < unmatched).unwrap_or(unmatched);
        let method = METHODS
            .iter()
            .position(|m| *m == method)
            .unwrap_or(METHODS.len() - 1);
        let class = usize::from((status / 100).clamp(1, 5) - 1);
        let series = slot * METHODS.len() + method;
        self.requests[series * CLASSES.len() + class].fetch_add(1, Ordering::Relaxed);
        self.durations[series].observe(elapsed);
    }

    pub(crate) fn render(
        &self,
        lifecycle: &crate::lifecycle::Lifecycle,
        dispatcher: &dyn Dispatcher,
    ) -> String {
        let in_flight = lifecycle.in_flight();
        let (sockets, streams) = lifecycle.open_connections();
        let mut out = String::with_capacity(4096);
        let total = "ferro_http_requests_total";
        type_line(&mut out, total, "counter");
        self.each_series(|series, labels| {
            for (class, name) in CLASSES.iter().enumerate() {
                let count = self.requests[series * CLASSES.len() + class].load(Ordering::Relaxed);
                if count > 0 {
                    let _ = writeln!(out, "{total}{{{labels},status=\"{name}\"}} {count}");
                }
            }
        });
        let duration = "ferro_http_request_duration_seconds";
        type_line(&mut out, duration, "histogram");
        self.each_series(|series, labels| {
            if self.durations[series].count() > 0 {
                self.durations[series].render(&mut out, duration, &labels);
            }
        });
        type_line(&mut out, "ferro_http_requests_in_flight", "gauge");
        let _ = writeln!(out, "ferro_http_requests_in_flight {in_flight}");
        type_line(&mut out, "ferro_websocket_connections", "gauge");
        let _ = writeln!(out, "ferro_websocket_connections {sockets}");
        type_line(&mut out, "ferro_http_streams_open", "gauge");
        let _ = writeln!(out, "ferro_http_streams_open {streams}");
        dispatcher.write_metrics(&mut out);
        type_line(&mut out, "ferro_process_start_time_seconds", "gauge");
        let _ = writeln!(out, "ferro_process_start_time_seconds {}", self.started_at);
        type_line(&mut out, "ferro_build_info", "gauge");
        let _ = writeln!(
            out,
            "ferro_build_info{{version=\"{}\"}} 1",
            python_version(env!("CARGO_PKG_VERSION"))
        );
        out
    }

    /// Calls `f(series index, "route=...,method=...")` for every route slot and method.
    fn each_series(&self, mut f: impl FnMut(usize, String)) {
        for (slot, label) in self.labels.iter().enumerate() {
            let Some(route) = label else { continue };
            for (index, method) in METHODS.iter().enumerate() {
                f(
                    slot * METHODS.len() + index,
                    format!("route=\"{route}\",method=\"{method}\""),
                );
            }
        }
    }
}

/// The version as Python packages spell it (PEP 440), the one `ferro --version` shows: Cargo's
/// `0.5.0-rc.1` is `0.5.0rc1`.
fn python_version(cargo: &str) -> String {
    let Some((release, pre)) = cargo.split_once('-') else {
        return cargo.to_string();
    };
    let (kind, number) = pre.split_once('.').unwrap_or((pre, ""));
    let short = match kind {
        "alpha" => "a",
        "beta" => "b",
        "rc" => "rc",
        _ => return cargo.to_string(),
    };
    format!("{release}{short}{number}")
}

#[cfg(test)]
mod version_tests {
    use super::python_version;

    #[test]
    fn cargo_versions_are_spelled_as_python_versions() {
        assert_eq!(python_version("0.5.0-rc.1"), "0.5.0rc1");
        assert_eq!(python_version("1.0.0-alpha.2"), "1.0.0a2");
        assert_eq!(python_version("1.0.0-beta.3"), "1.0.0b3");
        assert_eq!(python_version("1.0.0"), "1.0.0");
    }
}
