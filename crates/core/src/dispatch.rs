use std::future::Future;
use std::pin::Pin;

use crate::{FerroRequest, FerroResponse};

/// A future that stays on the thread that created it (engine workers are single-threaded).
pub type LocalBoxFuture<T> = Pin<Box<dyn Future<Output = T> + 'static>>;

/// Executes a routed request. Implemented by the Python bridge; engine adapters only see this
/// trait.
///
/// Dropping the returned future means the request was abandoned (timeout, client disconnect,
/// shutdown); implementations must stop the work they started.
pub trait Dispatcher: Send + Sync + 'static {
    fn dispatch(&self, request: FerroRequest) -> LocalBoxFuture<FerroResponse>;

    /// Names of the failing checks of what this dispatcher depends on (the database), for the
    /// readiness probe (Feature 011). Empty when everything answers.
    fn readiness(&self) -> LocalBoxFuture<Vec<&'static str>> {
        Box::pin(async { Vec::new() })
    }

    /// Appends this dispatcher's own series (database queries) to a metrics response, in the
    /// text exposition format (Feature 011).
    fn write_metrics(&self, _out: &mut String) {}
}
