use crate::error::OrmError;
use crate::field::FieldKind;
use crate::meta::ModelMeta;

/// Lookups usable as `field__lookup=value` (FR-009; Feature 022 added the case-insensitive
/// prefix/suffix, range, regular-expression and JSON lookups).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Lookup {
    Exact,
    IExact,
    Contains,
    IContains,
    StartsWith,
    IStartsWith,
    EndsWith,
    IEndsWith,
    Gt,
    Gte,
    Lt,
    Lte,
    In,
    Range,
    IsNull,
    Regex,
    IRegex,
    /// JSON: the document (or the value at a key path) is an object with this key.
    HasKey,
    HasKeys,
    HasAnyKeys,
}

impl Lookup {
    pub const ALL: [Lookup; 20] = [
        Self::Exact,
        Self::IExact,
        Self::Contains,
        Self::IContains,
        Self::StartsWith,
        Self::IStartsWith,
        Self::EndsWith,
        Self::IEndsWith,
        Self::Gt,
        Self::Gte,
        Self::Lt,
        Self::Lte,
        Self::In,
        Self::Range,
        Self::IsNull,
        Self::Regex,
        Self::IRegex,
        Self::HasKey,
        Self::HasKeys,
        Self::HasAnyKeys,
    ];

    pub fn name(self) -> &'static str {
        match self {
            Self::Exact => "exact",
            Self::IExact => "iexact",
            Self::Contains => "contains",
            Self::IContains => "icontains",
            Self::StartsWith => "startswith",
            Self::IStartsWith => "istartswith",
            Self::EndsWith => "endswith",
            Self::IEndsWith => "iendswith",
            Self::Gt => "gt",
            Self::Gte => "gte",
            Self::Lt => "lt",
            Self::Lte => "lte",
            Self::In => "in",
            Self::Range => "range",
            Self::IsNull => "isnull",
            Self::Regex => "regex",
            Self::IRegex => "iregex",
            Self::HasKey => "has_key",
            Self::HasKeys => "has_keys",
            Self::HasAnyKeys => "has_any_keys",
        }
    }

    pub fn parse(name: &str) -> Option<Self> {
        Self::ALL.into_iter().find(|l| l.name() == name)
    }

    /// Pattern lookups on text (`LIKE`-style); their operand is a string.
    pub fn is_text(self) -> bool {
        matches!(
            self,
            Self::IExact
                | Self::Contains
                | Self::IContains
                | Self::StartsWith
                | Self::IStartsWith
                | Self::EndsWith
                | Self::IEndsWith
        )
    }

    pub fn is_regex(self) -> bool {
        matches!(self, Self::Regex | Self::IRegex)
    }

    pub fn is_json_key(self) -> bool {
        matches!(self, Self::HasKey | Self::HasKeys | Self::HasAnyKeys)
    }

    pub fn is_comparison(self) -> bool {
        matches!(
            self,
            Self::Gt | Self::Gte | Self::Lt | Self::Lte | Self::Range
        )
    }
}

/// Parts of a date or time extracted by `created__year=2026`-style transforms.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum DatePart {
    Year,
    Quarter,
    Month,
    Day,
    /// 1 = Sunday ... 7 = Saturday (Django's numbering).
    WeekDay,
    Hour,
    Minute,
    Second,
    /// The date of a date-time (UTC).
    Date,
}

impl DatePart {
    pub const ALL: [DatePart; 9] = [
        Self::Year,
        Self::Quarter,
        Self::Month,
        Self::Day,
        Self::WeekDay,
        Self::Hour,
        Self::Minute,
        Self::Second,
        Self::Date,
    ];

    pub fn name(self) -> &'static str {
        match self {
            Self::Year => "year",
            Self::Quarter => "quarter",
            Self::Month => "month",
            Self::Day => "day",
            Self::WeekDay => "week_day",
            Self::Hour => "hour",
            Self::Minute => "minute",
            Self::Second => "second",
            Self::Date => "date",
        }
    }

    pub fn parse(name: &str) -> Option<Self> {
        Self::ALL.into_iter().find(|p| p.name() == name)
    }

    /// Whether the part exists for values of `kind`.
    pub fn applies_to(self, kind: &FieldKind) -> bool {
        match self {
            Self::Year | Self::Quarter | Self::Month | Self::Day | Self::WeekDay => {
                matches!(kind, FieldKind::Date | FieldKind::DateTime)
            }
            Self::Hour | Self::Minute | Self::Second => {
                matches!(kind, FieldKind::DateTime | FieldKind::Time)
            }
            Self::Date => matches!(kind, FieldKind::DateTime),
        }
    }

    /// The kind of the extracted value.
    pub fn output_kind(self) -> FieldKind {
        match self {
            Self::Date => FieldKind::Date,
            _ => FieldKind::Integer,
        }
    }
}

/// One step into a JSON document: an object key or an array index.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum JsonKey {
    Key(String),
    Index(i64),
}

impl JsonKey {
    /// `"3"` and `"-1"` are array indexes; anything else is an object key.
    pub fn parse(part: &str) -> Self {
        match part.parse::<i64>() {
            Ok(index) => Self::Index(index),
            Err(_) => Self::Key(part.to_string()),
        }
    }
}

/// A transform applied to a field's value before the lookup compares it.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Transform {
    Date(DatePart),
    /// The value at a key path inside a JSON field.
    Json(Vec<JsonKey>),
}

/// Splits `price__gte` into a field index and a lookup, validating both against the model.
pub fn parse_key(meta: &ModelMeta, key: &str) -> Result<(usize, Lookup), OrmError> {
    let (field_name, lookup_name) = match key.split_once("__") {
        Some((field, lookup)) => (field, Some(lookup)),
        None => (key, None),
    };
    let field = meta.field(field_name).ok_or_else(|| {
        OrmError(format!(
            "{} has no field '{field_name}'; fields: {}",
            meta.label,
            meta.field_names().join(", ")
        ))
    })?;
    let lookup = match lookup_name {
        None => Lookup::Exact,
        Some(name) => Lookup::parse(name).ok_or_else(|| {
            let names: Vec<&str> = Lookup::ALL.iter().map(|l| l.name()).collect();
            OrmError(format!(
                "{}: unknown lookup '{name}' in '{key}'; lookups: {}",
                meta.label,
                names.join(", ")
            ))
        })?,
    };
    check_lookup(
        &meta.label,
        &meta.fields[field].name,
        &meta.fields[field].kind,
        lookup,
    )?;
    Ok((field, lookup))
}

/// Refuses lookups that make no sense for values of `kind` (`name` is for the message).
pub fn check_lookup(
    label: &str,
    name: &str,
    kind: &FieldKind,
    lookup: Lookup,
) -> Result<(), OrmError> {
    let json = *kind == FieldKind::Json;
    let ok = if lookup.is_json_key() {
        json
    } else if lookup == Lookup::Contains && json {
        true
    } else if lookup.is_text() || lookup.is_regex() {
        kind.is_text()
    } else if lookup.is_comparison() {
        kind.is_ordered()
    } else {
        true
    };
    if ok {
        return Ok(());
    }
    Err(OrmError(format!(
        "{label}: lookup '{}' cannot be used on {} field '{name}'",
        lookup.name(),
        kind.name(),
    )))
}
