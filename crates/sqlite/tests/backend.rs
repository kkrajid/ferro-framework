use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, mpsc};
use std::time::Duration;

use ferro_database::{Backend, DbError, IntegrityKind, Job, Outcome, Statement, Value};
use ferro_sqlite::{SqliteBackend, SqliteConfig, SqlitePath};

fn run(backend: &SqliteBackend, job: Job) -> Result<Outcome, DbError> {
    let (tx, rx) = mpsc::channel();
    backend.submit(
        job,
        Arc::new(AtomicBool::new(false)),
        Box::new(move |result| {
            let _ = tx.send(result);
        }),
    );
    rx.recv_timeout(Duration::from_secs(10))
        .expect("job completes")
}

fn exec(backend: &SqliteBackend, sql: &str, params: Vec<Value>) -> Result<Outcome, DbError> {
    run(backend, Job::Execute(Statement::new(sql, params)))
}

fn query(backend: &SqliteBackend, sql: &str, params: Vec<Value>) -> Vec<Vec<Value>> {
    match run(backend, Job::Query(Statement::new(sql, params))).unwrap() {
        Outcome::Rows(rows) => rows.rows,
        other => panic!("{other:?}"),
    }
}

fn memory() -> SqliteBackend {
    let backend =
        SqliteBackend::open(SqliteConfig::from_url("sqlite:///:memory:").unwrap()).unwrap();
    exec(
        &backend,
        "CREATE TABLE t (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE, n REAL)",
        vec![],
    )
    .unwrap();
    backend
}

#[test]
fn url_parsing() {
    let memory = SqliteConfig::from_url("sqlite:///:memory:").unwrap();
    assert_eq!(memory.path, SqlitePath::Memory);
    assert_eq!(memory.pool_size, 1);
    let relative = SqliteConfig::from_url("sqlite:///data/app.db").unwrap();
    assert_eq!(relative.path, SqlitePath::File("data/app.db".into()));
    assert_eq!(relative.pool_size, 4);
    let absolute = SqliteConfig::from_url("sqlite:////var/app.db").unwrap();
    assert_eq!(absolute.path, SqlitePath::File("/var/app.db".into()));
    let windows = SqliteConfig::from_url("sqlite:///C:/data/app.db").unwrap();
    assert_eq!(windows.path, SqlitePath::File("C:/data/app.db".into()));
    for bad in ["postgres://x", "sqlite://", "sqlite:///", "app.db"] {
        let err = SqliteConfig::from_url(bad).unwrap_err().to_string();
        assert!(err.contains("sqlite:///"), "{bad}: {err}");
    }
}

#[test]
fn query_execute_and_list_tables() {
    let backend = memory();
    match exec(
        &backend,
        "INSERT INTO t (name, n) VALUES (?, ?)",
        vec![Value::Text("a".into()), Value::Real(1.5)],
    )
    .unwrap()
    {
        Outcome::Affected(1) => {}
        other => panic!("{other:?}"),
    }
    let rows = query(
        &backend,
        "INSERT INTO t (name, n) VALUES (?, ?) RETURNING id",
        vec![Value::Text("b".into()), Value::Null],
    );
    assert_eq!(rows, vec![vec![Value::Integer(2)]]);
    let rows = query(&backend, "SELECT id, name, n FROM t ORDER BY id", vec![]);
    assert_eq!(
        rows,
        vec![
            vec![Value::Integer(1), Value::Text("a".into()), Value::Real(1.5)],
            vec![Value::Integer(2), Value::Text("b".into()), Value::Null],
        ]
    );
    match run(&backend, Job::ListTables).unwrap() {
        Outcome::Tables(tables) => assert_eq!(tables, vec!["t".to_string()]),
        other => panic!("{other:?}"),
    }
}

#[test]
fn unique_and_not_null_violations_are_integrity_errors() {
    let backend = memory();
    exec(
        &backend,
        "INSERT INTO t (name) VALUES (?)",
        vec![Value::Text("a".into())],
    )
    .unwrap();
    match exec(
        &backend,
        "INSERT INTO t (name) VALUES (?)",
        vec![Value::Text("a".into())],
    ) {
        Err(DbError::Integrity {
            kind: IntegrityKind::Unique,
            table,
            columns,
            message,
        }) => {
            assert_eq!(table.as_deref(), Some("t"));
            assert_eq!(columns, vec!["name".to_string()]);
            assert!(message.contains("UNIQUE"), "{message}");
            assert!(!message.to_lowercase().contains("rusqlite"));
        }
        other => panic!("{other:?}"),
    }
    match exec(
        &backend,
        "INSERT INTO t (name) VALUES (?)",
        vec![Value::Null],
    ) {
        Err(DbError::Integrity {
            kind: IntegrityKind::NotNull,
            columns,
            ..
        }) => {
            assert_eq!(columns, vec!["name".to_string()]);
        }
        other => panic!("{other:?}"),
    }
}

#[test]
fn missing_table_is_operational_error() {
    let backend = memory();
    match exec(&backend, "SELECT * FROM nope", vec![]) {
        Err(DbError::MissingTable { message }) => {
            assert!(message.contains("no such table"), "{message}")
        }
        other => panic!("{other:?}"),
    }
}

#[test]
fn batch_returns_values_and_is_atomic() {
    let backend = memory();
    let insert = |name: &str| {
        Statement::new(
            "INSERT INTO t (name) VALUES (?) RETURNING id",
            vec![Value::Text(name.into())],
        )
    };
    match run(
        &backend,
        Job::Batch {
            statements: vec![insert("a"), insert("b"), insert("c")],
            returning: true,
        },
    )
    .unwrap()
    {
        Outcome::Batch { affected, returned } => {
            assert_eq!(affected, vec![1, 1, 1]);
            assert_eq!(
                returned,
                vec![
                    Some(Value::Integer(1)),
                    Some(Value::Integer(2)),
                    Some(Value::Integer(3))
                ]
            );
        }
        other => panic!("{other:?}"),
    }
    let failed = run(
        &backend,
        Job::Batch {
            statements: vec![insert("d"), insert("a")],
            returning: true,
        },
    );
    assert!(
        matches!(failed, Err(DbError::Integrity { .. })),
        "{failed:?}"
    );
    let rows = query(&backend, "SELECT COUNT(*) FROM t", vec![]);
    assert_eq!(
        rows,
        vec![vec![Value::Integer(3)]],
        "failed batch rolled back"
    );

    let plain = Statement::new("UPDATE t SET n = 1", vec![]);
    match run(
        &backend,
        Job::Batch {
            statements: vec![plain],
            returning: false,
        },
    )
    .unwrap()
    {
        Outcome::Batch { affected, returned } => {
            assert_eq!(affected, vec![3]);
            assert_eq!(returned, vec![None]);
        }
        other => panic!("{other:?}"),
    }
}

#[test]
fn decimals_bind_as_numbers() {
    let backend = memory();
    exec(
        &backend,
        "INSERT INTO t (name, n) VALUES ('a', ?)",
        vec![Value::Decimal("12.50".into())],
    )
    .unwrap();
    assert_eq!(
        query(
            &backend,
            "SELECT n FROM t WHERE n > ?",
            vec![Value::Decimal("9".into())]
        ),
        vec![vec![Value::Real(12.5)]]
    );
}

#[test]
fn cancelled_jobs_are_skipped() {
    let backend = memory();
    let (tx, rx) = mpsc::channel();
    backend.submit(
        Job::Execute(Statement::new("INSERT INTO t (name) VALUES ('x')", vec![])),
        Arc::new(AtomicBool::new(true)),
        Box::new(move |result| {
            let _ = tx.send(result);
        }),
    );
    let rows = query(&backend, "SELECT COUNT(*) FROM t", vec![]);
    assert_eq!(rows, vec![vec![Value::Integer(0)]]);
    assert!(
        rx.try_recv().is_err(),
        "completion not called for a skipped job"
    );
}

#[test]
fn file_database_uses_wal_foreign_keys_and_pool() {
    let dir = tempfile::tempdir().unwrap();
    let url = format!("sqlite:///{}", dir.path().join("app.db").display()).replace('\\', "/");
    let backend = SqliteBackend::open(SqliteConfig::from_url(&url).unwrap()).unwrap();
    assert_eq!(
        query(&backend, "PRAGMA journal_mode", vec![]),
        vec![vec![Value::Text("wal".into())]]
    );
    assert_eq!(
        query(&backend, "PRAGMA foreign_keys", vec![]),
        vec![vec![Value::Integer(1)]]
    );
    assert_eq!(
        query(&backend, "PRAGMA busy_timeout", vec![]),
        vec![vec![Value::Integer(5000)]]
    );
    exec(
        &backend,
        "CREATE TABLE c (id INTEGER PRIMARY KEY, v INTEGER)",
        vec![],
    )
    .unwrap();

    let (tx, rx) = mpsc::channel();
    for i in 0..200 {
        let tx = tx.clone();
        backend.submit(
            Job::Execute(Statement::new(
                "INSERT INTO c (v) VALUES (?)",
                vec![Value::Integer(i)],
            )),
            Arc::new(AtomicBool::new(false)),
            Box::new(move |result| {
                let _ = tx.send(result.is_ok());
            }),
        );
    }
    drop(tx);
    let results: Vec<bool> = rx.iter().collect();
    assert_eq!(results.len(), 200);
    assert!(results.iter().all(|ok| *ok));
    assert_eq!(
        query(&backend, "SELECT COUNT(*) FROM c", vec![]),
        vec![vec![Value::Integer(200)]]
    );
    backend.close();
}

#[test]
fn unwritable_path_fails_at_open() {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("missing-dir").join("x.db");
    let url = format!("sqlite:///{}", path.display()).replace('\\', "/");
    let err = SqliteBackend::open(SqliteConfig::from_url(&url).unwrap()).unwrap_err();
    assert!(matches!(err, DbError::Operational { .. }), "{err:?}");
    assert!(
        err.to_string().contains("unable to open database file"),
        "{err}"
    );
}

#[test]
fn closed_backend_rejects_jobs() {
    let backend = memory();
    backend.close();
    let result = exec(&backend, "SELECT 1", vec![]);
    assert!(matches!(result, Err(DbError::Closed)), "{result:?}");
}

#[test]
fn values_round_trip_exactly() {
    let backend = memory();
    let rows = query(
        &backend,
        "SELECT ?, ?, ?, ?, ?",
        vec![
            Value::Integer(i64::MIN),
            Value::Integer(i64::MAX),
            Value::Real(-0.1),
            Value::Text("മലയാളം 'quoted' \"double\"".into()),
            Value::Blob(vec![0, 1, 255]),
        ],
    );
    assert_eq!(
        rows[0],
        vec![
            Value::Integer(i64::MIN),
            Value::Integer(i64::MAX),
            Value::Real(-0.1),
            Value::Text("മലയാളം 'quoted' \"double\"".into()),
            Value::Blob(vec![0, 1, 255]),
        ]
    );
    let _ = Ordering::SeqCst;
}

// Feature 011: unavailability and the queue bound.

fn file_backend(dir: &tempfile::TempDir, suffix: &str, busy: Duration) -> (SqliteBackend, String) {
    let path = dir
        .path()
        .join("app.db")
        .display()
        .to_string()
        .replace('\\', "/");
    let mut config = SqliteConfig::from_url(&format!("sqlite:///{path}{suffix}"))
        .unwrap()
        .with_pool_size(1);
    config.busy_timeout = busy;
    let backend = SqliteBackend::open(config).unwrap();
    exec(&backend, "CREATE TABLE w (n INTEGER)", vec![]).unwrap();
    (backend, path)
}

fn submit(backend: &SqliteBackend, sql: &str) -> mpsc::Receiver<Result<Outcome, DbError>> {
    let (tx, rx) = mpsc::channel();
    backend.submit(
        Job::Execute(Statement::new(sql, vec![])),
        Arc::new(AtomicBool::new(false)),
        Box::new(move |result| {
            let _ = tx.send(result);
        }),
    );
    rx
}

#[test]
fn a_lock_held_past_the_busy_timeout_is_unavailable() {
    let dir = tempfile::tempdir().unwrap();
    let (backend, path) = file_backend(&dir, "", Duration::from_millis(200));
    let other = rusqlite::Connection::open(&path).unwrap();
    other
        .execute_batch("BEGIN IMMEDIATE; INSERT INTO w VALUES (1);")
        .unwrap();
    let err = exec(&backend, "INSERT INTO w VALUES (2)", vec![]).unwrap_err();
    assert!(matches!(err, DbError::Unavailable { .. }), "{err:?}");
    other.execute_batch("ROLLBACK").unwrap();
    exec(&backend, "INSERT INTO w VALUES (3)", vec![]).expect("works again once released");
}

#[test]
fn a_full_queue_rejects_new_jobs_at_once() {
    let dir = tempfile::tempdir().unwrap();
    let (backend, path) = file_backend(&dir, "?queue_size=2", Duration::from_secs(3));
    let other = rusqlite::Connection::open(&path).unwrap();
    other
        .execute_batch("BEGIN IMMEDIATE; INSERT INTO w VALUES (1);")
        .unwrap();
    let busy = submit(&backend, "INSERT INTO w VALUES (2)");
    std::thread::sleep(Duration::from_millis(300)); // the only connection is now waiting
    let queued = [
        submit(&backend, "INSERT INTO w VALUES (3)"),
        submit(&backend, "INSERT INTO w VALUES (4)"),
    ];
    let started = std::time::Instant::now();
    let err = exec(&backend, "INSERT INTO w VALUES (5)", vec![]).unwrap_err();
    assert!(matches!(err, DbError::Unavailable { .. }), "{err:?}");
    assert!(
        started.elapsed() < Duration::from_millis(500),
        "rejected without waiting"
    );
    other.execute_batch("ROLLBACK").unwrap();
    busy.recv_timeout(Duration::from_secs(10)).unwrap().unwrap();
    for rx in queued {
        rx.recv_timeout(Duration::from_secs(10)).unwrap().unwrap();
    }
}

#[test]
fn url_parameters() {
    let config = SqliteConfig::from_url("sqlite:///data/app.db?queue_size=64").unwrap();
    assert_eq!(config.path, SqlitePath::File("data/app.db".into()));
    assert_eq!(config.queue_size, 64);
    assert_eq!(
        SqliteConfig::from_url("sqlite:///data/app.db")
            .unwrap()
            .queue_size,
        1024
    );
    for (bad, mentions) in [
        ("sqlite:///a.db?queue_size=0", "queue_size"),
        ("sqlite:///a.db?queue_size=lots", "queue_size"),
        ("sqlite:///a.db?pool=3", "pool"),
    ] {
        let err = SqliteConfig::from_url(bad).unwrap_err().to_string();
        assert!(err.contains(mentions), "{bad}: {err}");
    }
}
