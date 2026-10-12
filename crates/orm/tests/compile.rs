use std::sync::Arc;

use ferro_database::{Statement, Value};
use ferro_orm::compile::{self, SqliteDialect};
use ferro_orm::ddl;
use ferro_orm::{FieldKind, FieldSpec, Lookup, ModelMeta, Operand, Query, parse_key};

const D: &SqliteDialect = &SqliteDialect;

fn product() -> Arc<ModelMeta> {
    let mut name = FieldSpec::new("name", FieldKind::String { max_length: 200 });
    name.null = true;
    name.index = true;
    let mut sku = FieldSpec::new("sku", FieldKind::String { max_length: 40 });
    sku.unique = true;
    let fields = vec![
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
        FieldSpec::new("data", FieldKind::Json),
    ];
    Arc::new(ModelMeta::new("shop.Product", "Product", "product", fields).unwrap())
}

const COLUMNS: &str = r#""id", "name", "sku", "price", "stock", "active", "data""#;

fn text(s: &str) -> Operand {
    Operand::Value(Value::Text(s.into()))
}

fn select(q: &Query) -> Statement {
    compile::select(D, q)
}

fn sql(q: &Query) -> String {
    select(q).sql
}

#[test]
fn select_all() {
    let q = Query::new(product());
    assert_eq!(
        select(&q),
        Statement::new(format!(r#"SELECT {COLUMNS} FROM "product""#), vec![])
    );
}

#[test]
fn filter_and_order() {
    let q = Query::new(product())
        .filter(
            false,
            vec![
                ("price__gte", Operand::Value(Value::Real(1000.0))),
                ("active", Operand::Value(Value::Integer(1))),
            ],
        )
        .unwrap()
        .order_by(&["-price"])
        .unwrap();
    assert_eq!(
        select(&q),
        Statement::new(
            format!(
                r#"SELECT {COLUMNS} FROM "product" WHERE ("price" >= ? AND "active" = ?) ORDER BY "price" DESC"#
            ),
            vec![Value::Real(1000.0), Value::Integer(1)]
        )
    );
}

#[test]
fn groups_combine_with_and_and_exclude_keeps_nulls() {
    let q = Query::new(product())
        .filter(
            false,
            vec![("stock__gt", Operand::Value(Value::Integer(0)))],
        )
        .unwrap()
        .filter(true, vec![("name", text("x"))])
        .unwrap()
        .filter(true, vec![("sku", text("y"))])
        .unwrap();
    assert_eq!(
        sql(&q),
        format!(
            r#"SELECT {COLUMNS} FROM "product" WHERE ("stock" > ?) AND NOT ("name" = ? AND "name" IS NOT NULL) AND NOT ("sku" = ?)"#
        )
    );
}

#[test]
fn none_means_isnull() {
    let q = Query::new(product())
        .filter(false, vec![("name", Operand::Value(Value::Null))])
        .unwrap()
        .filter(false, vec![("name__isnull", Operand::Flag(false))])
        .unwrap();
    let statement = select(&q);
    assert!(
        statement
            .sql
            .ends_with(r#"WHERE ("name" IS NULL) AND ("name" IS NOT NULL)"#),
        "{}",
        statement.sql
    );
    assert!(statement.params.is_empty());
}

#[test]
fn text_lookups_escape_patterns() {
    let cases = [
        (
            "name__contains",
            "a*b?[c",
            r#""name" GLOB ?"#,
            "*a[*]b[?][[]c*",
        ),
        ("name__startswith", "ab", r#""name" GLOB ?"#, "ab*"),
        ("name__endswith", "ab", r#""name" GLOB ?"#, "*ab"),
        (
            "name__icontains",
            r"50%_\",
            r#""name" LIKE ? ESCAPE '\'"#,
            r"%50\%\_\\%",
        ),
        (
            "name__iexact",
            "Ab%",
            r#""name" LIKE ? ESCAPE '\'"#,
            r"Ab\%",
        ),
    ];
    for (key, value, fragment, param) in cases {
        let q = Query::new(product())
            .filter(false, vec![(key, text(value))])
            .unwrap();
        let statement = select(&q);
        assert!(
            statement.sql.ends_with(&format!("WHERE ({fragment})")),
            "{key}: {}",
            statement.sql
        );
        assert_eq!(statement.params, vec![Value::Text(param.into())], "{key}");
    }
}

#[test]
fn comparisons_and_in() {
    for (key, op) in [("gt", ">"), ("gte", ">="), ("lt", "<"), ("lte", "<=")] {
        let q = Query::new(product())
            .filter(
                false,
                vec![(&*format!("stock__{key}"), Operand::Value(Value::Integer(5)))],
            )
            .unwrap();
        assert!(sql(&q).ends_with(&format!(r#"WHERE ("stock" {op} ?)"#)));
    }
    let q = Query::new(product())
        .filter(
            false,
            vec![(
                "id__in",
                Operand::List(vec![
                    Value::Integer(1),
                    Value::Integer(2),
                    Value::Integer(3),
                ]),
            )],
        )
        .unwrap();
    let statement = select(&q);
    assert!(statement.sql.ends_with(r#"WHERE ("id" IN (?, ?, ?))"#));
    assert_eq!(statement.params.len(), 3);

    let empty = Query::new(product())
        .filter(false, vec![("id__in", Operand::List(vec![]))])
        .unwrap();
    assert!(sql(&empty).ends_with("WHERE (0 = 1)"));
}

#[test]
fn windows() {
    let q = Query::new(product()).window(Some(20), Some(20));
    let statement = select(&q);
    assert!(
        statement.sql.ends_with("LIMIT ? OFFSET ?"),
        "{}",
        statement.sql
    );
    assert_eq!(
        statement.params,
        vec![Value::Integer(20), Value::Integer(20)]
    );

    let offset_only = Query::new(product()).window(Some(5), None);
    let statement = select(&offset_only);
    assert!(
        statement.sql.ends_with("LIMIT -1 OFFSET ?"),
        "{}",
        statement.sql
    );
    assert_eq!(statement.params, vec![Value::Integer(5)]);

    let limit_only = Query::new(product()).window(None, Some(3));
    assert!(sql(&limit_only).ends_with("LIMIT ?"));

    // [10:30] then [5:10] -> offset 15, limit 5
    let composed = Query::new(product())
        .window(Some(10), Some(20))
        .window(Some(5), Some(5));
    assert_eq!((composed.offset, composed.limit), (Some(15), Some(5)));
    // [0:4] then [3:] -> offset 3, limit 1
    let tail = Query::new(product())
        .window(None, Some(4))
        .window(Some(3), None);
    assert_eq!((tail.offset, tail.limit), (Some(3), Some(1)));
}

#[test]
fn reverse_ordering() {
    let unordered = Query::new(product()).reversed();
    assert!(sql(&unordered).ends_with(r#"ORDER BY "id" DESC"#));
    let ordered = Query::new(product())
        .order_by(&["-price", "name"])
        .unwrap()
        .reversed();
    assert!(sql(&ordered).ends_with(r#"ORDER BY "price" ASC, "name" DESC"#));
    assert!(ordered.is_ordered());
    assert!(!Query::new(product()).is_ordered());
}

#[test]
fn count_and_exists() {
    let q = Query::new(product())
        .filter(false, vec![("stock", Operand::Value(Value::Integer(0)))])
        .unwrap();
    assert_eq!(
        compile::count(D, &q),
        Statement::new(
            r#"SELECT COUNT(*) FROM "product" WHERE ("stock" = ?)"#,
            vec![Value::Integer(0)]
        )
    );
    assert_eq!(
        compile::exists(D, &q),
        Statement::new(
            r#"SELECT 1 FROM "product" WHERE ("stock" = ?) LIMIT 1"#,
            vec![Value::Integer(0)]
        )
    );
    let windowed = q.window(Some(2), Some(3));
    assert_eq!(
        compile::count(D, &windowed),
        Statement::new(
            r#"SELECT COUNT(*) FROM (SELECT "id" FROM "product" WHERE ("stock" = ?) LIMIT ? OFFSET ?) AS "subquery""#,
            vec![Value::Integer(0), Value::Integer(3), Value::Integer(2)]
        )
    );
}

#[test]
fn writes() {
    let meta = product();
    let insert = compile::insert(
        D,
        &meta,
        &[(1, Value::Text("Laptop".into())), (4, Value::Integer(3))],
        true,
    );
    assert_eq!(
        insert,
        Statement::new(
            r#"INSERT INTO "product" ("name", "stock") VALUES (?, ?) RETURNING "id""#,
            vec![Value::Text("Laptop".into()), Value::Integer(3)]
        )
    );
    assert_eq!(
        compile::insert(D, &meta, &[], true).sql,
        r#"INSERT INTO "product" DEFAULT VALUES RETURNING "id""#
    );
    assert_eq!(
        compile::insert(D, &meta, &[(4, Value::Integer(3))], false).sql,
        r#"INSERT INTO "product" ("stock") VALUES (?)"#
    );

    let q = Query::new(meta.clone())
        .filter(false, vec![("stock", Operand::Value(Value::Integer(0)))])
        .unwrap();
    assert_eq!(
        compile::update(D, &q, &[(5, compile::Assignment::Value(Value::Integer(0)))]).unwrap(),
        Statement::new(
            r#"UPDATE "product" SET "active" = ? WHERE ("stock" = ?)"#,
            vec![Value::Integer(0), Value::Integer(0)]
        )
    );
    assert_eq!(
        compile::delete(D, &q).unwrap(),
        Statement::new(
            r#"DELETE FROM "product" WHERE ("stock" = ?)"#,
            vec![Value::Integer(0)]
        )
    );
    assert_eq!(
        compile::delete(D, &Query::new(meta.clone())).unwrap().sql,
        r#"DELETE FROM "product""#
    );
    let windowed = q.window(None, Some(1));
    assert!(
        compile::update(
            D,
            &windowed,
            &[(5, compile::Assignment::Value(Value::Integer(0)))]
        )
        .unwrap_err()
        .to_string()
        .contains("slice")
    );
    assert!(
        compile::delete(D, &windowed)
            .unwrap_err()
            .to_string()
            .contains("slice")
    );
    assert!(compile::update(D, &q, &[]).is_err());

    assert_eq!(
        compile::update_row(
            D,
            &meta,
            &[(1, Value::Text("x".into())), (4, Value::Integer(1))],
            Value::Integer(7)
        ),
        Statement::new(
            r#"UPDATE "product" SET "name" = ?, "stock" = ? WHERE "id" = ?"#,
            vec![
                Value::Text("x".into()),
                Value::Integer(1),
                Value::Integer(7)
            ]
        )
    );
    assert_eq!(
        compile::delete_row(D, &meta, Value::Integer(7)),
        Statement::new(
            r#"DELETE FROM "product" WHERE "id" = ?"#,
            vec![Value::Integer(7)]
        )
    );
}

#[test]
fn identifiers_are_quoted() {
    let meta = Arc::new(
        ModelMeta::new(
            "a.Order",
            "Order",
            "order",
            vec![FieldSpec::new("select", FieldKind::Integer)],
        )
        .unwrap(),
    );
    assert_eq!(
        sql(&Query::new(meta)),
        r#"SELECT "id", "select" FROM "order""#
    );
}

#[test]
fn lookup_parsing_errors_list_options() {
    let meta = product();
    assert_eq!(parse_key(&meta, "price__gte").unwrap(), (3, Lookup::Gte));
    assert_eq!(parse_key(&meta, "pk").unwrap(), (0, Lookup::Exact));
    assert_eq!(parse_key(&meta, "pk__in").unwrap(), (0, Lookup::In));

    let err = parse_key(&meta, "colour").unwrap_err().to_string();
    assert!(err.contains("Product has no field 'colour'"), "{err}");
    assert!(err.contains("price"), "{err}");

    let err = parse_key(&meta, "price__near").unwrap_err().to_string();
    assert!(err.contains("unknown lookup 'near'"), "{err}");
    assert!(err.contains("icontains"), "{err}");

    let err = parse_key(&meta, "stock__contains").unwrap_err().to_string();
    assert!(
        err.contains("'contains' cannot be used on Integer field 'stock'"),
        "{err}"
    );
    let err = parse_key(&meta, "active__gt").unwrap_err().to_string();
    assert!(err.contains("Boolean"), "{err}");
    let err = parse_key(&meta, "data__lt").unwrap_err().to_string();
    assert!(err.contains("Json"), "{err}");
    assert!(parse_key(&meta, "name__price").is_err());
}

#[test]
fn operand_shapes_are_checked() {
    let q = Query::new(product());
    assert!(
        q.filter(false, vec![("id__in", Operand::Value(Value::Integer(1)))])
            .is_err()
    );
    assert!(
        q.filter(false, vec![("name__isnull", text("yes"))])
            .is_err()
    );
    assert!(
        q.filter(false, vec![("stock", Operand::List(vec![]))])
            .is_err()
    );
    assert!(
        q.filter(
            false,
            vec![("name__contains", Operand::Value(Value::Integer(1)))]
        )
        .is_err()
    );
    assert!(
        q.filter(false, vec![("stock__gt", Operand::Value(Value::Null))])
            .is_err()
    );
}

#[test]
fn ordering_errors() {
    let err = Query::new(product())
        .order_by(&["colour"])
        .unwrap_err()
        .to_string();
    assert!(
        err.contains("cannot order by unknown field 'colour'"),
        "{err}"
    );
    assert!(Query::new(product()).order_by(&["-pk"]).is_ok());
    assert!(Query::new(product()).order_by(&[""]).is_err());
}

#[test]
fn create_table_ddl() {
    let meta = product();
    let statements = ddl::create_table(D, &meta);
    assert_eq!(
        statements[0].sql,
        "CREATE TABLE \"product\" (\n  \
         \"id\" INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,\n  \
         \"name\" varchar(200),\n  \
         \"sku\" varchar(40) NOT NULL UNIQUE,\n  \
         \"price\" decimal(10, 2) NOT NULL,\n  \
         \"stock\" integer NOT NULL,\n  \
         \"active\" bool NOT NULL,\n  \
         \"data\" text NOT NULL\n)"
    );
    assert_eq!(
        statements[1].sql,
        r#"CREATE INDEX "product_name_idx" ON "product" ("name")"#
    );
    assert_eq!(statements.len(), 2);
}

#[test]
fn ddl_types_for_every_kind() {
    let kinds = [
        (FieldKind::Text, "text"),
        (FieldKind::Integer, "integer"),
        (FieldKind::BigInteger, "bigint"),
        (FieldKind::Float, "real"),
        (FieldKind::Date, "date"),
        (FieldKind::DateTime, "datetime"),
        (FieldKind::Time, "time"),
        (FieldKind::Uuid, "char(36)"),
        (FieldKind::Json, "text"),
    ];
    for (kind, expected) in kinds {
        let mut field = FieldSpec::new("f", kind.clone());
        field.primary_key = matches!(kind, FieldKind::Uuid);
        let meta = ModelMeta::new("a.M", "M", "m", vec![field]).unwrap();
        let create = &ddl::create_table(D, &meta)[0].sql;
        assert!(
            create.contains(&format!("\"f\" {expected} NOT NULL")),
            "{create}"
        );
        if matches!(kind, FieldKind::Uuid) {
            assert!(
                create.contains("\"f\" char(36) NOT NULL PRIMARY KEY"),
                "{create}"
            );
            assert!(!create.contains("AUTOINCREMENT"), "{create}");
        }
    }
}
