use std::net::SocketAddr;

use bytes::Bytes;

/// One routed request as handed to a [`crate::Dispatcher`]. The body is fully read and already
/// checked against the size limit.
#[derive(Debug, Clone)]
pub struct FerroRequest {
    /// Id of the matched route, as registered with the router.
    pub route_id: usize,
    /// Method exactly as received.
    pub method: String,
    /// Path exactly as received (percent-encoded).
    pub raw_path: String,
    pub query_string: String,
    /// Decoded path parameters in pattern order.
    pub path_params: Vec<(String, String)>,
    /// Header names are lower-case; values are decoded lossily as UTF-8.
    pub headers: Vec<(String, String)>,
    pub client: Option<SocketAddr>,
    pub request_id: String,
    pub body: Bytes,
}
