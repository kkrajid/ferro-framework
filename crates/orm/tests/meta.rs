use ferro_orm::{FieldKind, FieldSpec, ModelMeta, Registry, snake_case};

fn spec(name: &str, kind: FieldKind) -> FieldSpec {
    FieldSpec::new(name, kind)
}

fn product() -> ModelMeta {
    ModelMeta::new(
        "shop.Product",
        "Product",
        "product",
        vec![
            spec("name", FieldKind::String { max_length: 200 }),
            spec(
                "price",
                FieldKind::Decimal {
                    max_digits: 10,
                    decimal_places: 2,
                },
            ),
        ],
    )
    .unwrap()
}

#[test]
fn implicit_auto_id_is_first() {
    let meta = product();
    assert_eq!(meta.fields[0].name, "id");
    assert!(meta.fields[0].auto);
    assert!(meta.fields[0].primary_key);
    assert_eq!(meta.fields[0].kind, FieldKind::BigInteger);
    assert_eq!(meta.pk, 0);
    assert_eq!(meta.field("pk"), Some(0));
    assert_eq!(meta.field("price"), Some(2));
    assert_eq!(meta.field("nope"), None);
    assert_eq!(meta.field_names(), vec!["id", "name", "price"]);
}

#[test]
fn explicit_primary_key_replaces_auto_id() {
    let mut code = spec("code", FieldKind::Uuid);
    code.primary_key = true;
    let meta = ModelMeta::new("a.Token", "Token", "token", vec![code]).unwrap();
    assert_eq!(meta.fields.len(), 1);
    assert_eq!(meta.pk_field().name, "code");
    assert!(!meta.pk_field().auto);
    assert!(meta.pk_field().unique);
    assert!(!meta.pk_field().null);
}

#[test]
fn custom_column_names() {
    let mut name = spec("name", FieldKind::Text);
    name.column = Some("product_name".into());
    let meta = ModelMeta::new("a.P", "P", "p", vec![name]).unwrap();
    assert_eq!(meta.fields[1].column, "product_name");
}

#[test]
fn rejects_invalid_definitions() {
    let cases: Vec<(Vec<FieldSpec>, &str, &str)> = vec![
        (
            vec![spec("1bad", FieldKind::Text)],
            "t",
            "not a valid identifier",
        ),
        (vec![spec("objects", FieldKind::Text)], "t", "reserved"),
        (vec![spec("_private", FieldKind::Text)], "t", "reserved"),
        (
            vec![spec("a", FieldKind::Text), spec("a", FieldKind::Text)],
            "t",
            "duplicate field",
        ),
        (
            vec![spec("id", FieldKind::Text)],
            "t",
            "'id' is reserved for the automatic primary key",
        ),
        (vec![spec("a", FieldKind::Text)], "bad table", "table name"),
        (
            vec![spec(
                "a",
                FieldKind::Decimal {
                    max_digits: 5,
                    decimal_places: 6,
                },
            )],
            "t",
            "decimal_places",
        ),
        (
            vec![spec(
                "a",
                FieldKind::Decimal {
                    max_digits: 0,
                    decimal_places: 0,
                },
            )],
            "t",
            "max_digits",
        ),
        (
            vec![spec("a", FieldKind::String { max_length: 0 })],
            "t",
            "max_length",
        ),
    ];
    for (fields, table, expected) in cases {
        let err = ModelMeta::new("m.Model", "Model", table, fields).unwrap_err();
        assert!(err.to_string().contains(expected), "{err}");
        assert!(err.to_string().contains("Model"), "{err}");
    }

    let mut a = spec("a", FieldKind::Integer);
    a.primary_key = true;
    let mut b = spec("b", FieldKind::Integer);
    b.primary_key = true;
    let err = ModelMeta::new("m.M", "M", "m", vec![a, b]).unwrap_err();
    assert!(
        err.to_string().contains("more than one primary key"),
        "{err}"
    );

    let mut nullable_pk = spec("a", FieldKind::Integer);
    nullable_pk.primary_key = true;
    nullable_pk.null = true;
    let err = ModelMeta::new("m.M", "M", "m", vec![nullable_pk]).unwrap_err();
    assert!(
        err.to_string().contains("primary key cannot be null"),
        "{err}"
    );

    let mut col_a = spec("a", FieldKind::Text);
    col_a.column = Some("x".into());
    let mut col_b = spec("b", FieldKind::Text);
    col_b.column = Some("x".into());
    let err = ModelMeta::new("m.M", "M", "m", vec![col_a, col_b]).unwrap_err();
    assert!(err.to_string().contains("duplicate column"), "{err}");
}

#[test]
fn snake_case_table_names() {
    assert_eq!(snake_case("Product"), "product");
    assert_eq!(snake_case("OrderItem"), "order_item");
    assert_eq!(snake_case("HTTPLog"), "http_log");
    assert_eq!(snake_case("UserV2"), "user_v2");
}

#[test]
fn registry_replaces_redefinitions_and_rejects_clashes() {
    let mut registry = Registry::default();
    let first = registry.register(product()).unwrap();
    assert_eq!(first.table, "product");
    let again = registry.register(product()).unwrap();
    assert_eq!(registry.all().len(), 1);
    assert_eq!(again.qualname, "shop.Product");

    let clash = ModelMeta::new(
        "other.Item",
        "Item",
        "product",
        vec![spec("x", FieldKind::Integer)],
    )
    .unwrap();
    let err = registry.register(clash).unwrap_err();
    let message = err.to_string();
    assert!(message.contains("shop.Product"), "{message}");
    assert!(message.contains("other.Item"), "{message}");

    let moved = ModelMeta::new(
        "shop.Product",
        "Product",
        "products",
        vec![spec("x", FieldKind::Integer)],
    )
    .unwrap();
    registry.register(moved).unwrap();
    assert!(registry.by_table("product").is_none());
    assert!(registry.by_table("products").is_some());
}
