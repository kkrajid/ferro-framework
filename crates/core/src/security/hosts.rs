//! Host header validation (Feature 023): requests whose `Host` is not one of the site's names are
//! refused before routing, so absolute URLs built from the request (redirects, links in
//! e-mails) and the cross-origin check never trust a name an attacker chose.

/// One allowed name: `example.com`, `.example.com` (the domain and every subdomain) or `*`.
#[derive(Debug, Clone, PartialEq, Eq)]
enum Pattern {
    Any,
    Exact(String),
    Domain(String),
}

/// The `allowed_hosts` setting. Empty allows every host (the setting is off).
#[derive(Debug, Clone, Default)]
pub struct AllowedHosts {
    patterns: Vec<Pattern>,
}

/// `Host` without the port, lower-cased, without a trailing dot; IPv6 keeps its brackets.
pub fn host_name(host: &str) -> Option<String> {
    let host = host.trim();
    if host.is_empty() {
        return None;
    }
    let name = if let Some(rest) = host.strip_prefix('[') {
        let end = rest.find(']')?;
        let after = &rest[end + 1..];
        if !(after.is_empty() || (after.starts_with(':') && port_ok(&after[1..]))) {
            return None;
        }
        format!("[{}]", &rest[..end])
    } else {
        let (name, port) = match host.rsplit_once(':') {
            Some((name, port)) => (name, Some(port)),
            None => (host, None),
        };
        if port.is_some_and(|p| !port_ok(p)) || name.contains(':') {
            return None;
        }
        name.to_string()
    };
    let name = name.trim_end_matches('.').to_ascii_lowercase();
    let valid = !name.is_empty()
        && name
            .chars()
            .all(|c| c.is_ascii_alphanumeric() || matches!(c, '.' | '-' | '_' | '[' | ']' | ':'));
    valid.then_some(name)
}

fn port_ok(port: &str) -> bool {
    !port.is_empty() && port.len() <= 5 && port.bytes().all(|b| b.is_ascii_digit())
}

impl AllowedHosts {
    pub fn parse(names: &[String]) -> Result<Self, String> {
        let mut patterns = Vec::with_capacity(names.len());
        for raw in names {
            let name = raw.trim();
            let pattern = if name == "*" {
                Pattern::Any
            } else if let Some(domain) = name.strip_prefix('.') {
                let domain = host_name(domain)
                    .filter(|d| !d.starts_with('['))
                    .ok_or_else(|| format!("invalid host pattern '{raw}'"))?;
                Pattern::Domain(domain)
            } else {
                if name.contains(':') && !name.starts_with('[') {
                    return Err(format!("'{raw}': give host names without a port"));
                }
                Pattern::Exact(host_name(name).ok_or_else(|| format!("invalid host '{raw}'"))?)
            };
            patterns.push(pattern);
        }
        Ok(Self { patterns })
    }

    pub fn is_empty(&self) -> bool {
        self.patterns.is_empty()
    }

    /// Whether a request with this `Host` header is for this site.
    pub fn allows(&self, host: Option<&str>) -> bool {
        if self.patterns.is_empty() || self.patterns.contains(&Pattern::Any) {
            return true;
        }
        let Some(name) = host.and_then(host_name) else {
            return false;
        };
        self.patterns.iter().any(|pattern| match pattern {
            Pattern::Any => true,
            Pattern::Exact(exact) => *exact == name,
            Pattern::Domain(domain) => {
                name == *domain
                    || name
                        .strip_suffix(domain.as_str())
                        .is_some_and(|prefix| prefix.ends_with('.'))
            }
        })
    }
}

#[cfg(test)]
mod tests {
    use super::{AllowedHosts, host_name};

    fn hosts(names: &[&str]) -> AllowedHosts {
        AllowedHosts::parse(&names.iter().map(|n| (*n).to_string()).collect::<Vec<_>>()).unwrap()
    }

    #[test]
    fn host_names_drop_ports_and_case() {
        assert_eq!(
            host_name("Example.COM:8000").as_deref(),
            Some("example.com")
        );
        assert_eq!(host_name("example.com.").as_deref(), Some("example.com"));
        assert_eq!(host_name("[::1]:8000").as_deref(), Some("[::1]"));
        assert_eq!(host_name("a.com:port"), None);
        assert_eq!(host_name("a b.com"), None);
        assert_eq!(host_name("evil.com/x"), None);
        assert_eq!(host_name(""), None);
    }

    #[test]
    fn patterns_match_names_and_subdomains() {
        let allowed = hosts(&["example.com", ".shop.test", "[::1]"]);
        assert!(allowed.allows(Some("example.com:443")));
        assert!(allowed.allows(Some("EXAMPLE.com")));
        assert!(!allowed.allows(Some("www.example.com")));
        assert!(allowed.allows(Some("shop.test")));
        assert!(allowed.allows(Some("a.b.shop.test")));
        assert!(!allowed.allows(Some("evilshop.test")));
        assert!(allowed.allows(Some("[::1]:8000")));
        assert!(!allowed.allows(None));
        assert!(!allowed.allows(Some("example.com.evil.com")));
        assert!(hosts(&[]).allows(Some("anything")));
        assert!(hosts(&["*"]).allows(None));
        assert!(AllowedHosts::parse(&["example.com:80".into()]).is_err());
        assert!(AllowedHosts::parse(&["bad host".into()]).is_err());
    }
}
