/// The MVP field types (FR-002).
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum FieldKind {
    String { max_length: u32 },
    Text,
    Integer,
    BigInteger,
    Float,
    Decimal { max_digits: u8, decimal_places: u8 },
    Boolean,
    Date,
    DateTime,
    Time,
    Uuid,
    Json,
}

pub const MAX_STRING_LENGTH: u32 = 10_485_760;
pub const MAX_DECIMAL_DIGITS: u8 = 38;

impl FieldKind {
    pub fn name(&self) -> &'static str {
        match self {
            Self::String { .. } => "String",
            Self::Text => "Text",
            Self::Integer => "Integer",
            Self::BigInteger => "BigInteger",
            Self::Float => "Float",
            Self::Decimal { .. } => "Decimal",
            Self::Boolean => "Boolean",
            Self::Date => "Date",
            Self::DateTime => "DateTime",
            Self::Time => "Time",
            Self::Uuid => "UUID",
            Self::Json => "Json",
        }
    }

    /// Text lookups (`contains`, `iexact`, ...) apply only to text columns.
    pub fn is_text(&self) -> bool {
        matches!(self, Self::String { .. } | Self::Text)
    }

    /// Range comparisons (`gt`, `lte`, ...) are meaningless for booleans and JSON documents.
    pub fn is_ordered(&self) -> bool {
        !matches!(self, Self::Boolean | Self::Json)
    }

    pub(crate) fn validate(&self) -> Result<(), String> {
        match *self {
            Self::String { max_length } if !(1..=MAX_STRING_LENGTH).contains(&max_length) => Err(
                format!("max_length must be between 1 and {MAX_STRING_LENGTH}, got {max_length}"),
            ),
            Self::Decimal {
                max_digits,
                decimal_places,
            } => {
                if !(1..=MAX_DECIMAL_DIGITS).contains(&max_digits) {
                    Err(format!(
                        "max_digits must be between 1 and {MAX_DECIMAL_DIGITS}, got {max_digits}"
                    ))
                } else if decimal_places > max_digits {
                    Err(format!(
                        "decimal_places ({decimal_places}) cannot exceed max_digits ({max_digits})"
                    ))
                } else {
                    Ok(())
                }
            }
            _ => Ok(()),
        }
    }
}

/// What the database does with referencing rows when a referenced row is deleted.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum OnDelete {
    Cascade,
    SetNull,
    /// Refuse the delete (`ON DELETE RESTRICT`); reported as `ProtectedError`.
    Protect,
    /// Leave it to the constraint (`NO ACTION`): the delete fails if rows still refer to it.
    DoNothing,
}

impl OnDelete {
    pub const ALL: [OnDelete; 4] = [Self::Cascade, Self::SetNull, Self::Protect, Self::DoNothing];

    /// Name used in Python and migration files.
    pub fn name(self) -> &'static str {
        match self {
            Self::Cascade => "cascade",
            Self::SetNull => "set_null",
            Self::Protect => "protect",
            Self::DoNothing => "do_nothing",
        }
    }

    pub fn parse(name: &str) -> Option<Self> {
        Self::ALL.into_iter().find(|o| o.name() == name)
    }

    pub fn sql(self) -> &'static str {
        match self {
            Self::Cascade => "CASCADE",
            Self::SetNull => "SET NULL",
            Self::Protect => "RESTRICT",
            Self::DoNothing => "NO ACTION",
        }
    }
}

/// A foreign key constraint as the database sees it.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ForeignKeyRef {
    pub table: String,
    pub column: String,
    pub on_delete: OnDelete,
}

/// A relation as declared: the target is a registered model's qualname or `"self"`.
#[derive(Debug, Clone, PartialEq)]
pub struct RelationSpec {
    pub target: String,
    pub one_to_one: bool,
    pub on_delete: OnDelete,
    /// `None` → default reverse names; `Some("+")` → no reverse relation.
    pub related_name: Option<String>,
}

/// A resolved relation of a foreign key / one-to-one field.
#[derive(Debug, Clone, PartialEq)]
pub struct Relation {
    /// Qualname of the target model.
    pub target: String,
    pub one_to_one: bool,
    pub related_name: Option<String>,
}

/// A field as declared by the developer.
#[derive(Debug, Clone, PartialEq)]
pub struct FieldSpec {
    pub name: String,
    pub column: Option<String>,
    /// Ignored for relations: the target's key kind is used.
    pub kind: FieldKind,
    pub null: bool,
    pub unique: bool,
    pub index: bool,
    pub primary_key: bool,
    pub relation: Option<RelationSpec>,
}

impl FieldSpec {
    pub fn new(name: impl Into<String>, kind: FieldKind) -> Self {
        Self {
            name: name.into(),
            column: None,
            kind,
            null: false,
            unique: false,
            index: false,
            primary_key: false,
            relation: None,
        }
    }

    /// A foreign key (or one-to-one) field to `target` (`"self"` for the model itself).
    pub fn relation(
        name: impl Into<String>,
        target: impl Into<String>,
        on_delete: OnDelete,
        one_to_one: bool,
    ) -> Self {
        let mut spec = Self::new(name, FieldKind::BigInteger);
        spec.relation = Some(RelationSpec {
            target: target.into(),
            one_to_one,
            on_delete,
            related_name: None,
        });
        spec
    }
}

/// A validated field of a registered model.
#[derive(Debug, Clone, PartialEq)]
pub struct FieldMeta {
    pub name: String,
    /// Attribute holding the stored value on instances: `name`, or `<name>_id` for relations.
    pub attname: String,
    pub column: String,
    pub kind: FieldKind,
    pub null: bool,
    pub unique: bool,
    pub index: bool,
    pub primary_key: bool,
    /// The implicit auto-incrementing `id`.
    pub auto: bool,
    pub relation: Option<Relation>,
    pub references: Option<ForeignKeyRef>,
}

impl FieldMeta {
    /// A plain column (no relation) — used by DDL helpers and tests.
    pub fn column(name: impl Into<String>, kind: FieldKind) -> Self {
        let name = name.into();
        Self {
            attname: name.clone(),
            column: name.clone(),
            name,
            kind,
            null: false,
            unique: false,
            index: false,
            primary_key: false,
            auto: false,
            relation: None,
            references: None,
        }
    }
}
