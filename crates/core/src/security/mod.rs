//! Engine-independent security policies (Feature 006): cross-origin protection, CORS, rate
//! limiting, trusted proxies and cookie parsing. The HTTP adapter applies a [`Policy`] before
//! dispatch, so refused requests never reach Python.

pub mod cookies;
pub mod cors;
pub mod hosts;
pub mod origin;
pub mod proxy;
pub mod ratelimit;

use std::net::{IpAddr, SocketAddr};
use std::time::{Duration, Instant};

use crate::headers::security_headers;
use crate::settings::{Settings, SettingsError};
use cors::{CorsConfig, Preflight};
use hosts::AllowedHosts;
use origin::OriginCheck;
use proxy::TrustedProxies;
use ratelimit::{Limiter, RateSpec};

/// Per-route security options.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct RouteOptions {
    /// Apply the cross-origin check to unsafe methods.
    pub csrf: bool,
    /// Replaces the global rate limit for this route.
    pub rate_limit: Option<RateSpec>,
    /// A WebSocket endpoint: GET requests must upgrade (Feature 018).
    pub websocket: bool,
}

impl Default for RouteOptions {
    fn default() -> Self {
        Self {
            csrf: true,
            rate_limit: None,
            websocket: false,
        }
    }
}

/// Everything the HTTP adapter needs to protect requests, built once from validated settings.
#[derive(Debug)]
pub struct Policy {
    csrf: bool,
    origins: OriginCheck,
    cors: Option<CorsConfig>,
    proxies: TrustedProxies,
    rate_limit: Option<RateSpec>,
    limiter: Limiter,
    headers: Vec<(String, String)>,
    routes: Vec<RouteOptions>,
    hosts: AllowedHosts,
    https_redirect: bool,
}

impl Policy {
    pub fn new(settings: &Settings, routes: Vec<RouteOptions>) -> Result<Self, SettingsError> {
        let origins = OriginCheck::new(settings.trusted_origins.clone())
            .map_err(|e| SettingsError::new("trusted_origins", e))?;
        let proxies = TrustedProxies::parse(&settings.trusted_proxies)
            .map_err(|e| SettingsError::new("trusted_proxies", e))?;
        let rate_limit = settings
            .rate_limit
            .as_deref()
            .map(RateSpec::parse)
            .transpose()
            .map_err(|e| SettingsError::new("rate_limit", e))?;
        let cors = settings
            .cors
            .as_ref()
            .map(|c| {
                CorsConfig::new(
                    c.allow_origins.clone(),
                    c.allow_methods.clone(),
                    c.allow_headers.clone(),
                    c.expose_headers.clone(),
                    c.allow_credentials,
                    c.max_age,
                )
            })
            .transpose()
            .map_err(|e| SettingsError::new("cors", e))?;
        let mut headers: Vec<(String, String)> = security_headers()
            .iter()
            .map(|(n, v)| ((*n).to_string(), (*v).to_string()))
            .collect();
        if settings.hsts {
            if settings.hsts_preload
                && (!settings.hsts_include_subdomains || settings.hsts_max_age < 31_536_000)
            {
                return Err(SettingsError::new(
                    "hsts_preload",
                    "needs hsts_include_subdomains and hsts_max_age of at least 31536000 \
                     (one year), as the preload lists require",
                ));
            }
            let mut value = format!("max-age={}", settings.hsts_max_age);
            if settings.hsts_include_subdomains {
                value.push_str("; includeSubDomains");
            }
            if settings.hsts_preload {
                value.push_str("; preload");
            }
            headers.push(("strict-transport-security".into(), value));
        }
        let hosts = AllowedHosts::parse(&settings.allowed_hosts)
            .map_err(|e| SettingsError::new("allowed_hosts", e))?;
        if let Some(policy) = &settings.content_security_policy {
            if !crate::headers::is_valid_header_value(policy) {
                return Err(SettingsError::new(
                    "content_security_policy",
                    "contains control characters",
                ));
            }
            headers.push(("content-security-policy".into(), policy.clone()));
        }
        let limiter = match &settings.shared_state {
            Some(path) => Limiter::shared(
                ferro_shared::RateTable::open(std::path::Path::new(path)).map_err(|err| {
                    SettingsError::new("shared_state", format!("cannot open '{path}': {err}"))
                })?,
            ),
            None => Limiter::default(),
        };
        Ok(Self {
            csrf: settings.csrf,
            origins,
            cors,
            proxies,
            rate_limit,
            limiter,
            headers,
            routes,
            hosts,
            https_redirect: settings.https_redirect,
        })
    }

    /// Whether the `Host` header names this site (always, when `allowed_hosts` is empty).
    pub fn host_allowed(&self, host: Option<&str>) -> bool {
        self.hosts.allows(host)
    }

    /// The HTTPS address to redirect a plain-HTTP request to, when `https_redirect` is on.
    pub fn https_location(
        &self,
        scheme: &str,
        host: Option<&str>,
        path_and_query: &str,
    ) -> Option<String> {
        if !self.https_redirect || scheme == "https" {
            return None;
        }
        let name = hosts::host_name(host?)?;
        Some(format!("https://{name}{path_and_query}"))
    }

    /// The client's address: the peer, or the forwarded address when the peer is a trusted
    /// proxy (port 0, as the real one is unknown).
    pub fn client(
        &self,
        peer: Option<SocketAddr>,
        forwarded_for: Option<&str>,
    ) -> Option<SocketAddr> {
        let peer = peer?;
        let ip = self.proxies.client(peer.ip(), forwarded_for);
        Some(if ip == peer.ip() {
            peer
        } else {
            SocketAddr::new(ip, 0)
        })
    }

    /// `https` when a trusted proxy says so, otherwise `http` (Ferro itself serves plain HTTP).
    pub fn scheme(&self, peer: Option<IpAddr>, forwarded_proto: Option<&str>) -> &'static str {
        match (peer, forwarded_proto) {
            (Some(peer), Some(proto))
                if self.proxies.is_trusted(peer) && proto.trim().eq_ignore_ascii_case("https") =>
            {
                "https"
            }
            _ => "http",
        }
    }

    /// The answer to a CORS preflight, when CORS is configured.
    pub fn preflight(
        &self,
        origin: &str,
        method: &str,
        request_headers: Option<&str>,
    ) -> Option<Preflight> {
        self.cors
            .as_ref()
            .map(|cors| cors.preflight(origin, method, request_headers))
    }

    /// CORS headers for an actual response to `origin`.
    pub fn cors_headers(&self, origin: Option<&str>) -> Vec<(String, String)> {
        match (&self.cors, origin) {
            (Some(cors), Some(origin)) => cors.response_headers(origin).unwrap_or_default(),
            _ => Vec::new(),
        }
    }

    /// Whether `route_id` is a WebSocket endpoint.
    pub fn is_websocket(&self, route_id: usize) -> bool {
        self.routes.get(route_id).is_some_and(|r| r.websocket)
    }

    /// Takes a token from the route's (or the global) budget of `client`.
    pub fn rate_check(
        &self,
        route_id: usize,
        client: Option<IpAddr>,
        now: Instant,
    ) -> Result<(), Duration> {
        let route_limit = self.routes.get(route_id).and_then(|r| r.rate_limit);
        let (scope, spec) = match (route_limit, self.rate_limit) {
            (Some(spec), _) => (u32::try_from(route_id + 1).unwrap_or(u32::MAX), spec),
            (None, Some(spec)) => (0, spec),
            (None, None) => return Ok(()),
        };
        match client {
            Some(client) => self.limiter.check(scope, client, spec, now),
            None => Ok(()),
        }
    }

    /// Whether the cross-origin check lets the request through.
    #[allow(clippy::too_many_arguments)]
    pub fn csrf_allows(
        &self,
        route_id: usize,
        method: &str,
        scheme: &str,
        host: Option<&str>,
        sec_fetch_site: Option<&str>,
        origin: Option<&str>,
        has_cookies: bool,
    ) -> bool {
        if !self.csrf || !self.routes.get(route_id).is_none_or(|r| r.csrf) {
            return true;
        }
        // Origins listed in the CORS configuration are trusted. A wildcard (`*`) is only trusted
        // for requests without cookies: with ambient credentials it would let any site post
        // forms on behalf of signed-in users.
        let cors = |origin: &str| {
            self.cors
                .as_ref()
                .is_some_and(|c| c.lists(origin) || (c.allows(origin) && !has_cookies))
        };
        self.origins
            .allows(method, scheme, host, sec_fetch_site, origin, &cors)
    }

    /// Security headers added to every response unless the handler set them.
    pub fn headers(&self) -> &[(String, String)] {
        &self.headers
    }

    pub fn has_cors(&self) -> bool {
        self.cors.is_some()
    }
}
