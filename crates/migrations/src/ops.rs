use ferro_database::Value;
use ferro_orm::{FieldKind, FieldMeta, ForeignKeyRef};

/// A column as recorded in migrations. `name` is the database column name.
#[derive(Debug, Clone, PartialEq)]
pub struct ColumnDef {
    pub name: String,
    pub kind: FieldKind,
    pub null: bool,
    pub unique: bool,
    pub primary_key: bool,
    pub auto: bool,
    /// Foreign key constraint (`REFERENCES table (column) ON DELETE ...`).
    pub references: Option<ForeignKeyRef>,
}

impl ColumnDef {
    pub fn new(name: impl Into<String>, kind: FieldKind) -> Self {
        Self {
            name: name.into(),
            kind,
            null: false,
            unique: false,
            primary_key: false,
            auto: false,
            references: None,
        }
    }

    pub fn from_field(field: &FieldMeta) -> Self {
        Self {
            name: field.column.clone(),
            kind: field.kind.clone(),
            null: field.null,
            unique: field.unique,
            primary_key: field.primary_key,
            auto: field.auto,
            references: field.references.clone(),
        }
    }

    /// The equivalent ORM field, for DDL generation.
    pub fn to_field(&self) -> FieldMeta {
        let mut field = FieldMeta::column(self.name.clone(), self.kind.clone());
        field.null = self.null;
        field.unique = self.unique || self.primary_key;
        field.primary_key = self.primary_key;
        field.auto = self.auto;
        field.references = self.references.clone();
        field
    }
}

/// One schema change (FR-002).
#[derive(Debug, Clone, PartialEq)]
pub enum Operation {
    CreateTable {
        table: String,
        columns: Vec<ColumnDef>,
    },
    DropTable {
        table: String,
    },
    RenameTable {
        old: String,
        new: String,
    },
    /// `default` back-fills existing rows when the column is non-null.
    AddColumn {
        table: String,
        column: ColumnDef,
        default: Option<Value>,
    },
    /// `default` makes removing a non-null column reversible.
    RemoveColumn {
        table: String,
        column: String,
        default: Option<Value>,
    },
    AlterColumn {
        table: String,
        column: ColumnDef,
    },
    RenameColumn {
        table: String,
        old: String,
        new: String,
    },
    CreateIndex {
        table: String,
        columns: Vec<String>,
        name: String,
        unique: bool,
    },
    DropIndex {
        table: String,
        name: String,
    },
    /// Hand-written SQL, one statement per entry (Feature 021). Leaves the recorded schema
    /// unchanged; reversible only with `reverse_sql`.
    RunSql {
        sql: Vec<String>,
        reverse_sql: Option<Vec<String>>,
    },
    /// Python code run by the migration runner; no SQL here. Reversible with a backward function.
    RunPython {
        reversible: bool,
    },
    /// `CREATE EXTENSION IF NOT EXISTS` on PostgreSQL; nothing on SQLite.
    CreateExtension {
        name: String,
    },
    /// `DROP EXTENSION IF EXISTS` on PostgreSQL; nothing on SQLite.
    DropExtension {
        name: String,
    },
}

impl Operation {
    /// Whether the operation leaves the recorded schema unchanged (data and extension steps).
    pub fn keeps_schema(&self) -> bool {
        matches!(
            self,
            Self::RunSql { .. }
                | Self::RunPython { .. }
                | Self::CreateExtension { .. }
                | Self::DropExtension { .. }
        )
    }
}
