use std::sync::Arc;

use ferro_migrations::{ColumnDef, Operation, SchemaState, compile, diff};
use ferro_orm::compile::{PostgresDialect, SqliteDialect};
use ferro_orm::{FieldKind, FieldSpec, ForeignKeyRef, ModelMeta, OnDelete, Registry};

fn register(
    registry: &mut Registry,
    label: &str,
    fields: Vec<FieldSpec>,
    unique_together: &[&[&str]],
) -> Arc<ModelMeta> {
    let groups: Vec<Vec<String>> = unique_together
        .iter()
        .map(|g| g.iter().map(|s| s.to_string()).collect())
        .collect();
    let table = ferro_orm::snake_case(label);
    let meta = {
        let lookup = |q: &str| registry.get(q);
        ModelMeta::build(
            &format!("app.{label}"),
            label,
            &table,
            fields,
            &groups,
            &lookup,
        )
        .unwrap()
    };
    registry.register(meta).unwrap()
}

fn shop() -> (Registry, Vec<Arc<ModelMeta>>) {
    let mut registry = Registry::default();
    let category = register(
        &mut registry,
        "Category",
        vec![FieldSpec::new("name", FieldKind::String { max_length: 50 })],
        &[],
    );
    let product = register(
        &mut registry,
        "Product",
        vec![
            FieldSpec::new("name", FieldKind::String { max_length: 50 }),
            FieldSpec::relation("category", "app.Category", OnDelete::Cascade, false),
        ],
        &[],
    );
    let tag = register(
        &mut registry,
        "Tag",
        vec![FieldSpec::new("name", FieldKind::String { max_length: 50 })],
        &[],
    );
    let mut link_product = FieldSpec::relation("product", "app.Product", OnDelete::Cascade, false);
    let mut link_tag = FieldSpec::relation("tag", "app.Tag", OnDelete::Cascade, false);
    for spec in [&mut link_product, &mut link_tag] {
        spec.relation.as_mut().unwrap().related_name = Some("+".into());
    }
    let link = register(
        &mut registry,
        "ProductTags",
        vec![link_product, link_tag],
        &[&["product", "tag"]],
    );
    // Deliberately listed referencing-first: the diff must reorder.
    (registry, vec![link, product, tag, category])
}

fn table_order(ops: &[Operation]) -> Vec<&str> {
    ops.iter()
        .filter_map(|op| match op {
            Operation::CreateTable { table, .. } => Some(table.as_str()),
            _ => None,
        })
        .collect()
}

#[test]
fn new_tables_are_created_in_dependency_order_with_constraints() {
    let (_, models) = shop();
    let result = diff(&SchemaState::default(), &models).unwrap();
    // Tables without references keep their place; the others wait for their targets.
    assert_eq!(
        table_order(&result.operations),
        vec!["tag", "category", "product", "product_tags"]
    );
    let columns = result
        .operations
        .iter()
        .find_map(|op| match op {
            Operation::CreateTable { table, columns } if table == "product" => Some(columns),
            _ => None,
        })
        .unwrap();
    let category = columns.iter().find(|c| c.name == "category_id").unwrap();
    assert_eq!(
        category.references,
        Some(ForeignKeyRef {
            table: "category".into(),
            column: "id".into(),
            on_delete: OnDelete::Cascade,
        })
    );
    assert!(result.operations.contains(&Operation::CreateIndex {
        table: "product_tags".into(),
        columns: vec!["product_id".into(), "tag_id".into()],
        name: "product_tags_product_id_tag_id_uniq".into(),
        unique: true,
    }));
    let state = SchemaState::replay(&result.operations).unwrap();
    assert!(
        diff(&state, &models).unwrap().operations.is_empty(),
        "round trip"
    );
}

#[test]
fn circular_foreign_keys_between_new_tables_are_refused() {
    let mut registry = Registry::default();
    register(&mut registry, "A", vec![], &[]);
    let mut b_to_a = FieldSpec::relation("a", "app.A", OnDelete::Cascade, false);
    b_to_a.relation.as_mut().unwrap().related_name = Some("+".into());
    let b = register(&mut registry, "B", vec![b_to_a], &[]);
    let mut a_to_b = FieldSpec::relation("b", "app.B", OnDelete::Cascade, false);
    a_to_b.relation.as_mut().unwrap().related_name = Some("+".into());
    let a = register(&mut registry, "A", vec![a_to_b], &[]);
    let err = diff(&SchemaState::default(), &[a, b])
        .unwrap_err()
        .to_string();
    assert!(err.contains("circular foreign keys"), "{err}");
}

#[test]
fn dropped_tables_go_referencing_first() {
    let (_, models) = shop();
    let state =
        SchemaState::replay(&diff(&SchemaState::default(), &models).unwrap().operations).unwrap();
    let result = diff(&state, &[]).unwrap();
    let dropped: Vec<&str> = result
        .operations
        .iter()
        .filter_map(|op| match op {
            Operation::DropTable { table } => Some(table.as_str()),
            _ => None,
        })
        .collect();
    let position = |t: &str| dropped.iter().position(|d| *d == t).unwrap();
    assert!(position("product_tags") < position("product"));
    assert!(position("product") < position("category"));
    assert!(position("product_tags") < position("tag"));
}

fn state_with_product() -> SchemaState {
    let (_, models) = shop();
    SchemaState::replay(&diff(&SchemaState::default(), &models).unwrap().operations).unwrap()
}

fn category_column(on_delete: OnDelete, null: bool) -> ColumnDef {
    let mut column = ColumnDef::new("category_id", FieldKind::BigInteger);
    column.null = null;
    column.references = Some(ForeignKeyRef {
        table: "category".into(),
        column: "id".into(),
        on_delete,
    });
    column
}

#[test]
fn postgres_alters_foreign_key_constraints_in_place() {
    let state = state_with_product();
    let change = Operation::AlterColumn {
        table: "product".into(),
        column: category_column(OnDelete::SetNull, true),
    };
    let statements: Vec<String> = compile(&PostgresDialect, &state, &change)
        .unwrap()
        .into_iter()
        .map(|s| s.sql)
        .collect();
    assert_eq!(
        statements,
        vec![
            r#"ALTER TABLE "product" DROP CONSTRAINT "product_category_id_fkey""#,
            r#"ALTER TABLE "product" ALTER COLUMN "category_id" DROP NOT NULL"#,
            r#"ALTER TABLE "product" ADD CONSTRAINT "product_category_id_fkey" FOREIGN KEY ("category_id") REFERENCES "category" ("id") ON DELETE SET NULL"#,
        ]
    );
    let rename = Operation::RenameTable {
        old: "category".into(),
        new: "section".into(),
    };
    let after = {
        let mut s = state.clone();
        s.apply(&rename).unwrap();
        s
    };
    assert_eq!(
        after
            .table("product")
            .unwrap()
            .column("category_id")
            .unwrap()
            .references
            .as_ref()
            .unwrap()
            .table,
        "section",
        "references follow table renames"
    );
    let rename_column = Operation::RenameColumn {
        table: "product".into(),
        old: "category_id".into(),
        new: "section_id".into(),
    };
    let statements: Vec<String> = compile(&PostgresDialect, &state, &rename_column)
        .unwrap()
        .into_iter()
        .map(|s| s.sql)
        .collect();
    assert_eq!(
        statements,
        vec![
            r#"ALTER TABLE "product" RENAME COLUMN "category_id" TO "section_id""#,
            r#"ALTER INDEX "product_category_id_idx" RENAME TO "product_section_id_idx""#,
            r#"ALTER TABLE "product" RENAME CONSTRAINT "product_category_id_fkey" TO "product_section_id_fkey""#,
        ]
    );
}

#[test]
fn sqlite_rebuilds_keep_foreign_keys_and_composite_indexes() {
    let state = state_with_product();
    let change = Operation::AlterColumn {
        table: "product".into(),
        column: category_column(OnDelete::Protect, false),
    };
    let statements: Vec<String> = compile(&SqliteDialect, &state, &change)
        .unwrap()
        .into_iter()
        .map(|s| s.sql)
        .collect();
    assert!(
        statements[0].contains(
            r#""category_id" bigint NOT NULL REFERENCES "category" ("id") ON DELETE RESTRICT"#
        ),
        "{statements:?}"
    );
    assert!(
        statements
            .iter()
            .any(|s| s == r#"CREATE INDEX "product_category_id_idx" ON "product" ("category_id")"#)
    );
    let link_change = Operation::AddColumn {
        table: "product_tags".into(),
        column: ColumnDef::new("weight", FieldKind::Integer),
        default: Some(ferro_database::Value::Integer(1)),
    };
    let statements: Vec<String> = compile(&SqliteDialect, &state, &link_change)
        .unwrap()
        .into_iter()
        .map(|s| s.sql)
        .collect();
    assert!(statements.iter().any(|s| s
        == r#"CREATE UNIQUE INDEX "product_tags_product_id_tag_id_uniq" ON "product_tags" ("product_id", "tag_id")"#));
}
