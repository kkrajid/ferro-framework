//! Operations → SQL per dialect (research R4). PostgreSQL alters in place; SQLite alters in place
//! when it can and otherwise rebuilds the table. Values (back-fills) are always bound.

use ferro_database::{Statement, Value};
use ferro_orm::compile::Dialect;
use ferro_orm::ddl::{
    column_sql, create_composite_index_sql, create_table_sql, is_text, pattern_index_name,
    pattern_index_sql,
};

use crate::ops::{ColumnDef, Operation};
use crate::state::{IndexDef, SchemaState, TableState};
use crate::{MigrationError, error};

fn plain(sql: String) -> Statement {
    Statement::new(sql, vec![])
}

fn is_postgres(dialect: &dyn Dialect) -> bool {
    dialect.name() == "postgresql"
}

fn create_table(dialect: &dyn Dialect, table: &str, columns: &[ColumnDef]) -> String {
    let fields: Vec<_> = columns.iter().map(ColumnDef::to_field).collect();
    create_table_sql(dialect, table, &fields)
}

/// Where each column of a rebuilt table gets its values.
enum Source {
    Column(String),
    Value(Value),
}

fn rebuild(
    dialect: &dyn Dialect,
    table: &str,
    after: &TableState,
    sources: Vec<Source>,
) -> Vec<Statement> {
    let temporary = format!("__ferro_new_{table}");
    let q = |name: &str| dialect.quote(name);
    let mut statements = vec![plain(create_table(dialect, &temporary, &after.columns))];
    let columns: Vec<String> = after.columns.iter().map(|c| q(&c.name)).collect();
    let mut params = Vec::new();
    let selected: Vec<String> = sources
        .into_iter()
        .map(|source| match source {
            Source::Column(name) => q(&name),
            Source::Value(Value::Null) => "NULL".to_string(),
            Source::Value(value) => {
                params.push(value);
                dialect.placeholder(params.len())
            }
        })
        .collect();
    statements.push(Statement::new(
        format!(
            "INSERT INTO {} ({}) SELECT {} FROM {}",
            q(&temporary),
            columns.join(", "),
            selected.join(", "),
            q(table)
        ),
        params,
    ));
    if after.columns.iter().any(|c| c.auto) {
        // Copying explicit ids would reset AUTOINCREMENT to the highest remaining id; keep the
        // old table's counter so deleted ids are never reused.
        statements.push(Statement::new(
            "DELETE FROM sqlite_sequence WHERE name = ?",
            vec![Value::Text(temporary.clone())],
        ));
        statements.push(Statement::new(
            "INSERT INTO sqlite_sequence (name, seq) SELECT ?, seq FROM sqlite_sequence WHERE name = ?",
            vec![Value::Text(temporary.clone()), Value::Text(table.to_string())],
        ));
    }
    statements.push(plain(format!("DROP TABLE {}", q(table))));
    statements.push(plain(format!(
        "ALTER TABLE {} RENAME TO {}",
        q(&temporary),
        q(table)
    )));
    for index in &after.indexes {
        statements.push(plain(index_sql(dialect, table, index)));
    }
    statements
}

fn index_sql(dialect: &dyn Dialect, table: &str, index: &IndexDef) -> String {
    create_composite_index_sql(dialect, table, &index.name, &index.columns, index.unique)
}

/// The column of a single-column, non-unique index: the kind that gets a PostgreSQL pattern
/// index next to it.
fn single_column(columns: &[String], unique: bool) -> Option<&str> {
    match columns {
        [column] if !unique => Some(column),
        _ => None,
    }
}

/// `CREATE INDEX ... text_pattern_ops` for `index` when its column holds text (PostgreSQL).
fn pattern_index(
    dialect: &dyn Dialect,
    state: &TableState,
    table: &str,
    name: &str,
    columns: &[String],
    unique: bool,
) -> Option<Statement> {
    let column = single_column(columns, unique)?;
    let kind = &state.column(column)?.kind;
    pattern_index_sql(dialect, table, name, column, kind).map(plain)
}

fn drop_pattern_index(dialect: &dyn Dialect, name: &str) -> Statement {
    plain(format!(
        "DROP INDEX IF EXISTS {}",
        dialect.quote(&pattern_index_name(name))
    ))
}

/// Indexes whose names changed between two states of a table (renames keep Ferro's naming).
fn renamed_indexes(
    dialect: &dyn Dialect,
    table: &str,
    before: &TableState,
    after: &TableState,
) -> Vec<Statement> {
    let postgres = is_postgres(dialect);
    let mut statements = Vec::new();
    for (old, new) in before.indexes.iter().zip(&after.indexes) {
        if old.name == new.name {
            continue;
        }
        if postgres {
            statements.push(plain(format!(
                "ALTER INDEX {} RENAME TO {}",
                dialect.quote(&old.name),
                dialect.quote(&new.name)
            )));
            // Text indexes have a pattern index (none when created before Feature 021).
            let text = single_column(&old.columns, old.unique)
                .and_then(|column| before.column(column))
                .is_some_and(|column| is_text(&column.kind));
            if text {
                statements.push(plain(format!(
                    "ALTER INDEX IF EXISTS {} RENAME TO {}",
                    dialect.quote(&pattern_index_name(&old.name)),
                    dialect.quote(&pattern_index_name(&new.name))
                )));
            }
        } else {
            statements.push(plain(format!("DROP INDEX {}", dialect.quote(&old.name))));
            statements.push(plain(index_sql(dialect, table, new)));
        }
    }
    statements
}

/// PostgreSQL names column constraints after the table and column (`<t>_<c>_key`,
/// `<t>_<c>_fkey`) and does not rename them; keep them in step so later operations find them.
fn rename_constraints(
    dialect: &dyn Dialect,
    table: &str,
    old: (&str, &str),
    new: (&str, &str),
    column: &ColumnDef,
) -> Vec<Statement> {
    let mut statements = Vec::new();
    let mut rename = |suffix: &str| {
        statements.push(plain(format!(
            "ALTER TABLE {} RENAME CONSTRAINT {} TO {}",
            dialect.quote(table),
            dialect.quote(&format!("{}_{}_{suffix}", old.0, old.1)),
            dialect.quote(&format!("{}_{}_{suffix}", new.0, new.1))
        )));
    };
    if column.unique && !column.primary_key {
        rename("key");
    }
    if column.references.is_some() {
        rename("fkey");
    }
    statements
}

fn foreign_key_name(table: &str, column: &str) -> String {
    format!("{table}_{column}_fkey")
}

/// SQL for one operation applied to `before`.
pub fn compile(
    dialect: &dyn Dialect,
    before: &SchemaState,
    operation: &Operation,
) -> Result<Vec<Statement>, MigrationError> {
    let mut after = before.clone();
    after.apply(operation)?;
    let q = |name: &str| dialect.quote(name);
    let postgres = is_postgres(dialect);
    Ok(match operation {
        Operation::CreateTable { table, columns } => {
            vec![plain(create_table(dialect, table, columns))]
        }
        Operation::DropTable { table } => vec![plain(format!("DROP TABLE {}", q(table)))],
        Operation::RenameTable { old, new } => {
            let mut statements = vec![plain(format!(
                "ALTER TABLE {} RENAME TO {}",
                q(old),
                q(new)
            ))];
            let before_table = before.existing(old)?;
            statements.extend(renamed_indexes(
                dialect,
                new,
                before_table,
                after.existing(new)?,
            ));
            if postgres {
                for column in &before_table.columns {
                    statements.extend(rename_constraints(
                        dialect,
                        new,
                        (old, &column.name),
                        (new, &column.name),
                        column,
                    ));
                }
            }
            statements
        }
        Operation::CreateIndex {
            table,
            columns,
            name,
            unique,
        } => {
            let mut statements = vec![plain(create_composite_index_sql(
                dialect, table, name, columns, *unique,
            ))];
            statements.extend(pattern_index(
                dialect,
                before.existing(table)?,
                table,
                name,
                columns,
                *unique,
            ));
            statements
        }
        Operation::DropIndex { name, .. } => {
            let mut statements = Vec::new();
            if postgres {
                statements.push(drop_pattern_index(dialect, name));
            }
            statements.push(plain(format!("DROP INDEX {}", q(name))));
            statements
        }
        Operation::AddColumn {
            table,
            column,
            default,
        } => {
            let backfill = !column.null && default.is_some();
            if postgres {
                let mut definition = column.to_field();
                definition.null = definition.null || backfill;
                let mut statements = vec![plain(format!(
                    "ALTER TABLE {} ADD COLUMN {}",
                    q(table),
                    column_sql(dialect, &definition)
                ))];
                if let (true, Some(value)) = (backfill, default) {
                    statements.push(Statement::new(
                        format!(
                            "UPDATE {} SET {} = {}",
                            q(table),
                            q(&column.name),
                            dialect.placeholder(1)
                        ),
                        vec![value.clone()],
                    ));
                    statements.push(plain(format!(
                        "ALTER TABLE {} ALTER COLUMN {} SET NOT NULL",
                        q(table),
                        q(&column.name)
                    )));
                }
                statements
            } else if column.null && !column.unique && !column.primary_key {
                vec![plain(format!(
                    "ALTER TABLE {} ADD COLUMN {}",
                    q(table),
                    column_sql(dialect, &column.to_field())
                ))]
            } else {
                let current = before.existing(table)?;
                let mut sources: Vec<Source> = current
                    .columns
                    .iter()
                    .map(|c| Source::Column(c.name.clone()))
                    .collect();
                sources.push(Source::Value(default.clone().unwrap_or(Value::Null)));
                rebuild(dialect, table, after.existing(table)?, sources)
            }
        }
        Operation::RemoveColumn { table, column, .. } => {
            if postgres {
                vec![plain(format!(
                    "ALTER TABLE {} DROP COLUMN {}",
                    q(table),
                    q(column)
                ))]
            } else {
                let target = after.existing(table)?;
                let sources = target
                    .columns
                    .iter()
                    .map(|c| Source::Column(c.name.clone()))
                    .collect();
                rebuild(dialect, table, target, sources)
            }
        }
        Operation::AlterColumn { table, column } => {
            let old = before
                .existing(table)?
                .column(&column.name)
                .ok_or_else(|| {
                    error(format!(
                        "column '{}' does not exist in table '{table}'",
                        column.name
                    ))
                })?
                .clone();
            if old.primary_key != column.primary_key || old.auto != column.auto {
                return Err(error(format!(
                    "changing the primary key of table '{table}' is not supported by migrations yet"
                )));
            }
            if postgres {
                let mut statements = Vec::new();
                let name = q(&column.name);
                let fkey = q(&foreign_key_name(table, &column.name));
                let references_changed = old.references != column.references;
                if references_changed && old.references.is_some() {
                    statements.push(plain(format!(
                        "ALTER TABLE {} DROP CONSTRAINT {fkey}",
                        q(table)
                    )));
                }
                // A pattern index only fits text: drop it before the type leaves text, create
                // it once the type becomes text (varchar <-> text keeps it).
                let pattern_change = is_text(&old.kind) != is_text(&column.kind);
                let indexes: Vec<&IndexDef> = before
                    .existing(table)?
                    .indexes
                    .iter()
                    .filter(|i| single_column(&i.columns, i.unique) == Some(column.name.as_str()))
                    .collect();
                if pattern_change && is_text(&old.kind) {
                    for index in &indexes {
                        statements.push(drop_pattern_index(dialect, &index.name));
                    }
                }
                if old.kind != column.kind {
                    let target = dialect.column_type(&column.kind);
                    statements.push(plain(format!(
                        "ALTER TABLE {} ALTER COLUMN {name} TYPE {target} USING {name}::{target}",
                        q(table)
                    )));
                }
                if pattern_change && is_text(&column.kind) {
                    let target = after.existing(table)?;
                    for index in &indexes {
                        statements.extend(pattern_index(
                            dialect,
                            target,
                            table,
                            &index.name,
                            &index.columns,
                            index.unique,
                        ));
                    }
                }
                if old.null != column.null {
                    statements.push(plain(format!(
                        "ALTER TABLE {} ALTER COLUMN {name} {} NOT NULL",
                        q(table),
                        if column.null { "DROP" } else { "SET" }
                    )));
                }
                if old.unique != column.unique {
                    let constraint = q(&format!("{table}_{}_key", column.name));
                    statements.push(plain(if column.unique {
                        format!(
                            "ALTER TABLE {} ADD CONSTRAINT {constraint} UNIQUE ({name})",
                            q(table)
                        )
                    } else {
                        format!("ALTER TABLE {} DROP CONSTRAINT {constraint}", q(table))
                    }));
                }
                if let (true, Some(reference)) = (references_changed, &column.references) {
                    statements.push(plain(format!(
                        "ALTER TABLE {} ADD CONSTRAINT {fkey} FOREIGN KEY ({name}) REFERENCES {} ({}) \
                         ON DELETE {}",
                        q(table),
                        q(&reference.table),
                        q(&reference.column),
                        reference.on_delete.sql()
                    )));
                }
                statements
            } else {
                let target = after.existing(table)?;
                let sources = target
                    .columns
                    .iter()
                    .map(|c| Source::Column(c.name.clone()))
                    .collect();
                rebuild(dialect, table, target, sources)
            }
        }
        Operation::RenameColumn { table, old, new } => {
            let mut statements = vec![plain(format!(
                "ALTER TABLE {} RENAME COLUMN {} TO {}",
                q(table),
                q(old),
                q(new)
            ))];
            let current = before.existing(table)?;
            statements.extend(renamed_indexes(
                dialect,
                table,
                current,
                after.existing(table)?,
            ));
            if postgres && let Some(column) = current.column(old) {
                statements.extend(rename_constraints(
                    dialect,
                    table,
                    (table, old),
                    (table, new),
                    column,
                ));
            }
            statements
        }
        Operation::RunSql { sql, .. } => sql.iter().map(|s| plain(s.clone())).collect(),
        Operation::RunPython { .. } => Vec::new(),
        Operation::CreateExtension { name } if postgres => {
            vec![plain(format!("CREATE EXTENSION IF NOT EXISTS {}", q(name)))]
        }
        Operation::DropExtension { name } if postgres => {
            vec![plain(format!("DROP EXTENSION IF EXISTS {}", q(name)))]
        }
        Operation::CreateExtension { .. } | Operation::DropExtension { .. } => Vec::new(),
    })
}

/// SQL for a sequence of operations starting from `base`; returns the resulting state too.
pub fn compile_all(
    dialect: &dyn Dialect,
    base: &SchemaState,
    operations: &[Operation],
) -> Result<(Vec<Statement>, SchemaState), MigrationError> {
    let mut state = base.clone();
    let mut statements = Vec::new();
    for operation in operations {
        statements.extend(compile(dialect, &state, operation)?);
        state.apply(operation)?;
    }
    Ok((statements, state))
}
