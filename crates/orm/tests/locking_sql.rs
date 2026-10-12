mod common;

use common::{int, leaf, model, shop};
use ferro_orm::compile::{self, PostgresDialect, SqliteDialect};
use ferro_orm::{AggFunc, Aggregate, Lock, Query, Shape};

const PG: &PostgresDialect = &PostgresDialect;
const LITE: &SqliteDialect = &SqliteDialect;

#[test]
fn postgres_locks_the_model_rows_after_the_window() {
    let registry = shop();
    let product = model(&registry, "Product");
    let q = Query::new(product.clone())
        .with_node(Some(leaf(&registry, &product, "stock__gt", int(0))))
        .window(None, Some(5))
        .for_update(Lock::Wait)
        .unwrap();
    let statement = compile::select(PG, &q);
    assert!(
        statement
            .sql
            .ends_with(r#"WHERE ("stock" > $1) LIMIT $2 FOR UPDATE OF "product""#),
        "{}",
        statement.sql
    );
    assert_eq!(statement.params.len(), 2);
    assert!(q.is_locking());
}

#[test]
fn nowait_and_skip_locked() {
    let registry = shop();
    let product = model(&registry, "Product");
    let nowait = Query::new(product.clone())
        .for_update(Lock::NoWait)
        .unwrap();
    assert!(
        compile::select(PG, &nowait)
            .sql
            .ends_with(r#" FOR UPDATE OF "product" NOWAIT"#)
    );
    let skip = Query::new(product.clone())
        .for_update(Lock::SkipLocked)
        .unwrap();
    assert!(
        compile::select(PG, &skip)
            .sql
            .ends_with(r#" FOR UPDATE OF "product" SKIP LOCKED"#)
    );
}

#[test]
fn joined_rows_are_not_locked() {
    let registry = shop();
    let product = model(&registry, "Product");
    let q = Query::new(product.clone())
        .select_related(&registry, &["category"])
        .unwrap()
        .for_update(Lock::Wait)
        .unwrap();
    let sql = compile::select(PG, &q).sql;
    assert!(sql.contains(r#"LEFT JOIN "category" AS "T1""#), "{sql}");
    assert!(sql.ends_with(r#" FOR UPDATE OF "product""#), "{sql}");
}

#[test]
fn sqlite_adds_no_clause() {
    let registry = shop();
    let product = model(&registry, "Product");
    let plain = Query::new(product.clone());
    let locked = plain.for_update(Lock::Wait).unwrap();
    assert_eq!(
        compile::select(LITE, &locked).sql,
        compile::select(LITE, &plain).sql
    );
}

#[test]
fn values_keep_the_lock() {
    let registry = shop();
    let product = model(&registry, "Product");
    let q = Query::new(product.clone())
        .for_update(Lock::Wait)
        .unwrap()
        .values(&registry, &["id"], Shape::Flat)
        .unwrap();
    assert_eq!(
        compile::select(PG, &q).sql,
        r#"SELECT "id" FROM "product" FOR UPDATE OF "product""#
    );
}

#[test]
fn annotations_cannot_be_locked() {
    let registry = shop();
    let product = model(&registry, "Product");
    let count =
        Aggregate::resolve(&registry, &product, AggFunc::Count, Some("tags"), false).unwrap();
    let annotated = Query::new(product.clone())
        .annotate(vec![("n".into(), count.clone())])
        .unwrap();
    let err = annotated.for_update(Lock::Wait).unwrap_err().to_string();
    assert!(err.contains("select_for_update()"), "{err}");
    let err = Query::new(product.clone())
        .for_update(Lock::Wait)
        .unwrap()
        .annotate(vec![("n".into(), count)])
        .unwrap_err()
        .to_string();
    assert!(err.contains("select_for_update()"), "{err}");
}

#[test]
fn counting_queries_never_lock() {
    let registry = shop();
    let product = model(&registry, "Product");
    let q = Query::new(product.clone())
        .window(None, Some(3))
        .for_update(Lock::Wait)
        .unwrap();
    assert!(!compile::count(PG, &q).sql.contains("FOR UPDATE"));
    assert!(!compile::exists(PG, &q).sql.contains("FOR UPDATE"));
}
