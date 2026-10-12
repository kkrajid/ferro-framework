use std::net::IpAddr;
use std::time::{Duration, Instant};

use ferro_core::security::cookies::parse_cookies;
use ferro_core::security::cors::{CorsConfig, Preflight};
use ferro_core::security::origin::{OriginCheck, normalize_origin};
use ferro_core::security::proxy::TrustedProxies;
use ferro_core::security::ratelimit::{Limiter, RateSpec};

fn ip(text: &str) -> IpAddr {
    text.parse().unwrap()
}

fn check(
    method: &str,
    sec_fetch_site: Option<&str>,
    origin: Option<&str>,
    trusted: &[&str],
) -> bool {
    let policy = OriginCheck::new(trusted.iter().map(|s| s.to_string()).collect()).unwrap();
    policy.allows(
        method,
        "http",
        Some("shop.example:8000"),
        sec_fetch_site,
        origin,
        &|_| false,
    )
}

#[test]
fn safe_methods_are_never_blocked() {
    for method in ["GET", "HEAD", "OPTIONS"] {
        assert!(check(
            method,
            Some("cross-site"),
            Some("https://evil.example"),
            &[]
        ));
    }
}

#[test]
fn unsafe_cross_origin_requests_are_refused() {
    assert!(!check(
        "POST",
        Some("cross-site"),
        Some("https://evil.example"),
        &[]
    ));
    assert!(!check("DELETE", None, Some("https://evil.example"), &[]));
    assert!(!check(
        "PUT",
        Some("same-site"),
        Some("http://api.shop.example:8000"),
        &[]
    ));
    assert!(!check("POST", None, Some("null"), &[]));
}

#[test]
fn same_origin_headerless_and_trusted_requests_pass() {
    assert!(check(
        "POST",
        Some("same-origin"),
        Some("http://shop.example:8000"),
        &[]
    ));
    assert!(check("POST", Some("none"), None, &[]));
    assert!(check("POST", None, None, &[]), "non-browser clients");
    assert!(check("POST", None, Some("http://SHOP.example:8000"), &[]));
    assert!(check(
        "PATCH",
        Some("cross-site"),
        Some("https://admin.example"),
        &["https://admin.example"]
    ));
    let policy = OriginCheck::new(vec![]).unwrap();
    assert!(policy.allows(
        "POST",
        "http",
        Some("shop.example"),
        Some("cross-site"),
        Some("https://app.example"),
        &|origin| origin == "https://app.example",
    ));
}

#[test]
fn origins_are_normalized() {
    assert_eq!(
        normalize_origin("HTTPS://Example.COM:443").as_deref(),
        Some("https://example.com")
    );
    assert_eq!(
        normalize_origin("http://example.com:80/").as_deref(),
        Some("http://example.com")
    );
    assert_eq!(
        normalize_origin("http://localhost:5173").as_deref(),
        Some("http://localhost:5173")
    );
    assert_eq!(normalize_origin("example.com"), None);
    assert!(OriginCheck::new(vec!["not an origin".into()]).is_err());
}

fn cors(origins: &[&str], credentials: bool) -> CorsConfig {
    CorsConfig::new(
        origins.iter().map(|s| s.to_string()).collect(),
        vec!["GET".into(), "POST".into()],
        vec!["content-type".into(), "x-token".into()],
        vec!["x-total".into()],
        credentials,
        600,
    )
    .unwrap()
}

#[test]
fn cors_preflight_decisions() {
    let policy = cors(&["https://app.example"], true);
    match policy.preflight("https://app.example", "POST", Some("Content-Type, X-Token")) {
        Preflight::Allowed(headers) => {
            let get = |name: &str| {
                headers
                    .iter()
                    .find(|(n, _)| n == name)
                    .map(|(_, v)| v.as_str())
            };
            assert_eq!(
                get("access-control-allow-origin"),
                Some("https://app.example")
            );
            assert_eq!(get("access-control-allow-methods"), Some("GET, POST"));
            assert_eq!(
                get("access-control-allow-headers"),
                Some("content-type, x-token")
            );
            assert_eq!(get("access-control-allow-credentials"), Some("true"));
            assert_eq!(get("access-control-max-age"), Some("600"));
            assert_eq!(get("vary"), Some("origin"));
        }
        Preflight::Denied => panic!("allowed origin"),
    }
    assert!(matches!(
        policy.preflight("https://evil.example", "POST", None),
        Preflight::Denied
    ));
    assert!(matches!(
        policy.preflight("https://app.example", "DELETE", None),
        Preflight::Denied
    ));
    assert!(matches!(
        policy.preflight("https://app.example", "POST", Some("x-other")),
        Preflight::Denied
    ));
}

#[test]
fn cors_actual_response_headers() {
    let policy = cors(&["https://app.example"], false);
    let headers = policy.response_headers("https://app.example").unwrap();
    assert!(headers.contains(&(
        "access-control-allow-origin".into(),
        "https://app.example".into()
    )));
    assert!(headers.contains(&("access-control-expose-headers".into(), "x-total".into())));
    assert!(policy.response_headers("https://evil.example").is_none());
    let any = cors(&["*"], false);
    assert!(
        any.response_headers("https://whoever.example")
            .unwrap()
            .contains(&("access-control-allow-origin".into(), "*".into()))
    );
    assert!(any.allows("https://whoever.example"));
    let err = CorsConfig::new(vec!["*".into()], vec![], vec![], vec![], true, 600)
        .unwrap_err()
        .to_string();
    assert!(err.contains("allow_credentials"), "{err}");
}

#[test]
fn rate_specs_parse() {
    let spec = RateSpec::parse("100/minute").unwrap();
    assert_eq!(spec.count, 100);
    assert_eq!(spec.period, Duration::from_secs(60));
    for (text, secs) in [("5/second", 1), ("2/hour", 3600), ("1/day", 86_400)] {
        assert_eq!(RateSpec::parse(text).unwrap().period.as_secs(), secs);
    }
    for bad in ["0/minute", "10/fortnight", "ten/minute", "10", ""] {
        assert!(RateSpec::parse(bad).is_err(), "{bad}");
    }
}

#[test]
fn token_buckets_limit_refill_and_isolate() {
    let limiter = Limiter::default();
    let spec = RateSpec::parse("3/minute").unwrap();
    let start = Instant::now();
    let a = ip("10.0.0.1");
    let b = ip("10.0.0.2");
    for _ in 0..3 {
        assert!(limiter.check(0, a, spec, start).is_ok());
    }
    let retry = limiter.check(0, a, spec, start).unwrap_err();
    assert_eq!(retry.as_secs(), 20, "one token every 20 s");
    assert!(limiter.check(0, b, spec, start).is_ok(), "other client");
    assert!(limiter.check(1, a, spec, start).is_ok(), "other scope");
    let later = start + Duration::from_secs(20);
    assert!(limiter.check(0, a, spec, later).is_ok(), "refilled");
    assert!(limiter.check(0, a, spec, later).is_err());
}

#[test]
fn idle_buckets_are_evicted() {
    let limiter = Limiter::default();
    let spec = RateSpec::parse("10/second").unwrap();
    let start = Instant::now();
    for i in 0..5000u32 {
        let address = IpAddr::from([10, (i >> 16) as u8, (i >> 8) as u8, i as u8]);
        limiter.check(0, address, spec, start).unwrap();
    }
    assert!(limiter.len() >= 5000);
    let later = start + Duration::from_secs(5);
    for i in 0..1000u32 {
        let address = IpAddr::from([11, 0, (i >> 8) as u8, i as u8]);
        limiter.check(0, address, spec, later).unwrap();
    }
    assert!(
        limiter.len() < 3000,
        "idle clients evicted, {} left",
        limiter.len()
    );
}

#[test]
fn client_addresses_behind_trusted_proxies() {
    let proxies = TrustedProxies::parse(&["10.0.0.0/8".into(), "192.168.1.5".into()]).unwrap();
    assert_eq!(
        proxies.client(ip("10.1.2.3"), Some("203.0.113.7, 10.9.9.9")),
        ip("203.0.113.7")
    );
    assert_eq!(
        proxies.client(ip("192.168.1.5"), Some("198.51.100.1")),
        ip("198.51.100.1")
    );
    assert_eq!(
        proxies.client(ip("203.0.113.9"), Some("1.2.3.4")),
        ip("203.0.113.9"),
        "untrusted peers cannot choose their address"
    );
    assert_eq!(
        proxies.client(ip("10.0.0.1"), Some("garbage")),
        ip("10.0.0.1")
    );
    assert_eq!(proxies.client(ip("10.0.0.1"), None), ip("10.0.0.1"));
    assert!(proxies.is_trusted(ip("10.200.0.1")));
    assert!(TrustedProxies::parse(&["10.0.0.0/33".into()]).is_err());
    assert!(TrustedProxies::parse(&["nonsense".into()]).is_err());
    let v6 = TrustedProxies::parse(&["fd00::/8".into()]).unwrap();
    assert!(v6.is_trusted(ip("fd12::1")));
}

#[test]
fn cookie_headers_parse() {
    assert_eq!(
        parse_cookies("sid=abc; theme=dark;flag; empty=; sid=last"),
        vec![
            ("sid".to_string(), "abc".to_string()),
            ("theme".to_string(), "dark".to_string()),
            ("empty".to_string(), String::new()),
            ("sid".to_string(), "last".to_string()),
        ]
    );
    assert_eq!(
        parse_cookies(r#"quoted="a b""#),
        vec![("quoted".to_string(), "a b".to_string())]
    );
}

#[test]
fn memory_is_returned_after_a_burst_of_clients() {
    let limiter = Limiter::default();
    let spec = RateSpec::parse("100/minute").unwrap();
    let start = Instant::now();
    for i in 0..20_000u32 {
        let address = IpAddr::from([10, (i >> 16) as u8, (i >> 8) as u8, i as u8]);
        limiter.check(0, address, spec, start).unwrap();
    }
    assert!(limiter.len() > 15_000);
    let later = start + Duration::from_secs(3600);
    for i in 0..200u32 {
        limiter
            .check(0, IpAddr::from([11, 0, 0, i as u8]), spec, later)
            .unwrap();
    }
    assert!(limiter.len() <= 200, "{} buckets left", limiter.len());
}

// Security review fixes (Feature 017).

#[test]
fn ipv6_clients_share_a_budget_per_64() {
    use ferro_core::security::ratelimit::client_key;
    assert_eq!(client_key(ip("2001:db8:1:2:aaaa::1")), ip("2001:db8:1:2::"));
    assert_eq!(
        client_key(ip("2001:db8:1:2:ffff:ffff:ffff:ffff")),
        ip("2001:db8:1:2::")
    );
    assert_eq!(client_key(ip("::ffff:192.0.2.7")), ip("192.0.2.7"));
    assert_eq!(client_key(ip("192.0.2.7")), ip("192.0.2.7"));

    let limiter = Limiter::default();
    let spec = RateSpec::parse("2/minute").unwrap();
    let now = Instant::now();
    assert!(limiter.check(0, ip("2001:db8::1"), spec, now).is_ok());
    assert!(limiter.check(0, ip("2001:db8::2"), spec, now).is_ok());
    assert!(
        limiter.check(0, ip("2001:db8::3"), spec, now).is_err(),
        "rotating addresses inside one /64 does not reset the budget"
    );
    assert!(limiter.check(0, ip("2001:db8:0:1::1"), spec, now).is_ok());
}

#[test]
fn bucket_memory_is_capped() {
    let limiter = Limiter::default();
    let spec = RateSpec::parse("1/day").unwrap();
    let now = Instant::now();
    for n in 0..80_000u32 {
        let _ = limiter.check(0, IpAddr::from(n.to_be_bytes()), spec, now);
    }
    assert!(limiter.len() <= 16 * 4096, "{} buckets", limiter.len());
}

#[test]
fn wildcard_cors_never_trusts_requests_with_cookies() {
    use ferro_core::security::{Policy, RouteOptions};
    use ferro_core::{CorsSettings, Settings};
    let settings = Settings {
        cors: Some(CorsSettings {
            allow_origins: vec!["*".into()],
            allow_methods: vec!["GET".into(), "POST".into()],
            allow_headers: Vec::new(),
            expose_headers: Vec::new(),
            allow_credentials: false,
            max_age: 600,
        }),
        ..Settings::default()
    };
    let policy = Policy::new(&settings, vec![RouteOptions::default()]).unwrap();
    let post = |cookies: bool| {
        policy.csrf_allows(
            0,
            "POST",
            "https",
            Some("shop.example"),
            Some("cross-site"),
            Some("https://evil.example"),
            cookies,
        )
    };
    assert!(
        post(false),
        "a public API without cookies stays open to every origin"
    );
    assert!(
        !post(true),
        "a signed-in browser's form post from another site is refused"
    );

    let listed = Settings {
        cors: Some(CorsSettings {
            allow_origins: vec!["https://app.example".into()],
            ..settings.cors.clone().unwrap()
        }),
        ..Settings::default()
    };
    let policy = Policy::new(&listed, vec![RouteOptions::default()]).unwrap();
    assert!(policy.csrf_allows(
        0,
        "POST",
        "https",
        Some("shop.example"),
        Some("cross-site"),
        Some("https://app.example"),
        true,
    ));
}
