use std::sync::Arc;

use ferro_migrations::{ColumnDef, Operation, SchemaState, diff};
use ferro_orm::{FieldKind, FieldSpec, ModelMeta};

fn model(table: &str, fields: Vec<FieldSpec>) -> Arc<ModelMeta> {
    Arc::new(ModelMeta::new(&format!("app.{table}"), table, table, fields).unwrap())
}

fn string(name: &str, max_length: u32) -> FieldSpec {
    FieldSpec::new(name, FieldKind::String { max_length })
}

fn product_v1() -> Arc<ModelMeta> {
    let mut name = string("name", 50);
    name.index = true;
    model(
        "product",
        vec![name, FieldSpec::new("stock", FieldKind::Integer)],
    )
}

fn applied(models: &[Arc<ModelMeta>]) -> SchemaState {
    let first = diff(&SchemaState::default(), models).unwrap();
    SchemaState::replay(&first.operations).unwrap()
}

#[test]
fn initial_diff_creates_tables_and_indexes() {
    let result = diff(&SchemaState::default(), &[product_v1()]).unwrap();
    assert_eq!(result.operations.len(), 2);
    let Operation::CreateTable { table, columns } = &result.operations[0] else {
        panic!("{:?}", result.operations)
    };
    assert_eq!(table, "product");
    let names: Vec<&str> = columns.iter().map(|c| c.name.as_str()).collect();
    assert_eq!(names, vec!["id", "name", "stock"]);
    assert!(columns[0].auto && columns[0].primary_key);
    assert_eq!(
        result.operations[1],
        Operation::CreateIndex {
            table: "product".into(),
            columns: vec!["name".into()],
            name: "product_name_idx".into(),
            unique: false
        }
    );
    assert!(result.warnings.is_empty());
}

#[test]
fn replaying_the_diff_gives_no_further_changes() {
    let state = applied(&[product_v1()]);
    let again = diff(&state, &[product_v1()]).unwrap();
    assert!(again.operations.is_empty(), "{:?}", again.operations);
}

#[test]
fn column_changes_in_order() {
    let state = applied(&[product_v1()]);
    let mut price = FieldSpec::new(
        "price",
        FieldKind::Decimal {
            max_digits: 10,
            decimal_places: 2,
        },
    );
    price.null = true;
    let mut name = string("name", 120); // altered (length) and index removed
    name.unique = false;
    let v2 = model("product", vec![name, price]);
    let result = diff(&state, &[v2]).unwrap();
    let kinds: Vec<&str> = result
        .operations
        .iter()
        .map(|op| match op {
            Operation::DropIndex { .. } => "drop_index",
            Operation::RemoveColumn { .. } => "remove_column",
            Operation::AddColumn { .. } => "add_column",
            Operation::AlterColumn { .. } => "alter_column",
            other => panic!("{other:?}"),
        })
        .collect();
    assert_eq!(
        kinds,
        vec!["drop_index", "remove_column", "add_column", "alter_column"]
    );
    let Operation::AlterColumn { column, .. } = &result.operations[3] else {
        panic!()
    };
    assert_eq!(
        column,
        &ColumnDef::new("name", FieldKind::String { max_length: 120 })
    );
}

#[test]
fn new_and_removed_models() {
    let state = applied(&[product_v1()]);
    let category = model("category", vec![string("title", 30)]);
    let result = diff(&state, &[category]).unwrap();
    assert!(
        matches!(result.operations[0], Operation::CreateTable { ref table, .. } if table == "category")
    );
    assert!(
        matches!(result.operations.last().unwrap(), Operation::DropTable { table } if table == "product")
    );
    assert!(
        result
            .warnings
            .iter()
            .any(|w| w.contains("table 'product' will be dropped")),
        "{:?}",
        result.warnings
    );
}

#[test]
fn possible_rename_is_warned() {
    let state = applied(&[product_v1()]);
    let mut title = string("title", 50);
    title.index = true;
    let renamed = model(
        "product",
        vec![title, FieldSpec::new("stock", FieldKind::Integer)],
    );
    let result = diff(&state, &[renamed]).unwrap();
    assert!(
        result
            .warnings
            .iter()
            .any(|w| w.contains("'name'") && w.contains("'title'") && w.contains("RenameColumn")),
        "{:?}",
        result.warnings
    );
}

#[test]
fn unique_and_null_changes_alter_columns() {
    let state = applied(&[product_v1()]);
    let mut name = string("name", 50);
    name.unique = true;
    let mut stock = FieldSpec::new("stock", FieldKind::Integer);
    stock.null = true;
    let result = diff(&state, &[model("product", vec![name, stock])]).unwrap();
    let altered: Vec<&str> = result
        .operations
        .iter()
        .filter_map(|op| match op {
            Operation::AlterColumn { column, .. } => Some(column.name.as_str()),
            _ => None,
        })
        .collect();
    assert_eq!(altered, vec!["name", "stock"]);
    assert!(
        result
            .operations
            .iter()
            .any(|op| matches!(op, Operation::DropIndex { .. })),
        "a unique column needs no separate index"
    );
}

#[test]
fn primary_key_changes_are_refused() {
    let state = applied(&[product_v1()]);
    let mut code = FieldSpec::new("code", FieldKind::Uuid);
    code.primary_key = true;
    let err = diff(&state, &[model("product", vec![code, string("name", 50)])])
        .unwrap_err()
        .to_string();
    assert!(err.contains("primary key"), "{err}");
}
