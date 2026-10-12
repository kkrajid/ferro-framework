//! Feature 011: the `queue_size` URL parameter.

use ferro_postgres::PgConfig;

#[test]
fn queue_size_parameter() {
    let config = PgConfig::from_url("postgresql://u@localhost/db?queue_size=200").unwrap();
    assert_eq!(config.queue_size, 200);
    let default = PgConfig::from_url("postgresql://u@localhost/db").unwrap();
    assert_eq!(default.queue_size, 1024);
    for bad in [
        "postgresql://u@localhost/db?queue_size=0",
        "postgresql://u@localhost/db?queue_size=x",
    ] {
        let err = PgConfig::from_url(bad).unwrap_err().to_string();
        assert!(err.contains("queue_size"), "{bad}: {err}");
    }
}
