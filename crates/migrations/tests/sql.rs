use std::sync::Arc;

use ferro_database::{Statement, Value};
use ferro_migrations::{ColumnDef, Operation, SchemaState, compile, diff};
use ferro_orm::compile::{Dialect, PostgresDialect, SqliteDialect};
use ferro_orm::{FieldKind, FieldSpec, ModelMeta, ddl};

const PG: &PostgresDialect = &PostgresDialect;
const LITE: &SqliteDialect = &SqliteDialect;

fn product() -> Arc<ModelMeta> {
    let mut name = FieldSpec::new("name", FieldKind::String { max_length: 50 });
    name.index = true;
    let mut sku = FieldSpec::new("sku", FieldKind::String { max_length: 20 });
    sku.unique = true;
    Arc::new(
        ModelMeta::new(
            "app.Product",
            "Product",
            "product",
            vec![name, sku, FieldSpec::new("stock", FieldKind::Integer)],
        )
        .unwrap(),
    )
}

fn base() -> SchemaState {
    SchemaState::replay(
        &diff(&SchemaState::default(), &[product()])
            .unwrap()
            .operations,
    )
    .unwrap()
}

fn sql(dialect: &dyn Dialect, op: Operation) -> Vec<String> {
    compile(dialect, &base(), &op)
        .unwrap()
        .into_iter()
        .map(|s| s.sql)
        .collect()
}

#[test]
fn create_table_matches_create_tables_ddl() {
    for dialect in [PG as &dyn Dialect, LITE] {
        let ops = diff(&SchemaState::default(), &[product()])
            .unwrap()
            .operations;
        let mut statements: Vec<Statement> = Vec::new();
        let mut state = SchemaState::default();
        for op in &ops {
            statements.extend(compile(dialect, &state, op).unwrap());
            state.apply(op).unwrap();
        }
        assert_eq!(
            statements,
            ddl::create_table(dialect, &product()),
            "{}",
            dialect.name()
        );
    }
}

#[test]
fn postgres_alter_statements() {
    let stock = ColumnDef::new("qty", FieldKind::Integer);
    let add = compile(
        PG,
        &base(),
        &Operation::AddColumn {
            table: "product".into(),
            column: stock,
            default: Some(Value::Integer(5)),
        },
    )
    .unwrap();
    assert_eq!(
        add,
        vec![
            Statement::new(r#"ALTER TABLE "product" ADD COLUMN "qty" integer"#, vec![]),
            Statement::new(
                r#"UPDATE "product" SET "qty" = $1"#,
                vec![Value::Integer(5)]
            ),
            Statement::new(
                r#"ALTER TABLE "product" ALTER COLUMN "qty" SET NOT NULL"#,
                vec![]
            ),
        ]
    );
    let mut nullable = ColumnDef::new("note", FieldKind::Text);
    nullable.null = true;
    nullable.unique = true;
    assert_eq!(
        sql(
            PG,
            Operation::AddColumn {
                table: "product".into(),
                column: nullable,
                default: None
            }
        ),
        vec![r#"ALTER TABLE "product" ADD COLUMN "note" text UNIQUE"#]
    );
    assert_eq!(
        sql(
            PG,
            Operation::RemoveColumn {
                table: "product".into(),
                column: "stock".into(),
                default: None
            }
        ),
        vec![r#"ALTER TABLE "product" DROP COLUMN "stock""#]
    );
    let mut altered = ColumnDef::new("sku", FieldKind::String { max_length: 40 });
    altered.null = true;
    assert_eq!(
        sql(
            PG,
            Operation::AlterColumn {
                table: "product".into(),
                column: altered
            }
        ),
        vec![
            r#"ALTER TABLE "product" ALTER COLUMN "sku" TYPE varchar(40) USING "sku"::varchar(40)"#,
            r#"ALTER TABLE "product" ALTER COLUMN "sku" DROP NOT NULL"#,
            r#"ALTER TABLE "product" DROP CONSTRAINT "product_sku_key""#,
        ]
    );
    let mut unique_stock = ColumnDef::new("stock", FieldKind::Integer);
    unique_stock.unique = true;
    assert_eq!(
        sql(
            PG,
            Operation::AlterColumn {
                table: "product".into(),
                column: unique_stock
            }
        ),
        vec![r#"ALTER TABLE "product" ADD CONSTRAINT "product_stock_key" UNIQUE ("stock")"#]
    );
    assert_eq!(
        sql(
            PG,
            Operation::RenameColumn {
                table: "product".into(),
                old: "name".into(),
                new: "title".into()
            }
        ),
        vec![
            r#"ALTER TABLE "product" RENAME COLUMN "name" TO "title""#,
            r#"ALTER INDEX "product_name_idx" RENAME TO "product_title_idx""#,
        ]
    );
    assert_eq!(
        sql(
            PG,
            Operation::RenameTable {
                old: "product".into(),
                new: "item".into()
            }
        ),
        vec![
            r#"ALTER TABLE "product" RENAME TO "item""#,
            r#"ALTER INDEX "product_name_idx" RENAME TO "item_name_idx""#,
            r#"ALTER TABLE "item" RENAME CONSTRAINT "product_sku_key" TO "item_sku_key""#,
        ]
    );
    assert_eq!(
        sql(
            PG,
            Operation::DropIndex {
                table: "product".into(),
                name: "product_name_idx".into()
            }
        ),
        vec![r#"DROP INDEX "product_name_idx""#]
    );
}

#[test]
fn sqlite_rebuilds_tables() {
    let add = compile(
        LITE,
        &base(),
        &Operation::AddColumn {
            table: "product".into(),
            column: ColumnDef::new("qty", FieldKind::Integer),
            default: Some(Value::Integer(5)),
        },
    )
    .unwrap();
    let texts: Vec<&str> = add.iter().map(|s| s.sql.as_str()).collect();
    assert!(
        texts[0].starts_with(r#"CREATE TABLE "__ferro_new_product" ("#),
        "{}",
        texts[0]
    );
    assert!(texts[0].contains(r#""qty" integer NOT NULL"#));
    assert_eq!(
        texts[1],
        r#"INSERT INTO "__ferro_new_product" ("id", "name", "sku", "stock", "qty") SELECT "id", "name", "sku", "stock", ? FROM "product""#
    );
    assert_eq!(add[1].params, vec![Value::Integer(5)]);
    // AUTOINCREMENT sequence carried over so ids are never reused after a rebuild.
    assert_eq!(texts[2], "DELETE FROM sqlite_sequence WHERE name = ?");
    assert_eq!(
        texts[3],
        "INSERT INTO sqlite_sequence (name, seq) SELECT ?, seq FROM sqlite_sequence WHERE name = ?"
    );
    assert_eq!(
        add[3].params,
        vec![
            Value::Text("__ferro_new_product".into()),
            Value::Text("product".into())
        ]
    );
    assert_eq!(texts[4], r#"DROP TABLE "product""#);
    assert_eq!(
        texts[5],
        r#"ALTER TABLE "__ferro_new_product" RENAME TO "product""#
    );
    assert_eq!(
        texts[6],
        r#"CREATE INDEX "product_name_idx" ON "product" ("name")"#
    );
    assert_eq!(texts.len(), 7);

    let mut nullable = ColumnDef::new("note", FieldKind::Text);
    nullable.null = true;
    assert_eq!(
        sql(
            LITE,
            Operation::AddColumn {
                table: "product".into(),
                column: nullable,
                default: None
            }
        ),
        vec![r#"ALTER TABLE "product" ADD COLUMN "note" text"#]
    );

    let removed = sql(
        LITE,
        Operation::RemoveColumn {
            table: "product".into(),
            column: "stock".into(),
            default: None,
        },
    );
    assert_eq!(
        removed[1],
        r#"INSERT INTO "__ferro_new_product" ("id", "name", "sku") SELECT "id", "name", "sku" FROM "product""#
    );
    let renamed = sql(
        LITE,
        Operation::RenameColumn {
            table: "product".into(),
            old: "name".into(),
            new: "title".into(),
        },
    );
    assert_eq!(
        renamed,
        vec![
            r#"ALTER TABLE "product" RENAME COLUMN "name" TO "title""#,
            r#"DROP INDEX "product_name_idx""#,
            r#"CREATE INDEX "product_title_idx" ON "product" ("title")"#,
        ]
    );
}

#[test]
fn identifiers_are_quoted_and_values_bound() {
    let op = Operation::AddColumn {
        table: "product".into(),
        column: ColumnDef::new("x", FieldKind::Text),
        default: Some(Value::Text("'); DROP TABLE product; --".into())),
    };
    for dialect in [PG as &dyn Dialect, LITE] {
        for statement in compile(dialect, &base(), &op).unwrap() {
            assert!(
                !statement.sql.contains("DROP TABLE product;"),
                "{}",
                statement.sql
            );
        }
    }
}
