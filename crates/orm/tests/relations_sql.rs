mod common;

use common::{int, leaf, model, shop, text};
use ferro_database::Value;
use ferro_orm::compile::{self, Assignment, PostgresDialect, SqliteDialect};
use ferro_orm::{ArithOp, Expr, Node, Operand, Query};

const D: &SqliteDialect = &SqliteDialect;

fn where_of(sql: &str) -> &str {
    sql.split_once(" WHERE ").map_or("", |(_, w)| w)
}

#[test]
fn local_key_shortcuts_need_no_subquery() {
    let registry = shop();
    let product = model(&registry, "Product");
    for key in ["category", "category_id", "category__id", "category__pk"] {
        let q = Query::new(product.clone()).with_node(Some(leaf(&registry, &product, key, int(3))));
        assert_eq!(
            where_of(&compile::select(D, &q).sql),
            r#"("category_id" = ?)"#
        );
    }
    let q = Query::new(product.clone()).with_node(Some(leaf(
        &registry,
        &product,
        "category__isnull",
        Operand::Flag(true),
    )));
    assert_eq!(
        where_of(&compile::select(D, &q).sql),
        r#"("category_id" IS NULL)"#
    );
}

#[test]
fn forward_reverse_and_many_to_many_lookups_use_exists() {
    let registry = shop();
    let product = model(&registry, "Product");
    let q = Query::new(product.clone()).with_node(Some(leaf(
        &registry,
        &product,
        "category__name",
        text("Phones"),
    )));
    let statement = compile::select(D, &q);
    assert_eq!(
        where_of(&statement.sql),
        r#"(EXISTS (SELECT 1 FROM "category" AS "T1" WHERE "T1"."id" = "product"."category_id" AND "T1"."name" = ?))"#
    );
    assert_eq!(statement.params, vec![Value::Text("Phones".into())]);

    let category = model(&registry, "Category");
    let q = Query::new(category.clone()).with_node(Some(leaf(
        &registry,
        &category,
        "products__stock__gt",
        int(5),
    )));
    assert_eq!(
        where_of(&compile::select(D, &q).sql),
        r#"(EXISTS (SELECT 1 FROM "product" AS "T1" WHERE "T1"."category_id" = "category"."id" AND "T1"."stock" > ?))"#
    );

    let q = Query::new(product.clone()).with_node(Some(leaf(
        &registry,
        &product,
        "tags__name",
        text("sale"),
    )));
    assert_eq!(
        where_of(&compile::select(D, &q).sql),
        r#"(EXISTS (SELECT 1 FROM "product_tags" AS "T1" INNER JOIN "tag" AS "T2" ON "T2"."id" = "T1"."tag_id" WHERE "T1"."product_id" = "product"."id" AND "T2"."name" = ?))"#
    );

    let tag = model(&registry, "Tag");
    let q =
        Query::new(tag.clone()).with_node(Some(leaf(&registry, &tag, "products__stock", int(0))));
    assert_eq!(
        where_of(&compile::select(D, &q).sql),
        r#"(EXISTS (SELECT 1 FROM "product_tags" AS "T1" INNER JOIN "product" AS "T2" ON "T2"."id" = "T1"."product_id" WHERE "T1"."tag_id" = "tag"."id" AND "T2"."stock" = ?))"#
    );
}

#[test]
fn same_call_conditions_share_one_subquery() {
    let registry = shop();
    let order = model(&registry, "Order");
    let quantity = leaf(&registry, &order, "items__quantity__gt", int(1));
    let note = leaf(&registry, &order, "items__note", text("gift"));
    let together =
        Query::new(order.clone()).with_node(Node::and(vec![quantity.clone(), note.clone()]));
    assert_eq!(
        where_of(&compile::select(D, &together).sql),
        r#"(EXISTS (SELECT 1 FROM "order_item" AS "T1" WHERE "T1"."order_id" = "order"."id" AND "T1"."quantity" > ? AND "T1"."note" = ?))"#
    );
    let separate = Query::new(order.clone())
        .with_node(Some(quantity))
        .with_node(Some(note));
    assert_eq!(
        where_of(&compile::select(D, &separate).sql),
        r#"(EXISTS (SELECT 1 FROM "order_item" AS "T1" WHERE "T1"."order_id" = "order"."id" AND "T1"."quantity" > ?)) AND (EXISTS (SELECT 1 FROM "order_item" AS "T2" WHERE "T2"."order_id" = "order"."id" AND "T2"."note" = ?))"#
    );
}

#[test]
fn multi_hop_paths_nest_subqueries() {
    let registry = shop();
    let order = model(&registry, "Order");
    let q = Query::new(order.clone()).with_node(Some(leaf(
        &registry,
        &order,
        "items__product__category__name",
        text("Phones"),
    )));
    assert_eq!(
        where_of(&compile::select(D, &q).sql),
        concat!(
            r#"(EXISTS (SELECT 1 FROM "order_item" AS "T1" WHERE "T1"."order_id" = "order"."id" AND "#,
            r#"EXISTS (SELECT 1 FROM "product" AS "T2" WHERE "T2"."id" = "T1"."product_id" AND "#,
            r#"EXISTS (SELECT 1 FROM "category" AS "T3" WHERE "T3"."id" = "T2"."category_id" AND "T3"."name" = ?))))"#
        )
    );
}

#[test]
fn exclude_and_isnull_across_relations() {
    let registry = shop();
    let category = model(&registry, "Category");
    let q = Query::new(category.clone()).with_node(Some(
        leaf(&registry, &category, "products__name", text("x")).negate(),
    ));
    assert_eq!(
        where_of(&compile::select(D, &q).sql),
        r#"NOT (EXISTS (SELECT 1 FROM "product" AS "T1" WHERE "T1"."category_id" = "category"."id" AND "T1"."name" = ?))"#
    );
    let q = Query::new(category.clone()).with_node(Some(leaf(
        &registry,
        &category,
        "products__isnull",
        Operand::Flag(true),
    )));
    assert_eq!(
        where_of(&compile::select(D, &q).sql),
        r#"(NOT EXISTS (SELECT 1 FROM "product" AS "T1" WHERE "T1"."category_id" = "category"."id"))"#
    );
    let order = model(&registry, "Order");
    let q = Query::new(order.clone()).with_node(Some(leaf(
        &registry,
        &order,
        "items__note__isnull",
        Operand::Flag(true),
    )));
    assert_eq!(
        where_of(&compile::select(D, &q).sql),
        concat!(
            r#"((NOT EXISTS (SELECT 1 FROM "order_item" AS "T1" WHERE "T1"."order_id" = "order"."id") OR "#,
            r#"EXISTS (SELECT 1 FROM "order_item" AS "T2" WHERE "T2"."order_id" = "order"."id" AND "T2"."note" IS NULL)))"#
        )
    );
}

#[test]
fn q_trees_render_with_null_guards_under_not() {
    let registry = shop();
    let category = model(&registry, "Category");
    let parent_is_3 = leaf(&registry, &category, "parent", int(3));
    let named = leaf(&registry, &category, "name", text("a"));
    let tree = Node::or(vec![parent_is_3.negate(), named]);
    let q = Query::new(category.clone()).with_node(tree);
    assert_eq!(
        where_of(&compile::select(D, &q).sql),
        r#"(NOT ("parent_id" = ? AND "parent_id" IS NOT NULL) OR "name" = ?)"#
    );
}

#[test]
fn select_related_and_related_ordering_join() {
    let registry = shop();
    let product = model(&registry, "Product");
    let q = Query::new(product.clone())
        .select_related(&registry, &["category__parent"])
        .unwrap()
        .order_by_in(&registry, &["category__name", "-price"])
        .unwrap();
    assert_eq!(
        compile::select(D, &q).sql,
        concat!(
            r#"SELECT "product"."id", "product"."name", "product"."price", "product"."stock", "product"."category_id", "#,
            r#""T1"."id", "T1"."name", "T1"."parent_id", "T2"."id", "T2"."name", "T2"."parent_id" "#,
            r#"FROM "product" LEFT JOIN "category" AS "T1" ON "T1"."id" = "product"."category_id" "#,
            r#"LEFT JOIN "category" AS "T2" ON "T2"."id" = "T1"."parent_id" "#,
            r#"ORDER BY "T1"."name" ASC, "product"."price" DESC"#
        )
    );
    let err = Query::new(product.clone())
        .select_related(&registry, &["tags"])
        .unwrap_err()
        .to_string();
    assert!(err.contains("prefetch_related"), "{err}");
    let err = Query::new(product.clone())
        .order_by_in(&registry, &["tags__name"])
        .unwrap_err()
        .to_string();
    assert!(err.contains("many-valued"), "{err}");
}

#[test]
fn postgres_placeholders_follow_text_order() {
    let registry = shop();
    let order = model(&registry, "Order");
    let q = Query::new(order.clone())
        .with_node(Some(leaf(&registry, &order, "number", text("A1"))))
        .with_node(Some(leaf(&registry, &order, "items__quantity", int(2))))
        .window(None, Some(10));
    let statement = compile::select(&PostgresDialect, &q);
    assert_eq!(
        statement.sql,
        r#"SELECT "id", "number" FROM "order" WHERE ("number" = $1) AND (EXISTS (SELECT 1 FROM "order_item" AS "T1" WHERE "T1"."order_id" = "order"."id" AND "T1"."quantity" = $2)) LIMIT $3"#
    );
    assert_eq!(statement.params.len(), 3);
}

#[test]
fn update_with_f_expressions_and_relation_filters() {
    let registry = shop();
    let product = model(&registry, "Product");
    let stock = product.field("stock").unwrap();
    let q = Query::new(product.clone()).with_node(Some(leaf(
        &registry,
        &product,
        "category__name",
        text("Phones"),
    )));
    let decrement = Expr::Binary(
        Box::new(Expr::Field(stock)),
        ArithOp::Sub,
        Box::new(Expr::Value(Value::Integer(2))),
    );
    let statement =
        compile::update(D, &q, &[(stock, Assignment::Expr(decrement.clone()))]).unwrap();
    assert_eq!(
        statement.sql,
        r#"UPDATE "product" SET "stock" = ("stock" - ?) WHERE (EXISTS (SELECT 1 FROM "category" AS "T1" WHERE "T1"."id" = "product"."category_id" AND "T1"."name" = ?))"#
    );
    assert_eq!(
        statement.params,
        vec![Value::Integer(2), Value::Text("Phones".into())]
    );
    let price = product.field("price").unwrap();
    let compare = Query::new(product.clone()).with_node(Some(leaf(
        &registry,
        &product,
        "stock__gt",
        Operand::Expr(Expr::Field(price)),
    )));
    assert_eq!(
        where_of(&compile::select(D, &compare).sql),
        r#"("stock" > "price")"#
    );
}

#[test]
fn insert_can_ignore_conflicts() {
    let registry = shop();
    let link = model(&registry, "Product_tags");
    let statement = compile::insert_with(
        D,
        &link,
        &[(1, Value::Integer(1)), (2, Value::Integer(2))],
        true,
        true,
    );
    assert_eq!(
        statement.sql,
        r#"INSERT INTO "product_tags" ("product_id", "tag_id") VALUES (?, ?) ON CONFLICT DO NOTHING RETURNING "id""#
    );
}
