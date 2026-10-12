use crate::error::OrmError;
use crate::meta::ModelMeta;

/// Lookups usable as `field__lookup=value` (FR-009).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Lookup {
    Exact,
    IExact,
    Contains,
    IContains,
    StartsWith,
    EndsWith,
    Gt,
    Gte,
    Lt,
    Lte,
    In,
    IsNull,
}

impl Lookup {
    pub const ALL: [Lookup; 12] = [
        Self::Exact,
        Self::IExact,
        Self::Contains,
        Self::IContains,
        Self::StartsWith,
        Self::EndsWith,
        Self::Gt,
        Self::Gte,
        Self::Lt,
        Self::Lte,
        Self::In,
        Self::IsNull,
    ];

    pub fn name(self) -> &'static str {
        match self {
            Self::Exact => "exact",
            Self::IExact => "iexact",
            Self::Contains => "contains",
            Self::IContains => "icontains",
            Self::StartsWith => "startswith",
            Self::EndsWith => "endswith",
            Self::Gt => "gt",
            Self::Gte => "gte",
            Self::Lt => "lt",
            Self::Lte => "lte",
            Self::In => "in",
            Self::IsNull => "isnull",
        }
    }

    pub fn parse(name: &str) -> Option<Self> {
        Self::ALL.into_iter().find(|l| l.name() == name)
    }

    pub fn is_text(self) -> bool {
        matches!(
            self,
            Self::IExact | Self::Contains | Self::IContains | Self::StartsWith | Self::EndsWith
        )
    }

    pub fn is_comparison(self) -> bool {
        matches!(self, Self::Gt | Self::Gte | Self::Lt | Self::Lte)
    }
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
    let kind = &meta.fields[field].kind;
    if (lookup.is_text() && !kind.is_text()) || (lookup.is_comparison() && !kind.is_ordered()) {
        return Err(OrmError(format!(
            "{}: lookup '{}' cannot be used on {} field '{}'",
            meta.label,
            lookup.name(),
            kind.name(),
            meta.fields[field].name
        )));
    }
    Ok((field, lookup))
}
