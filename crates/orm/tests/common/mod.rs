//! A small shop schema with every relation kind, shared by the relation tests.
#![allow(dead_code)]

use std::sync::Arc;

use ferro_database::Value;
use ferro_orm::{
    Condition, FieldKind, FieldSpec, ManyToMany, ModelMeta, Node, OnDelete, Operand, Registry,
    Target, resolve_lookup,
};

pub fn register(
    registry: &mut Registry,
    label: &str,
    table: &str,
    fields: Vec<FieldSpec>,
    unique_together: &[&[&str]],
) -> Arc<ModelMeta> {
    let groups: Vec<Vec<String>> = unique_together
        .iter()
        .map(|g| g.iter().map(|s| s.to_string()).collect())
        .collect();
    let meta = {
        let lookup = |q: &str| registry.get(q);
        ModelMeta::build(
            &format!("shop.{label}"),
            label,
            table,
            fields,
            &groups,
            &lookup,
        )
        .unwrap()
    };
    registry.register(meta).unwrap()
}

pub fn fk(name: &str, target: &str, on_delete: OnDelete, related: Option<&str>) -> FieldSpec {
    let mut spec = FieldSpec::relation(name, target, on_delete, false);
    if let Some(relation) = &mut spec.relation {
        relation.related_name = related.map(str::to_string);
    }
    if on_delete == OnDelete::SetNull {
        spec.null = true;
    }
    spec
}

pub fn string(name: &str) -> FieldSpec {
    FieldSpec::new(name, FieldKind::String { max_length: 100 })
}

/// category(parent → self) ← product(category, tags ↔ tag) ← order_item(order → order)
pub fn shop() -> Registry {
    let mut registry = Registry::default();
    register(
        &mut registry,
        "Category",
        "category",
        vec![
            string("name"),
            fk("parent", "self", OnDelete::SetNull, Some("children")),
        ],
        &[],
    );
    let mut tag_name = string("name");
    tag_name.unique = true;
    register(&mut registry, "Tag", "tag", vec![tag_name], &[]);
    register(
        &mut registry,
        "Product",
        "product",
        vec![
            string("name"),
            FieldSpec::new(
                "price",
                FieldKind::Decimal {
                    max_digits: 10,
                    decimal_places: 2,
                },
            ),
            FieldSpec::new("stock", FieldKind::Integer),
            fk(
                "category",
                "shop.Category",
                OnDelete::Protect,
                Some("products"),
            ),
        ],
        &[],
    );
    let through = register(
        &mut registry,
        "Product_tags",
        "product_tags",
        vec![
            fk("product", "shop.Product", OnDelete::Cascade, Some("+")),
            fk("tag", "shop.Tag", OnDelete::Cascade, Some("+")),
        ],
        &[&["product", "tag"]],
    );
    registry
        .register_many_to_many(ManyToMany {
            source: "shop.Product".into(),
            name: "tags".into(),
            target: "shop.Tag".into(),
            through: through.qualname.clone(),
            from_field: through.field("product").unwrap(),
            to_field: through.field("tag").unwrap(),
            related_name: Some("products".into()),
        })
        .unwrap();
    register(&mut registry, "Order", "order", vec![string("number")], &[]);
    let mut note = FieldSpec::new("note", FieldKind::Text);
    note.null = true;
    register(
        &mut registry,
        "OrderItem",
        "order_item",
        vec![
            fk("order", "shop.Order", OnDelete::Cascade, Some("items")),
            fk("product", "shop.Product", OnDelete::Protect, None),
            FieldSpec::new("quantity", FieldKind::Integer),
            note,
        ],
        &[],
    );
    registry
}

pub fn model(registry: &Registry, label: &str) -> Arc<ModelMeta> {
    registry.get(&format!("shop.{label}")).unwrap()
}

pub fn leaf(registry: &Registry, root: &Arc<ModelMeta>, key: &str, operand: Operand) -> Node {
    let (path, lookup) = resolve_lookup(registry, root, key).unwrap();
    let field = path.meta().clone();
    Node::Leaf(Condition::on(&root.label, &field, Target::Field(path), lookup, operand).unwrap())
}

pub fn text(value: &str) -> Operand {
    Operand::Value(Value::Text(value.into()))
}

pub fn int(value: i64) -> Operand {
    Operand::Value(Value::Integer(value))
}
