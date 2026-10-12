//! Cross-Origin Resource Sharing decisions (research R4).

use super::origin::normalize_origin;

const DEFAULT_METHODS: [&str; 5] = ["GET", "POST", "PUT", "PATCH", "DELETE"];

#[derive(Debug, Clone)]
pub struct CorsConfig {
    any: bool,
    origins: Vec<String>,
    methods: Vec<String>,
    headers: Vec<String>,
    expose: Vec<String>,
    credentials: bool,
    max_age: u32,
}

/// The answer to a preflight request.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Preflight {
    Allowed(Vec<(String, String)>),
    Denied,
}

impl CorsConfig {
    pub fn new(
        origins: Vec<String>,
        methods: Vec<String>,
        headers: Vec<String>,
        expose: Vec<String>,
        credentials: bool,
        max_age: u32,
    ) -> Result<Self, String> {
        let any = origins.iter().any(|o| o == "*");
        if any && credentials {
            return Err(
                "CORS allow_origins=['*'] cannot be combined with allow_credentials=True; list \
                 the allowed origins"
                    .into(),
            );
        }
        let origins = origins
            .iter()
            .filter(|o| *o != "*")
            .map(|origin| {
                normalize_origin(origin).ok_or_else(|| {
                    format!(
                        "CORS origin '{origin}' is not an origin (expected scheme://host[:port])"
                    )
                })
            })
            .collect::<Result<Vec<_>, _>>()?;
        let methods = if methods.is_empty() {
            DEFAULT_METHODS.iter().map(|m| (*m).to_string()).collect()
        } else {
            methods.iter().map(|m| m.to_ascii_uppercase()).collect()
        };
        Ok(Self {
            any,
            origins,
            methods,
            headers: headers
                .iter()
                .map(|h| h.trim().to_ascii_lowercase())
                .collect(),
            expose: expose
                .iter()
                .map(|h| h.trim().to_ascii_lowercase())
                .collect(),
            credentials,
            max_age,
        })
    }

    pub fn allows(&self, origin: &str) -> bool {
        self.any || self.lists(origin)
    }

    /// Whether `origin` is named explicitly (a wildcard does not count).
    pub fn lists(&self, origin: &str) -> bool {
        normalize_origin(origin).is_some_and(|o| self.origins.contains(&o))
    }

    fn origin_headers(&self, origin: &str) -> Vec<(String, String)> {
        let mut headers = Vec::with_capacity(4);
        if self.any && !self.credentials {
            headers.push(("access-control-allow-origin".into(), "*".into()));
        } else {
            headers.push(("access-control-allow-origin".into(), origin.to_string()));
            headers.push(("vary".into(), "origin".into()));
        }
        if self.credentials {
            headers.push(("access-control-allow-credentials".into(), "true".into()));
        }
        headers
    }

    /// Answers `OPTIONS` with `Origin` and `Access-Control-Request-Method`.
    pub fn preflight(
        &self,
        origin: &str,
        method: &str,
        request_headers: Option<&str>,
    ) -> Preflight {
        if !self.allows(origin) || !self.methods.iter().any(|m| m.eq_ignore_ascii_case(method)) {
            return Preflight::Denied;
        }
        if let Some(requested) = request_headers {
            for name in requested
                .split(',')
                .map(str::trim)
                .filter(|n| !n.is_empty())
            {
                if !self.headers.iter().any(|h| h.eq_ignore_ascii_case(name)) {
                    return Preflight::Denied;
                }
            }
        }
        let mut headers = self.origin_headers(origin);
        headers.push((
            "access-control-allow-methods".into(),
            self.methods.join(", "),
        ));
        if !self.headers.is_empty() {
            headers.push((
                "access-control-allow-headers".into(),
                self.headers.join(", "),
            ));
        }
        headers.push(("access-control-max-age".into(), self.max_age.to_string()));
        if !headers.iter().any(|(n, _)| n == "vary") {
            headers.push(("vary".into(), "origin".into()));
        }
        Preflight::Allowed(headers)
    }

    /// Headers for an actual (non-preflight) response to `origin`, or `None` if not allowed.
    pub fn response_headers(&self, origin: &str) -> Option<Vec<(String, String)>> {
        if !self.allows(origin) {
            return None;
        }
        let mut headers = self.origin_headers(origin);
        if !self.expose.is_empty() {
            headers.push((
                "access-control-expose-headers".into(),
                self.expose.join(", "),
            ));
        }
        Some(headers)
    }
}
