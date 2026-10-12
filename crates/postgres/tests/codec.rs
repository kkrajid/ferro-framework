use ferro_postgres::{PgConfig, TlsMode, numeric, pgtime};

#[test]
fn numeric_round_trips() {
    for text in [
        "0",
        "0.00",
        "1",
        "-1",
        "10000",
        "12345678.1234",
        "-99999999.9999",
        "0.0001",
        "1.5000",
        "100000000",
        "1234567890123456789012345678.0123456789",
        "-0.5",
        "9999",
        "10000.0001",
    ] {
        let encoded = numeric::encode(text).unwrap();
        assert_eq!(numeric::decode(&encoded).unwrap(), text, "{text}");
    }
}

#[test]
fn numeric_known_encoding() {
    // 12345.678 -> digits [1, 2345, 6780], weight 1, positive, dscale 3
    let encoded = numeric::encode("12345.678").unwrap();
    let words: Vec<i16> = encoded
        .chunks(2)
        .map(|c| i16::from_be_bytes([c[0], c[1]]))
        .collect();
    assert_eq!(words, vec![3, 1, 0, 3, 1, 2345, 6780]);
    assert!(numeric::encode("1e5").is_err());
    assert!(numeric::encode("abc").is_err());
    assert!(numeric::encode("").is_err());
}

#[test]
fn dates_and_times() {
    assert_eq!(pgtime::date_to_days("2000-01-01").unwrap(), 0);
    assert_eq!(pgtime::date_to_days("1999-12-31").unwrap(), -1);
    for text in [
        "0001-01-01",
        "9999-12-31",
        "2026-10-10",
        "2024-02-29",
        "1970-01-01",
    ] {
        assert_eq!(
            pgtime::days_to_date(pgtime::date_to_days(text).unwrap()),
            text
        );
    }
    assert!(pgtime::date_to_days("2026-13-01").is_err());

    let stamp = "2026-10-10 04:00:00.123456";
    let micros = pgtime::timestamp_to_micros(stamp).unwrap();
    assert_eq!(pgtime::micros_to_timestamp(micros), stamp);
    assert_eq!(
        pgtime::timestamp_to_micros("2000-01-01 00:00:00.000000").unwrap(),
        0
    );
    assert_eq!(
        pgtime::micros_to_timestamp(
            pgtime::timestamp_to_micros("0001-01-01 00:00:00.000000").unwrap()
        ),
        "0001-01-01 00:00:00.000000"
    );

    for text in ["00:00:00.000000", "23:59:59.999999", "08:15:00.000000"] {
        assert_eq!(
            pgtime::micros_to_time(pgtime::time_to_micros(text).unwrap()),
            text
        );
    }
    assert!(pgtime::time_to_micros("25:00:00").is_err());
}

#[test]
fn url_parsing() {
    let config = PgConfig::from_url(
        "postgresql://shop:s3cr%40t@db.example.com:6543/shop?sslmode=require&pool_size=3&connect_timeout=5&options=-csearch_path%3Dtenant1",
    )
    .unwrap();
    assert_eq!(config.pool_size, 3);
    assert_eq!(config.tls, TlsMode::Require);
    assert_eq!(config.describe(), "db.example.com:6543/shop as shop");
    assert!(
        !format!("{config:?}").contains("s3cr"),
        "Debug must not show the password"
    );

    let defaults = PgConfig::from_url("postgres://localhost/app").unwrap();
    assert_eq!(defaults.pool_size, 10);
    assert_eq!(defaults.tls, TlsMode::Prefer);
    assert_eq!(defaults.describe(), "localhost:5432/app");

    assert_eq!(
        PgConfig::from_url("postgresql://h/d?sslmode=disable")
            .unwrap()
            .tls,
        TlsMode::Disable
    );
    assert_eq!(
        PgConfig::from_url("postgresql://h/d?sslmode=verify-full")
            .unwrap()
            .tls,
        TlsMode::Require
    );

    for (url, expected) in [
        (
            "postgresql://u:topsecret@h/d?colour=blue",
            "unsupported PostgreSQL URL parameter 'colour'",
        ),
        ("postgresql://u:topsecret@h/d?sslmode=sometimes", "sslmode"),
        ("postgresql://u:topsecret@h/d?pool_size=0", "pool_size"),
        ("postgresql://u:topsecret@h/d?pool_size=x", "pool_size"),
        ("mysql://u:topsecret@h/d", "postgresql://"),
    ] {
        let message = PgConfig::from_url(url).unwrap_err().to_string();
        assert!(message.contains(expected), "{url}: {message}");
        assert!(!message.contains("topsecret"), "{url}: {message}");
    }
}
