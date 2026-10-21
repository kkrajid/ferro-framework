//! The single catch-all service: limits, routing, dispatch, error envelopes, headers, access log.

use std::sync::Arc;
use std::sync::atomic::{AtomicBool, Ordering};
use std::time::{Duration, Instant};

use actix_web::http::StatusCode;
use actix_web::http::header::{HeaderName, HeaderValue};
use actix_web::{HttpRequest, HttpResponse, web};
use bytes::{Bytes, BytesMut};
use ferro_core::security::Policy;
use ferro_core::security::cors::Preflight;
use ferro_core::{Dispatcher, ErrorEnvelope, FerroRequest, FerroResponse, Method, request_id};
use ferro_router::{Match, Router};
use futures_util::StreamExt;

pub(crate) struct State {
    pub(crate) router: Router,
    pub(crate) dispatcher: Arc<dyn Dispatcher>,
    pub(crate) max_body_size: usize,
    pub(crate) request_timeout: Duration,
    pub(crate) policy: Policy,
    /// Probe addresses; `None` when disabled (Feature 011).
    pub(crate) health_path: Option<String>,
    pub(crate) ready_path: Option<String>,
    /// Set by a drain or a stop: readiness then reports `server`.
    pub(crate) draining: Arc<AtomicBool>,
    pub(crate) lifecycle: Arc<crate::lifecycle::Lifecycle>,
    /// Present when `metrics` is on (Feature 011 US5).
    pub(crate) metrics: Option<crate::metrics::Metrics>,
    /// The listener speaks TLS: requests are `https` (Feature 014).
    pub(crate) tls: bool,
}

#[derive(Clone, Copy)]
enum Probe {
    Health,
    Ready,
}

impl State {
    fn probe_for(&self, path: &str) -> Option<Probe> {
        let matches = |probe: &Option<String>| {
            probe
                .as_deref()
                .is_some_and(|p| ferro_core::settings::same_path(p, path))
        };
        if matches(&self.health_path) {
            Some(Probe::Health)
        } else if matches(&self.ready_path) {
            Some(Probe::Ready)
        } else {
            None
        }
    }
}

fn header<'r>(req: &'r HttpRequest, name: &str) -> Option<&'r str> {
    req.headers().get(name).and_then(|v| v.to_str().ok())
}

/// Every value of a header that may be repeated, joined in order with `,` (a proxy may add its
/// own `X-Forwarded-For` line after one the client sent).
fn all_values(req: &HttpRequest, name: &str) -> Option<String> {
    let values: Vec<&str> = req
        .headers()
        .get_all(name)
        .filter_map(|v| v.to_str().ok())
        .collect();
    (!values.is_empty()).then(|| values.join(","))
}

/// The request's host: the `Host` header, or the authority of an HTTP/2 request (which has no
/// `Host` header).
fn host(req: &HttpRequest) -> Option<&str> {
    header(req, "host").or_else(|| req.uri().authority().map(|a| a.as_str()))
}

enum BodyError {
    TooLarge,
    Incomplete,
}

/// What routing produced: a response to send, or a connection already upgraded to WebSocket.
enum Reply {
    Ferro(FerroResponse),
    Upgraded(HttpResponse),
}

/// Accepts a WebSocket handshake and starts the connection (Feature 018).
fn upgrade(
    req: &HttpRequest,
    payload: web::Payload,
    state: &State,
    request: FerroRequest,
) -> Reply {
    let request_id = request.request_id.clone();
    if !crate::socket::wants_upgrade(req) {
        return Reply::Ferro(
            ErrorEnvelope::new(426, &request_id)
                .with_message("This address only accepts WebSocket connections")
                .with_header("upgrade", "websocket")
                .with_header("connection", "upgrade")
                .into_response(),
        );
    }
    match actix_ws::handle(req, payload) {
        Ok((response, session, frames)) => {
            let link = Arc::new(ferro_core::SocketLink::new());
            let events = state.dispatcher.open_socket(request, link.clone());
            let frames = frames
                .max_frame_size(state.max_body_size)
                .aggregate_continuations()
                .max_continuation_size(state.max_body_size);
            actix_web::rt::spawn(crate::socket::run(
                session,
                frames,
                link,
                events,
                state.lifecycle.clone(),
            ));
            Reply::Upgraded(response)
        }
        Err(_) => Reply::Ferro(
            ErrorEnvelope::new(400, &request_id)
                .with_message("Invalid WebSocket handshake")
                .into_response(),
        ),
    }
}

pub(crate) async fn handle(
    req: HttpRequest,
    payload: web::Payload,
    state: web::Data<State>,
) -> HttpResponse {
    let started = Instant::now();
    let request_id = request_id::accept_or_generate(
        req.headers()
            .get("x-request-id")
            .and_then(|v| v.to_str().ok()),
    );
    if let Some(probe) = state.probe_for(req.uri().path()) {
        let response = answer_probe(&req, &state, probe, &request_id).await;
        let status = response.status;
        let http = into_http(response, &request_id, false, &state.policy);
        // Probes come every few seconds; at info level they would drown real traffic.
        tracing::debug!(
            target: "ferro.access",
            method = %req.method(),
            path = %req.uri().path(),
            status = status,
            request_id = %request_id,
        );
        return http;
    }
    let client = state.policy.client(
        req.peer_addr(),
        all_values(&req, "x-forwarded-for").as_deref(),
    );
    if let Some(metrics) = &state.metrics
        && ferro_core::settings::same_path(&metrics.path, req.uri().path())
    {
        let response = if metrics.allows(client.map(|c| c.ip())) {
            FerroResponse::new(200)
                .with_header("content-type", "text/plain; version=0.0.4; charset=utf-8")
                .with_header("cache-control", "no-store")
                .with_body(metrics.respond(&state.lifecycle, state.dispatcher.as_ref()))
        } else {
            // As if metrics were off: nothing tells an outsider they exist.
            ErrorEnvelope::new(404, &request_id).into_response()
        };
        let status = response.status;
        let http = into_http(response, &request_id, false, &state.policy);
        tracing::debug!(
            target: "ferro.access",
            method = %req.method(),
            path = %req.uri().path(),
            status = status,
            request_id = %request_id,
        );
        return http;
    }
    if let Some(response) = site_check(&req, &state, &request_id) {
        let status = response.status;
        let http = into_http(response, &request_id, false, &state.policy);
        tracing::info!(
            target: "ferro.access",
            method = %req.method(),
            path = %req.uri().path(),
            status = status,
            request_id = %request_id,
            client = %client.map(|a| a.ip().to_string()).unwrap_or_default(),
        );
        return http;
    }
    let in_flight = state.lifecycle.enter();
    let origin = header(&req, "origin");
    let (reply, close, route_id) = match preflight(&req, &state, &request_id, origin) {
        Some(answer) => (Reply::Ferro(answer), false, None),
        None => route(&req, payload, &state, &request_id, client, started).await,
    };
    let (status, http) = match reply {
        Reply::Ferro(mut response) => {
            for (name, value) in state.policy.cors_headers(origin) {
                if !response.has_header(&name) {
                    response.headers.push((name, value));
                }
            }
            let stream = response.stream.take().and_then(|stream| stream.take());
            // A stream of known length (files, Feature 025) is sent with `content-length`
            // instead of chunked encoding, so clients show progress and can resume.
            let length = match &stream {
                Some(_) => {
                    let length = response
                        .header("content-length")
                        .and_then(|v| v.trim().parse::<u64>().ok());
                    response
                        .headers
                        .retain(|(n, _)| !n.eq_ignore_ascii_case("content-length"));
                    length
                }
                None => None,
            };
            let status = response.status;
            let http = into_http(response, &request_id, close, &state.policy);
            let http = match (stream, length) {
                (Some(source), Some(length)) => http
                    .set_body(actix_web::body::SizedStream::new(
                        length,
                        crate::socket::chunks(source, state.lifecycle.clone()),
                    ))
                    .map_into_boxed_body(),
                // Headers go now; the body follows chunk by chunk (Feature 018).
                (Some(source), None) => http
                    .set_body(actix_web::body::BodyStream::new(crate::socket::chunks(
                        source,
                        state.lifecycle.clone(),
                    )))
                    .map_into_boxed_body(),
                (None, _) => http,
            };
            (status, http)
        }
        Reply::Upgraded(mut http) => {
            if let Ok(value) = HeaderValue::from_str(&request_id) {
                http.headers_mut()
                    .insert(HeaderName::from_static("x-request-id"), value);
            }
            (101, http)
        }
    };
    tracing::info!(
        target: "ferro.access",
        method = %req.method(),
        path = %req.uri().path(),
        status = status,
        duration_ms = %format!("{:.2}", started.elapsed().as_secs_f64() * 1000.0),
        request_id = %request_id,
        client = %client.map(|a| a.ip().to_string()).unwrap_or_default(),
    );
    if let Some(metrics) = &state.metrics {
        metrics.record(route_id, req.method().as_str(), status, started.elapsed());
    }
    in_flight.complete();
    http
}

/// Refuses a `Host` outside `allowed_hosts` (400) and redirects plain HTTP to HTTPS when
/// `https_redirect` is on (308, keeping the method and body). Probes and metrics come earlier,
/// so load balancers can reach them by address (Feature 023).
fn site_check(req: &HttpRequest, state: &State, request_id: &str) -> Option<FerroResponse> {
    if !state.policy.host_allowed(host(req)) {
        tracing::warn!(
            target: "ferro.security",
            host = %host(req).unwrap_or_default(),
            request_id = %request_id,
            "request refused: its Host is not in allowed_hosts",
        );
        return Some(
            ErrorEnvelope::new(400, request_id)
                .with_message("Invalid Host header")
                .into_response(),
        );
    }
    if state.tls {
        return None;
    }
    let scheme = state.policy.scheme(
        req.peer_addr().map(|a| a.ip()),
        header(req, "x-forwarded-proto"),
    );
    let path_and_query = req.uri().path_and_query().map_or("/", |p| p.as_str());
    let location = state
        .policy
        .https_location(scheme, host(req), path_and_query)?;
    Some(
        FerroResponse::new(308)
            .with_header("location", location)
            .with_header("cache-control", "no-store"),
    )
}

/// Liveness and readiness, answered without dispatching (no Python, middleware, rate limit or
/// CSRF check). Bodies carry only the status and failing check names.
async fn answer_probe(
    req: &HttpRequest,
    state: &State,
    probe: Probe,
    request_id: &str,
) -> FerroResponse {
    let method = req.method();
    if method != actix_web::http::Method::GET && method != actix_web::http::Method::HEAD {
        return ErrorEnvelope::new(405, request_id)
            .with_header("allow", "GET, HEAD")
            .into_response();
    }
    let (status, body) = match probe {
        Probe::Health => (200, serde_json::json!({"status": "ok"})),
        Probe::Ready => {
            let mut failing = Vec::new();
            if state.draining.load(Ordering::Acquire) {
                failing.push("server");
            }
            failing.extend(state.dispatcher.readiness().await);
            if failing.is_empty() {
                (200, serde_json::json!({"status": "ready"}))
            } else {
                (
                    503,
                    serde_json::json!({"status": "not ready", "failing": failing}),
                )
            }
        }
    };
    let response =
        FerroResponse::json(status, body.to_string()).with_header("cache-control", "no-store");
    if status == 503 {
        response.with_header("retry-after", "1")
    } else {
        response
    }
}

/// CORS preflights are answered here, for any path, without dispatching.
fn preflight(
    req: &HttpRequest,
    state: &State,
    request_id: &str,
    origin: Option<&str>,
) -> Option<FerroResponse> {
    if req.method() != actix_web::http::Method::OPTIONS || !state.policy.has_cors() {
        return None;
    }
    let origin = origin?;
    let method = header(req, "access-control-request-method")?;
    match state.policy.preflight(
        origin,
        method,
        header(req, "access-control-request-headers"),
    )? {
        Preflight::Allowed(headers) => {
            let mut response = FerroResponse::new(204);
            response.headers = headers;
            Some(response)
        }
        Preflight::Denied => Some(
            ErrorEnvelope::new(403, request_id)
                .with_message("CORS preflight refused")
                .into_response(),
        ),
    }
}

async fn route(
    req: &HttpRequest,
    payload: web::Payload,
    state: &State,
    request_id: &str,
    client: Option<std::net::SocketAddr>,
    started: Instant,
) -> (Reply, bool, Option<usize>) {
    // One deadline for receiving the body and running the handler (Feature 011 FR-015).
    let deadline = tokio::time::Instant::from_std(started) + state.request_timeout;
    let raw_path = req.uri().path();
    let query = req.query_string();
    match state
        .router
        .lookup(Method::parse(req.method().as_str()), raw_path)
    {
        Match::Found { route_id, params } => {
            if let Err(retry) =
                state
                    .policy
                    .rate_check(route_id, client.map(|c| c.ip()), Instant::now())
            {
                let envelope = ErrorEnvelope::new(429, request_id)
                    .with_header("retry-after", retry.as_secs().to_string());
                return (
                    Reply::Ferro(envelope.into_response()),
                    false,
                    Some(route_id),
                );
            }
            let scheme = if state.tls {
                "https"
            } else {
                state.policy.scheme(
                    req.peer_addr().map(|a| a.ip()),
                    header(req, "x-forwarded-proto"),
                )
            };
            let websocket = state.policy.is_websocket(route_id);
            // A WebSocket handshake is checked like an unsafe request: a page on another site
            // must not open a socket carrying the user's cookies (Feature 018).
            let checked_method = if websocket {
                "WEBSOCKET"
            } else {
                req.method().as_str()
            };
            if !state.policy.csrf_allows(
                route_id,
                checked_method,
                scheme,
                host(req),
                header(req, "sec-fetch-site"),
                header(req, "origin"),
                req.headers().contains_key("cookie"),
            ) {
                let envelope = ErrorEnvelope::new(403, request_id)
                    .with_message("Cross-origin request refused");
                return (
                    Reply::Ferro(envelope.into_response()),
                    false,
                    Some(route_id),
                );
            }
            if websocket {
                let request = FerroRequest {
                    route_id,
                    method: req.method().as_str().to_string(),
                    raw_path: raw_path.to_string(),
                    query_string: query.to_string(),
                    path_params: params,
                    headers: request_headers(req),
                    client,
                    request_id: request_id.to_string(),
                    body: Bytes::new(),
                };
                return (upgrade(req, payload, state, request), false, Some(route_id));
            }
            let body = match tokio::time::timeout_at(
                deadline,
                read_body(
                    req,
                    payload,
                    state.policy.body_limit(route_id, state.max_body_size),
                ),
            )
            .await
            {
                Ok(Ok(body)) => body,
                // The client is too slow sending its body: answer and drop the connection.
                Err(_) => {
                    return (
                        Reply::Ferro(ErrorEnvelope::new(408, request_id).into_response()),
                        true,
                        Some(route_id),
                    );
                }
                Ok(Err(BodyError::TooLarge)) => {
                    return (
                        Reply::Ferro(ErrorEnvelope::new(413, request_id).into_response()),
                        true,
                        Some(route_id),
                    );
                }
                Ok(Err(BodyError::Incomplete)) => {
                    let envelope = ErrorEnvelope::new(400, request_id)
                        .with_message("Request body could not be read");
                    return (Reply::Ferro(envelope.into_response()), true, Some(route_id));
                }
            };
            let request = FerroRequest {
                route_id,
                method: req.method().as_str().to_string(),
                raw_path: raw_path.to_string(),
                query_string: query.to_string(),
                path_params: params,
                headers: request_headers(req),
                client,
                request_id: request_id.to_string(),
                body,
            };
            let dispatched = state.dispatcher.dispatch(request);
            match tokio::time::timeout_at(deadline, dispatched).await {
                Ok(response) => (Reply::Ferro(response), false, Some(route_id)),
                // The dispatch future is dropped here, which cancels the handler.
                Err(_) => (
                    Reply::Ferro(
                        ErrorEnvelope::new(503, request_id)
                            .with_header("retry-after", "1")
                            .into_response(),
                    ),
                    false,
                    Some(route_id),
                ),
            }
        }
        Match::MethodNotAllowed { allowed } => {
            let allow: Vec<&str> = allowed.iter().map(|m| m.as_str()).collect();
            let envelope =
                ErrorEnvelope::new(405, request_id).with_header("allow", allow.join(", "));
            (Reply::Ferro(envelope.into_response()), false, None)
        }
        // A protocol-relative location ("//host", or "/\host", which browsers read the same way)
        // would be an open redirect.
        Match::Redirect { location }
            if !location.starts_with("//") && !location.starts_with("/\\") =>
        {
            let location = if query.is_empty() {
                location
            } else {
                format!("{location}?{query}")
            };
            let envelope = ErrorEnvelope::new(308, request_id).with_header("location", location);
            (Reply::Ferro(envelope.into_response()), false, None)
        }
        Match::Redirect { .. } | Match::NotFound => (
            Reply::Ferro(ErrorEnvelope::new(404, request_id).into_response()),
            false,
            None,
        ),
    }
}

async fn read_body(
    req: &HttpRequest,
    mut payload: web::Payload,
    limit: usize,
) -> Result<Bytes, BodyError> {
    let declared = req
        .headers()
        .get("content-length")
        .and_then(|v| v.to_str().ok())
        .and_then(|v| v.parse::<usize>().ok());
    if declared.is_some_and(|len| len > limit) {
        return Err(BodyError::TooLarge);
    }
    let mut body = BytesMut::with_capacity(declared.unwrap_or(0));
    while let Some(chunk) = payload.next().await {
        let chunk = chunk.map_err(|_| BodyError::Incomplete)?;
        if body.len() + chunk.len() > limit {
            return Err(BodyError::TooLarge);
        }
        body.extend_from_slice(&chunk);
    }
    Ok(body.freeze())
}

fn into_http(
    response: FerroResponse,
    request_id: &str,
    close: bool,
    policy: &Policy,
) -> HttpResponse {
    let status = StatusCode::from_u16(response.status).unwrap_or(StatusCode::INTERNAL_SERVER_ERROR);
    let mut builder = HttpResponse::build(status);
    for (name, value) in &response.headers {
        if name.eq_ignore_ascii_case("x-request-id") {
            continue;
        }
        match (
            HeaderName::from_bytes(name.as_bytes()),
            HeaderValue::from_bytes(value.as_bytes()),
        ) {
            (Ok(name), Ok(value)) => {
                builder.append_header((name, value));
            }
            _ => {
                tracing::error!(
                    target: "ferro.server",
                    request_id = %request_id,
                    "dropping response because header {name:?} is invalid"
                );
                return into_http(
                    ErrorEnvelope::new(500, request_id).into_response(),
                    request_id,
                    close,
                    policy,
                );
            }
        }
    }
    for (name, value) in policy.headers() {
        if !response.has_header(name) {
            builder.insert_header((name.as_str(), value.as_str()));
        }
    }
    builder.insert_header(("x-request-id", request_id));
    if close {
        builder.force_close();
    }
    builder.body(response.body)
}

/// Request headers for the dispatcher. An HTTP/2 request carries its host in the authority, so
/// it is added as `host` when the header is absent (handlers read the host the same way for both
/// protocols).
fn request_headers(req: &HttpRequest) -> Vec<(String, String)> {
    let mut headers: Vec<(String, String)> = req
        .headers()
        .iter()
        .map(|(n, v)| {
            (
                n.as_str().to_string(),
                String::from_utf8_lossy(v.as_bytes()).into_owned(),
            )
        })
        .collect();
    if !req.headers().contains_key("host")
        && let Some(authority) = req.uri().authority()
    {
        headers.push(("host".to_string(), authority.as_str().to_string()));
    }
    headers
}
