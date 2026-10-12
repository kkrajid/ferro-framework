//! Schema DDL shared by `create-tables` (FR-022) and the migration engine (Feature 004), so both
//! produce identical schemas.

use std::sync::Arc;

use ferro_database::Statement;

use crate::compile::Dialect;
use crate::error::OrmError;
use crate::field::FieldMeta;
use crate::meta::ModelMeta;

/// One column definition, e.g. `"sku" varchar(40) NOT NULL UNIQUE` or
/// `"category_id" bigint NOT NULL REFERENCES "category" ("id") ON DELETE CASCADE`.
pub fn column_sql(dialect: &dyn Dialect, field: &FieldMeta) -> String {
    let column = dialect.quote(&field.column);
    if field.auto {
        return format!("{column} {}", dialect.auto_primary_key());
    }
    let mut definition = format!("{column} {}", dialect.column_type(&field.kind));
    if !field.null {
        definition.push_str(" NOT NULL");
    }
    if field.primary_key {
        definition.push_str(" PRIMARY KEY");
    } else if field.unique {
        definition.push_str(" UNIQUE");
    }
    if let Some(reference) = &field.references {
        definition.push_str(&format!(
            " REFERENCES {} ({}) ON DELETE {}",
            dialect.quote(&reference.table),
            dialect.quote(&reference.column),
            reference.on_delete.sql()
        ));
    }
    definition
}

pub fn create_table_sql(dialect: &dyn Dialect, table: &str, fields: &[FieldMeta]) -> String {
    let columns: Vec<String> = fields.iter().map(|f| column_sql(dialect, f)).collect();
    format!(
        "CREATE TABLE {} (\n  {}\n)",
        dialect.quote(table),
        columns.join(",\n  ")
    )
}

/// Ferro's index naming: `<table>_<column>_idx`.
pub fn index_name(table: &str, column: &str) -> String {
    format!("{table}_{column}_idx")
}

pub fn create_index_sql(dialect: &dyn Dialect, table: &str, name: &str, column: &str) -> String {
    create_composite_index_sql(dialect, table, name, &[column.to_string()], false)
}

/// `CREATE [UNIQUE] INDEX "name" ON "table" ("a", "b")`.
pub fn create_composite_index_sql(
    dialect: &dyn Dialect,
    table: &str,
    name: &str,
    columns: &[String],
    unique: bool,
) -> String {
    let columns: Vec<String> = columns.iter().map(|c| dialect.quote(c)).collect();
    format!(
        "CREATE {}INDEX {} ON {} ({})",
        if unique { "UNIQUE " } else { "" },
        dialect.quote(name),
        dialect.quote(table),
        columns.join(", ")
    )
}

/// `CREATE TABLE` followed by one `CREATE INDEX` per indexed (non-unique) field and one per
/// composite index (`unique_together`).
pub fn create_table(dialect: &dyn Dialect, meta: &ModelMeta) -> Vec<Statement> {
    let mut statements = vec![Statement::new(
        create_table_sql(dialect, &meta.table, &meta.fields),
        vec![],
    )];
    for field in meta.fields.iter().filter(|f| f.index && !f.unique) {
        statements.push(Statement::new(
            create_index_sql(
                dialect,
                &meta.table,
                &index_name(&meta.table, &field.column),
                &field.column,
            ),
            vec![],
        ));
    }
    for index in &meta.indexes {
        statements.push(Statement::new(
            create_composite_index_sql(
                dialect,
                &meta.table,
                &index.name,
                &index.columns,
                index.unique,
            ),
            vec![],
        ));
    }
    statements
}

/// Orders models so every table comes after the tables it references (self references are
/// fine). Ties keep the given order. Circular references between different tables are refused.
pub fn creation_order(metas: &[Arc<ModelMeta>]) -> Result<Vec<Arc<ModelMeta>>, OrmError> {
    let tables: Vec<&str> = metas.iter().map(|m| m.table.as_str()).collect();
    let mut placed: Vec<Arc<ModelMeta>> = Vec::with_capacity(metas.len());
    let mut remaining: Vec<&Arc<ModelMeta>> = metas.iter().collect();
    while !remaining.is_empty() {
        let ready = remaining.iter().position(|meta| {
            meta.fields
                .iter()
                .filter_map(|f| f.references.as_ref())
                .all(|r| {
                    r.table == meta.table
                        || !tables.contains(&r.table.as_str())
                        || placed.iter().any(|p| p.table == r.table)
                })
        });
        match ready {
            Some(index) => placed.push(remaining.remove(index).clone()),
            None => {
                let names: Vec<&str> = remaining.iter().map(|m| m.table.as_str()).collect();
                return Err(OrmError(format!(
                    "circular foreign keys between tables {}; make one of the keys null=True \
                     and add it in a separate step",
                    names.join(", ")
                )));
            }
        }
    }
    Ok(placed)
}
