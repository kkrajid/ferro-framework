//! Runs routed requests through Python endpoints via the asyncio bridge, and answers the
//! engine's readiness question with a cached database check (Feature 011 research R2).

use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use ferro_core::{Dispatcher, FerroRequest, FerroResponse, LocalBoxFuture};
use ferro_database::{Job, Statement};
use tokio::sync::{Notify, oneshot};

use crate::bridge::Bridge;
use crate::db;

/// How long a database check's answer is reused.
const CHECK_TTL: Duration = Duration::from_secs(1);
/// How long a database check may take before it counts as failed.
const CHECK_LIMIT: Duration = Duration::from_millis(750);

pub(crate) struct PyDispatcher {
    pub(crate) bridge: Bridge,
    pub(crate) database_probe: Arc<DatabaseProbe>,
}

impl Dispatcher for PyDispatcher {
    fn dispatch(&self, request: FerroRequest) -> LocalBoxFuture<FerroResponse> {
        Box::pin(self.bridge.submit(request))
    }

    fn open_socket(
        &self,
        request: FerroRequest,
        link: Arc<ferro_core::SocketLink>,
    ) -> Arc<dyn ferro_core::SocketEvents> {
        self.bridge.open_socket(request, link)
    }

    fn write_metrics(&self, out: &mut String) {
        db::write_metrics(out);
    }

    fn readiness(&self) -> LocalBoxFuture<Vec<&'static str>> {
        let probe = self.database_probe.clone();
        Box::pin(async move {
            if probe.healthy().await {
                Vec::new()
            } else {
                vec!["database"]
            }
        })
    }
}

/// Answers "does the database work?" for readiness probes: one `SELECT 1` at a time, its answer
/// reused for a second, so a flood of probes cannot load the database.
#[derive(Default)]
pub(crate) struct DatabaseProbe {
    last: Mutex<Option<(Instant, bool)>>,
    running: AtomicBool,
    done: Notify,
}

/// Clears the "check running" flag even if the probing request is abandoned mid-check.
struct Running<'a>(&'a DatabaseProbe);

impl Drop for Running<'_> {
    fn drop(&mut self) {
        self.0.running.store(false, Ordering::Release);
        self.0.done.notify_waiters();
    }
}

impl DatabaseProbe {
    async fn healthy(&self) -> bool {
        loop {
            if let Some(answer) = self.fresh() {
                return answer;
            }
            let finished = self.done.notified();
            if self.running.swap(true, Ordering::AcqRel) {
                // Another probe is checking: wait for its answer, then look again.
                let wait = CHECK_LIMIT + Duration::from_millis(100);
                let _ = tokio::time::timeout(wait, finished).await;
                continue;
            }
            let _running = Running(self);
            let healthy = check().await;
            *self.last.lock().unwrap_or_else(|e| e.into_inner()) = Some((Instant::now(), healthy));
            return healthy;
        }
    }

    fn fresh(&self) -> Option<bool> {
        let last = *self.last.lock().unwrap_or_else(|e| e.into_inner());
        last.filter(|(at, _)| at.elapsed() < CHECK_TTL)
            .map(|(_, healthy)| healthy)
    }
}

/// `SELECT 1` on the configured database within [`CHECK_LIMIT`]; `true` without a database.
async fn check() -> bool {
    let Some(database) = db::connected() else {
        return true;
    };
    let (sender, receiver) = oneshot::channel();
    let cancelled = Arc::new(AtomicBool::new(false));
    database.backend.submit(
        Job::Query(Statement::new("SELECT 1", Vec::new())),
        cancelled.clone(),
        Box::new(move |result| {
            let _ = sender.send(result.is_ok());
        }),
    );
    match tokio::time::timeout(CHECK_LIMIT, receiver).await {
        Ok(Ok(healthy)) => healthy,
        _ => {
            // Not started yet: skip it instead of piling checks onto a stuck database.
            cancelled.store(true, Ordering::Release);
            false
        }
    }
}
