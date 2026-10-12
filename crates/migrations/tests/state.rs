use ferro_database::Value;
use ferro_migrations::{ColumnDef, IndexDef, Operation, SchemaState, reverse, reverse_all};
use ferro_orm::FieldKind;

fn auto_id() -> ColumnDef {
    let mut id = ColumnDef::new("id", FieldKind::BigInteger);
    id.primary_key = true;
    id.unique = true;
    id.auto = true;
    id
}

fn create_product() -> Operation {
    Operation::CreateTable {
        table: "product".into(),
        columns: vec![
            auto_id(),
            ColumnDef::new("name", FieldKind::String { max_length: 50 }),
        ],
    }
}

fn op_add_stock(default: Option<Value>) -> Operation {
    Operation::AddColumn {
        table: "product".into(),
        column: ColumnDef::new("stock", FieldKind::Integer),
        default,
    }
}

#[test]
fn replay_builds_tables_columns_and_indexes() {
    let state = SchemaState::replay(&[
        create_product(),
        op_add_stock(Some(Value::Integer(0))),
        Operation::CreateIndex {
            table: "product".into(),
            columns: vec!["name".into()],
            name: "product_name_idx".into(),
            unique: false,
        },
        Operation::RenameColumn {
            table: "product".into(),
            old: "stock".into(),
            new: "quantity".into(),
        },
    ])
    .unwrap();
    let table = state.table("product").unwrap();
    let names: Vec<&str> = table.columns.iter().map(|c| c.name.as_str()).collect();
    assert_eq!(names, vec!["id", "name", "quantity"]);
    assert_eq!(
        table.indexes,
        vec![IndexDef::single("product_name_idx", "name")]
    );
}

#[test]
fn rename_and_drop_tables() {
    let state = SchemaState::replay(&[
        create_product(),
        Operation::RenameTable {
            old: "product".into(),
            new: "products".into(),
        },
    ])
    .unwrap();
    assert!(state.table("product").is_none());
    assert!(state.table("products").is_some());
    let state = SchemaState::replay(&[
        create_product(),
        Operation::DropTable {
            table: "product".into(),
        },
    ])
    .unwrap();
    assert!(state.tables.is_empty());
}

#[test]
fn invalid_operations_are_rejected() {
    let cases = vec![
        (
            vec![Operation::DropTable {
                table: "nope".into(),
            }],
            "table 'nope' does not exist",
        ),
        (
            vec![create_product(), create_product()],
            "table 'product' already exists",
        ),
        (
            vec![create_product(), op_add_stock(None), op_add_stock(None)],
            "column 'stock' already exists in table 'product'",
        ),
        (
            vec![
                create_product(),
                Operation::RemoveColumn {
                    table: "product".into(),
                    column: "nope".into(),
                    default: None,
                },
            ],
            "column 'nope' does not exist in table 'product'",
        ),
        (
            vec![
                create_product(),
                Operation::DropIndex {
                    table: "product".into(),
                    name: "x_idx".into(),
                },
            ],
            "index 'x_idx' does not exist",
        ),
    ];
    for (ops, expected) in cases {
        let err = SchemaState::replay(&ops).unwrap_err().to_string();
        assert!(err.contains(expected), "{err}");
    }
}

#[test]
fn reverse_of_each_operation() {
    let before = SchemaState::replay(&[
        create_product(),
        Operation::CreateIndex {
            table: "product".into(),
            columns: vec!["name".into()],
            name: "product_name_idx".into(),
            unique: false,
        },
    ])
    .unwrap();
    assert_eq!(
        reverse(&op_add_stock(None), &before).unwrap(),
        vec![Operation::RemoveColumn {
            table: "product".into(),
            column: "stock".into(),
            default: None
        }]
    );
    let dropped = reverse(
        &Operation::DropTable {
            table: "product".into(),
        },
        &before,
    )
    .unwrap();
    assert_eq!(dropped[0], {
        let Operation::CreateTable { .. } = &dropped[0] else {
            panic!()
        };
        dropped[0].clone()
    });
    assert_eq!(
        dropped[1],
        Operation::CreateIndex {
            table: "product".into(),
            columns: vec!["name".into()],
            name: "product_name_idx".into(),
            unique: false
        }
    );
    let altered = ColumnDef::new("name", FieldKind::Text);
    assert_eq!(
        reverse(
            &Operation::AlterColumn {
                table: "product".into(),
                column: altered
            },
            &before
        )
        .unwrap(),
        vec![Operation::AlterColumn {
            table: "product".into(),
            column: ColumnDef::new("name", FieldKind::String { max_length: 50 })
        }]
    );
    assert_eq!(
        reverse(
            &Operation::RenameColumn {
                table: "product".into(),
                old: "name".into(),
                new: "title".into()
            },
            &before
        )
        .unwrap(),
        vec![Operation::RenameColumn {
            table: "product".into(),
            old: "title".into(),
            new: "name".into()
        }]
    );
    assert_eq!(
        reverse(
            &Operation::DropIndex {
                table: "product".into(),
                name: "product_name_idx".into()
            },
            &before
        )
        .unwrap(),
        vec![Operation::CreateIndex {
            table: "product".into(),
            columns: vec!["name".into()],
            name: "product_name_idx".into(),
            unique: false
        }]
    );
}

#[test]
fn removing_a_non_null_column_without_default_is_irreversible() {
    let base = SchemaState::replay(&[create_product()]).unwrap();
    let remove = Operation::RemoveColumn {
        table: "product".into(),
        column: "name".into(),
        default: None,
    };
    let err = reverse(&remove, &base).unwrap_err().to_string();
    assert!(err.contains("cannot be reversed"), "{err}");
    let with_default = Operation::RemoveColumn {
        table: "product".into(),
        column: "name".into(),
        default: Some(Value::Text(String::new())),
    };
    assert_eq!(
        reverse(&with_default, &base).unwrap(),
        vec![Operation::AddColumn {
            table: "product".into(),
            column: ColumnDef::new("name", FieldKind::String { max_length: 50 }),
            default: Some(Value::Text(String::new()))
        }]
    );
}

#[test]
fn reverse_all_runs_backwards() {
    let base = SchemaState::default();
    let ops = vec![create_product(), op_add_stock(Some(Value::Integer(0)))];
    let reversed = reverse_all(&base, &ops).unwrap();
    assert!(matches!(reversed[0], Operation::RemoveColumn { .. }));
    assert!(matches!(reversed[1], Operation::DropTable { .. }));
}
