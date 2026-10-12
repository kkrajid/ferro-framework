mod common;

use common::{fk, model, register, shop, string};
use ferro_orm::ddl;
use ferro_orm::{
    FieldKind, FieldSpec, ForeignKeyRef, ModelMeta, OnDelete, Registry, Reverse, resolve_lookup,
};

#[test]
fn foreign_key_fields_store_the_target_key() {
    let registry = shop();
    let product = model(&registry, "Product");
    let category = &product.fields[product.field("category").unwrap()];
    assert_eq!(category.attname, "category_id");
    assert_eq!(category.column, "category_id");
    assert_eq!(category.kind, FieldKind::BigInteger);
    assert!(category.index && !category.unique && !category.null);
    assert_eq!(
        category.references,
        Some(ForeignKeyRef {
            table: "category".into(),
            column: "id".into(),
            on_delete: OnDelete::Protect,
        })
    );
    assert_eq!(category.relation.as_ref().unwrap().target, "shop.Category");
    // The attribute name finds the same field.
    assert_eq!(product.field("category_id"), product.field("category"));
}

#[test]
fn self_references_and_custom_target_keys() {
    let registry = shop();
    let category = model(&registry, "Category");
    let parent = &category.fields[category.field("parent").unwrap()];
    assert_eq!(parent.references.as_ref().unwrap().table, "category");
    assert!(parent.null);

    let mut registry = Registry::default();
    let mut code = FieldSpec::new("code", FieldKind::String { max_length: 12 });
    code.primary_key = true;
    register(&mut registry, "Country", "country", vec![code], &[]);
    let mut capital = FieldSpec::relation("country", "shop.Country", OnDelete::Cascade, true);
    capital.column = Some("country_code".into());
    let city = register(&mut registry, "City", "city", vec![capital], &[]);
    let field = &city.fields[city.field("country").unwrap()];
    assert_eq!(field.kind, FieldKind::String { max_length: 12 });
    assert_eq!(field.column, "country_code");
    assert_eq!(field.references.as_ref().unwrap().column, "code");
    assert!(field.unique && !field.index, "one-to-one keys are unique");
}

#[test]
fn invalid_relations_are_rejected() {
    let registry = shop();
    let resolve = |q: &str| registry.get(q);
    let build = |fields: Vec<FieldSpec>| {
        ModelMeta::build("shop.Bad", "Bad", "bad", fields, &[], &resolve)
            .unwrap_err()
            .to_string()
    };
    let mut set_null = FieldSpec::relation("category", "shop.Category", OnDelete::SetNull, false);
    set_null.null = false;
    assert!(build(vec![set_null]).contains("SET_NULL needs null=True"));
    assert!(
        build(vec![FieldSpec::relation(
            "x",
            "shop.Missing",
            OnDelete::Cascade,
            false
        )])
        .contains("related model 'shop.Missing' is not defined")
    );
    let mut key = FieldSpec::relation("category", "shop.Category", OnDelete::Cascade, false);
    key.primary_key = true;
    assert!(build(vec![key]).contains("a relation cannot be the primary key"));
    assert!(
        build(vec![
            FieldSpec::relation("category", "shop.Category", OnDelete::Cascade, false),
            FieldSpec::new("category_id", FieldKind::Integer),
        ])
        .contains("clashes")
    );
}

#[test]
fn unique_together_becomes_a_composite_unique_index() {
    let registry = shop();
    let link = model(&registry, "Product_tags");
    assert_eq!(link.indexes.len(), 1);
    assert_eq!(link.indexes[0].name, "product_tags_product_id_tag_id_uniq");
    assert_eq!(link.indexes[0].columns, vec!["product_id", "tag_id"]);
    assert!(link.indexes[0].unique);
}

#[test]
fn reverse_names_default_and_custom() {
    let registry = shop();
    assert!(matches!(
        registry.reverse("shop.Category", "products"),
        Some(Reverse::Field { ref source, .. }) if source.table == "product"
    ));
    assert!(matches!(
        registry.reverse("shop.Product", "order_item"),
        Some(Reverse::Field { .. })
    ));
    assert!(matches!(
        registry.reverse("shop.Tag", "products"),
        Some(Reverse::ManyToMany(_))
    ));
    assert!(
        registry.reverse("shop.Product", "product_tags").is_none(),
        "related_name='+'"
    );
    assert!(registry.reverse("shop.Category", "children").is_some());
}

#[test]
fn clashing_reverse_names_are_rejected() {
    let mut registry = shop();
    let resolve_meta = {
        let lookup = |q: &str| registry.get(q);
        ModelMeta::build(
            "shop.Offer",
            "Offer",
            "offer",
            vec![fk(
                "category",
                "shop.Category",
                OnDelete::Cascade,
                Some("products"),
            )],
            &[],
            &lookup,
        )
        .unwrap()
    };
    let err = registry.register(resolve_meta).unwrap_err().to_string();
    assert!(err.contains("reverse name 'products' clashes"), "{err}");

    let field_clash = {
        let lookup = |q: &str| registry.get(q);
        ModelMeta::build(
            "shop.Note",
            "Note",
            "note",
            vec![fk(
                "category",
                "shop.Category",
                OnDelete::Cascade,
                Some("name"),
            )],
            &[],
            &lookup,
        )
        .unwrap()
    };
    let err = registry.register(field_clash).unwrap_err().to_string();
    assert!(err.contains("clashes with field 'name'"), "{err}");
}

#[test]
fn lookups_resolve_across_relations() {
    let registry = shop();
    let order = model(&registry, "Order");
    let (path, _) = resolve_lookup(&registry, &order, "items__product__category__name").unwrap();
    assert_eq!(path.hops.len(), 3);
    assert_eq!(path.model.table, "category");
    let product = model(&registry, "Product");
    for key in ["category", "category_id", "category__id", "category__pk"] {
        let (path, _) = resolve_lookup(&registry, &product, key).unwrap();
        assert!(path.is_local(), "{key} compares the local key");
    }
    let (path, _) = resolve_lookup(&registry, &product, "tags").unwrap();
    assert_eq!(path.model.table, "tag");
    let err = resolve_lookup(&registry, &product, "category__nope")
        .unwrap_err()
        .to_string();
    assert!(err.contains("Category has no field 'nope'"), "{err}");
    let err = resolve_lookup(&registry, &product, "name__foo__bar")
        .unwrap_err()
        .to_string();
    assert!(err.contains("is not a relation"), "{err}");
}

#[test]
fn tables_are_created_after_the_tables_they_reference() {
    let registry = shop();
    let all = registry.all();
    let mut reversed = all.clone();
    reversed.reverse();
    let ordered = ddl::creation_order(&reversed).unwrap();
    let position = |table: &str| ordered.iter().position(|m| m.table == table).unwrap();
    assert!(position("category") < position("product"));
    assert!(position("product") < position("product_tags"));
    assert!(position("tag") < position("product_tags"));
    assert!(position("order") < position("order_item"));

    let mut cyclic = Registry::default();
    register(&mut cyclic, "A", "a", vec![string("name")], &[]);
    let b = register(
        &mut cyclic,
        "B",
        "b",
        vec![fk("a", "shop.A", OnDelete::Cascade, None)],
        &[],
    );
    let a = register(
        &mut cyclic,
        "A",
        "a",
        vec![fk("b", "shop.B", OnDelete::Cascade, Some("+"))],
        &[],
    );
    let err = ddl::creation_order(&[a, b]).unwrap_err().to_string();
    assert!(err.contains("circular foreign keys"), "{err}");
}

#[test]
fn ddl_declares_foreign_keys_and_composite_indexes() {
    use ferro_orm::compile::{PostgresDialect, SqliteDialect};
    let registry = shop();
    let product = model(&registry, "Product");
    let statements = ddl::create_table(&SqliteDialect, &product);
    assert!(
        statements[0].sql.contains(
            r#""category_id" bigint NOT NULL REFERENCES "category" ("id") ON DELETE RESTRICT"#
        ),
        "{}",
        statements[0].sql
    );
    assert!(
        statements
            .iter()
            .any(|s| s.sql
                == r#"CREATE INDEX "product_category_id_idx" ON "product" ("category_id")"#)
    );
    let link = model(&registry, "Product_tags");
    let statements = ddl::create_table(&PostgresDialect, &link);
    assert!(statements.iter().any(|s| s.sql
        == r#"CREATE UNIQUE INDEX "product_tags_product_id_tag_id_uniq" ON "product_tags" ("product_id", "tag_id")"#));
    let category = model(&registry, "Category");
    let statements = ddl::create_table(&PostgresDialect, &category);
    assert!(
        statements[0]
            .sql
            .contains(r#""parent_id" bigint REFERENCES "category" ("id") ON DELETE SET NULL"#),
        "{}",
        statements[0].sql
    );
}
