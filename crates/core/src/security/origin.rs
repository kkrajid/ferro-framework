//! Cross-origin request protection (CSRF) from fetch metadata and `Origin` (research R3).

/// `scheme://host[:port]` in lowercase without a default port, or `None` when `value` is not an
/// http(s) origin (`null`, a bare host, a URL with a path).
pub fn normalize_origin(value: &str) -> Option<String> {
    let value = value.trim();
    let value = value.strip_suffix('/').unwrap_or(value);
    let (scheme, rest) = value.split_once("://")?;
    let scheme = scheme.to_ascii_lowercase();
    if (scheme != "http" && scheme != "https")
        || rest.is_empty()
        || rest.contains(['/', '?', '#', '@', ' '])
    {
        return None;
    }
    let rest = rest.to_ascii_lowercase();
    let host = match rest.rsplit_once(':') {
        Some((host, port)) if !port.is_empty() && port.bytes().all(|b| b.is_ascii_digit()) => {
            let default =
                (scheme == "http" && port == "80") || (scheme == "https" && port == "443");
            if default {
                host.to_string()
            } else {
                rest.clone()
            }
        }
        _ => rest.clone(),
    };
    if host.is_empty() {
        return None;
    }
    Some(format!("{scheme}://{host}"))
}

fn safe(method: &str) -> bool {
    matches!(method, "GET" | "HEAD" | "OPTIONS" | "TRACE")
}

/// Refuses state-changing requests a browser sends on behalf of another site.
#[derive(Debug, Clone, Default)]
pub struct OriginCheck {
    trusted: Vec<String>,
}

impl OriginCheck {
    pub fn new(trusted: Vec<String>) -> Result<Self, String> {
        let trusted = trusted
            .iter()
            .map(|origin| {
                normalize_origin(origin).ok_or_else(|| {
                    format!("'{origin}' is not an origin (expected scheme://host[:port])")
                })
            })
            .collect::<Result<_, _>>()?;
        Ok(Self { trusted })
    }

    /// Whether a request may proceed. `scheme` and `host` describe the request's own origin;
    /// `cors_allowed` accepts origins the CORS configuration allows.
    pub fn allows(
        &self,
        method: &str,
        scheme: &str,
        host: Option<&str>,
        sec_fetch_site: Option<&str>,
        origin: Option<&str>,
        cors_allowed: &dyn Fn(&str) -> bool,
    ) -> bool {
        if safe(method) {
            return true;
        }
        if let Some(site) = sec_fetch_site
            && (site.eq_ignore_ascii_case("same-origin") || site.eq_ignore_ascii_case("none"))
        {
            return true;
        }
        let Some(origin) = origin else {
            // No browser metadata at all: not a cross-site browser request.
            return sec_fetch_site.is_none();
        };
        let Some(origin) = normalize_origin(origin) else {
            return false; // `null` and malformed origins
        };
        if host.and_then(|h| normalize_origin(&format!("{scheme}://{h}"))) == Some(origin.clone()) {
            return true;
        }
        self.trusted.contains(&origin) || cors_allowed(&origin)
    }
}
