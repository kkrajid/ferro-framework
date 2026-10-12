use std::sync::Arc;

use ferro_database::{Statement, Value};
use ferro_orm::compile::{self, PostgresDialect};
use ferro_orm::ddl;
use ferro_orm::{FieldKind, FieldSpec, ModelMeta, Operand, Query};

const D: &PostgresDialect = &PostgresDialect;

fn product() -> Arc<ModelMeta> {
    let mut name = FieldSpec::new("name", FieldKind::String { max_length: 200 });
    name.null = true;
    name.index = true;
    let mut sku = FieldSpec::new("sku", FieldKind::String { max_length: 40 });
    sku.unique = true;
    Arc::new(
        ModelMeta::new(
            "shop.Product",
            "Product",
            "product",
            vec![
                name,
                sku,
                FieldSpec::new(
                    "price",
                    FieldKind::Decimal {
                        max_digits: 10,
                        decimal_places: 2,
                    },
                ),
                FieldSpec::new("stock", FieldKind::Integer),
                FieldSpec::new("active", FieldKind::Boolean),
            ],
        )
        .unwrap(),
    )
}

const COLUMNS: &str = r#""id", "name", "sku", "price", "stock", "active""#;

fn text(s: &str) -> Operand {
    Operand::Value(Value::Text(s.into()))
}

#[test]
fn numbered_placeholders() {
    let q = Query::new(product())
        .filter(
            false,
            vec![
                ("price__gte", Operand::Value(Value::Decimal("1000".into()))),
                ("active", Operand::Value(Value::Integer(1))),
            ],
        )
        .unwrap()
        .filter(
            false,
            vec![(
                "id__in",
                Operand::List(vec![Value::Integer(1), Value::Integer(2)]),
            )],
        )
        .unwrap()
        .order_by(&["-price"])
        .unwrap()
        .window(Some(10), Some(5));
    assert_eq!(
        compile::select(D, &q),
        Statement::new(
            format!(
                r#"SELECT {COLUMNS} FROM "product" WHERE ("price" >= $1 AND "active" = $2) AND ("id" IN ($3, $4)) ORDER BY "price" DESC LIMIT $5 OFFSET $6"#
            ),
            vec![
                Value::Decimal("1000".into()),
                Value::Integer(1),
                Value::Integer(1),
                Value::Integer(2),
                Value::Integer(5),
                Value::Integer(10),
            ]
        )
    );
}

#[test]
fn text_lookups_use_like_and_ilike() {
    let cases = [
        (
            "name__contains",
            r"a%b_c\",
            r#""name" LIKE $1 ESCAPE '\'"#,
            r"%a\%b\_c\\%",
        ),
        (
            "name__startswith",
            "ab",
            r#""name" LIKE $1 ESCAPE '\'"#,
            "ab%",
        ),
        (
            "name__endswith",
            "ab",
            r#""name" LIKE $1 ESCAPE '\'"#,
            "%ab",
        ),
        (
            "name__icontains",
            "École",
            r#""name" ILIKE $1 ESCAPE '\'"#,
            "%École%",
        ),
        (
            "name__iexact",
            "A_b",
            r#""name" ILIKE $1 ESCAPE '\'"#,
            r"A\_b",
        ),
    ];
    for (key, value, fragment, param) in cases {
        let q = Query::new(product())
            .filter(false, vec![(key, text(value))])
            .unwrap();
        let statement = compile::select(D, &q);
        assert!(
            statement.sql.ends_with(&format!("WHERE ({fragment})")),
            "{key}: {}",
            statement.sql
        );
        assert_eq!(statement.params, vec![Value::Text(param.into())], "{key}");
    }
}

#[test]
fn offset_only_and_aliased_subqueries() {
    let q = Query::new(product()).window(Some(5), None);
    assert!(compile::select(D, &q).sql.ends_with(" OFFSET $1"));
    let filtered = Query::new(product())
        .filter(false, vec![("stock", Operand::Value(Value::Integer(0)))])
        .unwrap();
    assert_eq!(
        compile::count(D, &filtered.window(Some(2), Some(3))).sql,
        r#"SELECT COUNT(*) FROM (SELECT "id" FROM "product" WHERE ("stock" = $1) LIMIT $2 OFFSET $3) AS "subquery""#
    );
    assert_eq!(
        compile::exists(D, &filtered).sql,
        r#"SELECT 1 FROM "product" WHERE ("stock" = $1) LIMIT 1"#
    );
}

#[test]
fn writes_number_parameters_in_order() {
    let meta = product();
    assert_eq!(
        compile::insert(
            D,
            &meta,
            &[(1, Value::Text("x".into())), (4, Value::Integer(3))],
            true
        )
        .sql,
        r#"INSERT INTO "product" ("name", "stock") VALUES ($1, $2) RETURNING "id""#
    );
    let q = Query::new(meta.clone())
        .filter(false, vec![("stock", Operand::Value(Value::Integer(0)))])
        .unwrap();
    assert_eq!(
        compile::update(D, &q, &[(5, compile::Assignment::Value(Value::Integer(0)))])
            .unwrap()
            .sql,
        r#"UPDATE "product" SET "active" = $1 WHERE ("stock" = $2)"#
    );
    assert_eq!(
        compile::update_row(D, &meta, &[(1, Value::Null)], Value::Integer(1)).sql,
        r#"UPDATE "product" SET "name" = $1 WHERE "id" = $2"#
    );
    assert_eq!(
        compile::delete_row(D, &meta, Value::Integer(1)).sql,
        r#"DELETE FROM "product" WHERE "id" = $1"#
    );
}

#[test]
fn ddl_uses_native_types() {
    let statements = ddl::create_table(D, &product());
    assert_eq!(
        statements[0].sql,
        "CREATE TABLE \"product\" (\n  \
         \"id\" bigint GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,\n  \
         \"name\" varchar(200),\n  \
         \"sku\" varchar(40) NOT NULL UNIQUE,\n  \
         \"price\" numeric(10, 2) NOT NULL,\n  \
         \"stock\" integer NOT NULL,\n  \
         \"active\" boolean NOT NULL\n)"
    );
    assert_eq!(
        statements[1].sql,
        r#"CREATE INDEX "product_name_idx" ON "product" ("name")"#
    );
    let kinds = [
        (FieldKind::Text, "text"),
        (FieldKind::BigInteger, "bigint"),
        (FieldKind::Float, "double precision"),
        (FieldKind::Date, "date"),
        (FieldKind::DateTime, "timestamp with time zone"),
        (FieldKind::Time, "time"),
        (FieldKind::Uuid, "uuid"),
        (FieldKind::Json, "jsonb"),
    ];
    for (kind, expected) in kinds {
        let meta = ModelMeta::new("a.M", "M", "m", vec![FieldSpec::new("f", kind)]).unwrap();
        let create = &ddl::create_table(D, &meta)[0].sql;
        assert!(
            create.contains(&format!("\"f\" {expected} NOT NULL")),
            "{create}"
        );
    }
}
