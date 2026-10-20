mod common;

use common::{int, model, shop};
use ferro_database::Value;
use ferro_orm::compile::{self, PostgresDialect, SqliteDialect};
use ferro_orm::{AggFunc, Aggregate, Condition, Lookup, Node, Operand, Query, Shape, Target};

const D: &SqliteDialect = &SqliteDialect;

fn agg(
    registry: &ferro_orm::Registry,
    model: &std::sync::Arc<ferro_orm::ModelMeta>,
    func: AggFunc,
    path: Option<&str>,
) -> Aggregate {
    Aggregate::resolve(registry, model, func, path, false).unwrap()
}

#[test]
fn aggregate_over_fields_and_forward_paths() {
    let registry = shop();
    let product = model(&registry, "Product");
    let mut avg = agg(&registry, &product, AggFunc::Avg, Some("price"));
    avg.distinct = true;
    let mut total = agg(&registry, &product, AggFunc::Sum, Some("stock"));
    total.default = Some(Value::Integer(0));
    let aggregates = vec![
        agg(&registry, &product, AggFunc::Count, None),
        avg,
        total,
        agg(&registry, &product, AggFunc::Max, Some("category__name")),
    ];
    let q = Query::new(product.clone()).with_node(Some(common::leaf(
        &registry,
        &product,
        "stock__gt",
        int(0),
    )));
    let statement = compile::aggregate(D, &q, &aggregates).unwrap();
    assert_eq!(
        statement.sql,
        concat!(
            r#"SELECT COUNT(*), AVG(DISTINCT "product"."price"), COALESCE(SUM("product"."stock"), ?), "#,
            r#"MAX("T1"."name") FROM "product" LEFT JOIN "category" AS "T1" ON "T1"."id" = "product"."category_id" "#,
            r#"WHERE ("product"."stock" > ?)"#
        )
    );
    assert_eq!(statement.params, vec![Value::Integer(0), Value::Integer(0)]);

    let windowed = q.order_by(&["-price"]).unwrap().window(None, Some(5));
    let statement = compile::aggregate(
        &PostgresDialect,
        &windowed,
        &[agg(&registry, &product, AggFunc::Sum, Some("price"))],
    )
    .unwrap();
    assert_eq!(
        statement.sql,
        r#"SELECT SUM("subquery"."c0") FROM (SELECT "id", "price" AS "c0" FROM "product" WHERE ("stock" > $1) ORDER BY "price" DESC LIMIT $2) AS "subquery""#
    );

    let order = model(&registry, "Order");
    let err = compile::aggregate(
        D,
        &Query::new(order.clone()),
        &[agg(
            &registry,
            &order,
            AggFunc::Sum,
            Some("items__quantity"),
        )],
    )
    .unwrap_err()
    .to_string();
    assert!(err.contains("many-valued"), "{err}");
    let err = Aggregate::resolve(&registry, &product, AggFunc::Sum, Some("name"), false)
        .unwrap_err()
        .to_string();
    assert!(
        err.contains("Sum() cannot be used on String field 'name'"),
        "{err}"
    );
}

#[test]
fn per_record_annotations_are_correlated_subqueries() {
    let registry = shop();
    let category = model(&registry, "Category");
    let count = agg(&registry, &category, AggFunc::Count, Some("products"));
    let q = Query::new(category.clone())
        .annotate(vec![("n".into(), count)])
        .unwrap();
    let n = q.annotation("n").unwrap();
    let field = q.annotations[n].output_field();
    let q = q
        .with_node(Some(Node::Leaf(
            Condition::on(
                "Category",
                &field,
                Target::Annotation(n),
                Lookup::Gt,
                Operand::Value(Value::Integer(1)),
            )
            .unwrap(),
        )))
        .order_by_in(&registry, &["-n"])
        .unwrap();
    let subquery = r#"(SELECT COUNT("T1"."id") FROM "product" AS "T1" WHERE "T1"."category_id" = "category"."id")"#;
    assert_eq!(
        compile::select(D, &q).sql,
        format!(
            r#"SELECT "id", "name", "parent_id", {} FROM "category" WHERE ({} > ?) ORDER BY {} DESC"#,
            subquery,
            subquery.replace("T1", "T2"),
            subquery.replace("T1", "T3"),
        )
    );

    let order = model(&registry, "Order");
    let mut revenue = agg(
        &registry,
        &order,
        AggFunc::Sum,
        Some("items__product__price"),
    );
    revenue.default = Some(Value::Integer(0));
    let q = Query::new(order.clone())
        .annotate(vec![("revenue".into(), revenue)])
        .unwrap();
    assert_eq!(
        compile::select(D, &q).sql,
        concat!(
            r#"SELECT "id", "number", COALESCE((SELECT SUM("T2"."price") FROM "order_item" AS "T1" "#,
            r#"INNER JOIN "product" AS "T2" ON "T2"."id" = "T1"."product_id" WHERE "T1"."order_id" = "order"."id"), ?) "#,
            r#"FROM "order""#
        )
    );
    let err = Query::new(order.clone())
        .annotate(vec![(
            "x".into(),
            agg(&registry, &order, AggFunc::Count, Some("number")),
        )])
        .unwrap_err()
        .to_string();
    assert!(err.contains("needs a relation path"), "{err}");
}

#[test]
fn values_and_grouped_annotations() {
    let registry = shop();
    let product = model(&registry, "Product");
    let q = Query::new(product.clone())
        .values(&registry, &["name", "category__name"], Shape::Tuples)
        .unwrap();
    assert_eq!(
        compile::select(D, &q).sql,
        r#"SELECT "product"."name", "T1"."name" FROM "product" LEFT JOIN "category" AS "T1" ON "T1"."id" = "product"."category_id""#
    );
    let total = agg(&registry, &product, AggFunc::Sum, Some("price"));
    let grouped = Query::new(product.clone())
        .values(&registry, &["category__name"], Shape::Dicts)
        .unwrap()
        .annotate(vec![("total".into(), total)])
        .unwrap();
    let index = grouped.annotation("total").unwrap();
    let field = grouped.annotations[index].output_field();
    let grouped = grouped
        .with_node(Some(Node::Leaf(
            Condition::on(
                "Product",
                &field,
                Target::Annotation(index),
                Lookup::Gte,
                Operand::Value(Value::Integer(100)),
            )
            .unwrap(),
        )))
        .with_node(Some(common::leaf(&registry, &product, "stock__gt", int(0))))
        .order_by_in(&registry, &["-total"])
        .unwrap();
    assert_eq!(
        compile::select(D, &grouped).sql,
        concat!(
            r#"SELECT "T1"."name", SUM("product"."price") FROM "product" "#,
            r#"LEFT JOIN "category" AS "T1" ON "T1"."id" = "product"."category_id" "#,
            r#"WHERE ("product"."stock" > ?) GROUP BY "T1"."name" HAVING (SUM("product"."price") >= ?) "#,
            r#"ORDER BY SUM("product"."price") DESC"#
        )
    );
    assert!(
        compile::count(D, &grouped)
            .sql
            .starts_with(r#"SELECT COUNT(*) FROM (SELECT "T1"."name""#)
    );
    let err = Query::new(product.clone())
        .values(&registry, &["name"], Shape::Flat)
        .unwrap()
        .values(&registry, &["name", "stock"], Shape::Flat)
        .unwrap_err()
        .to_string();
    assert!(err.contains("exactly one field"), "{err}");
}
