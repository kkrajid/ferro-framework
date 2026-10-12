use std::collections::HashSet;
use std::sync::Arc;

use crate::error::OrmError;
use crate::field::{FieldKind, FieldMeta, FieldSpec, ForeignKeyRef, OnDelete, Relation};

/// Attribute names a field may not use: they belong to the model API.
pub const RESERVED_NAMES: &[&str] = &[
    "objects",
    "pk",
    "save",
    "delete",
    "refresh",
    "to_dict",
    "DoesNotExist",
    "MultipleObjectsReturned",
    "Meta",
];

pub fn is_identifier(name: &str) -> bool {
    let mut chars = name.chars();
    matches!(chars.next(), Some(c) if c == '_' || c.is_ascii_alphabetic())
        && chars.all(|c| c == '_' || c.is_ascii_alphanumeric())
}

/// `OrderItem` → `order_item`, `HTTPLog` → `http_log`.
pub fn snake_case(name: &str) -> String {
    let chars: Vec<char> = name.chars().collect();
    let mut out = String::with_capacity(name.len() + 4);
    for (i, &c) in chars.iter().enumerate() {
        if c.is_ascii_uppercase() {
            if i > 0 {
                let prev = chars[i - 1];
                let next_is_lower = chars.get(i + 1).is_some_and(|n| n.is_ascii_lowercase());
                if prev.is_ascii_lowercase()
                    || prev.is_ascii_digit()
                    || (prev.is_ascii_uppercase() && next_is_lower)
                {
                    out.push('_');
                }
            }
            out.push(c.to_ascii_lowercase());
        } else {
            out.push(c);
        }
    }
    out
}

/// A multi-column index (`Meta.unique_together`).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CompositeIndex {
    pub name: String,
    pub columns: Vec<String>,
    pub unique: bool,
}

/// Looks up registered models by qualname while a model with relations is validated.
pub type Resolver<'a> = &'a dyn Fn(&str) -> Option<Arc<ModelMeta>>;

/// A validated model definition.
#[derive(Debug, Clone, PartialEq)]
pub struct ModelMeta {
    /// `module.QualName`; identity in the registry.
    pub qualname: String,
    /// Class name, used in messages.
    pub label: String,
    pub table: String,
    pub fields: Vec<FieldMeta>,
    /// Index of the primary-key field in `fields`.
    pub pk: usize,
    pub indexes: Vec<CompositeIndex>,
}

impl ModelMeta {
    /// A model without relations or composite indexes.
    pub fn new(
        qualname: &str,
        label: &str,
        table: &str,
        specs: Vec<FieldSpec>,
    ) -> Result<Self, OrmError> {
        Self::build(qualname, label, table, specs, &[], &|_| None)
    }

    /// Validates a model. Relation targets are found through `resolve` (or are the model itself
    /// for `"self"`); `unique_together` lists groups of field names that must be unique together.
    pub fn build(
        qualname: &str,
        label: &str,
        table: &str,
        specs: Vec<FieldSpec>,
        unique_together: &[Vec<String>],
        resolve: Resolver<'_>,
    ) -> Result<Self, OrmError> {
        let error = |message: String| OrmError(format!("{label}: {message}"));
        if !is_identifier(table) {
            return Err(error(format!(
                "invalid table name '{table}' (use letters, digits and underscores)"
            )));
        }
        // The model's own key, needed by self references before the fields are built.
        let own_key = match specs.iter().find(|s| s.primary_key) {
            Some(spec) if spec.relation.is_some() => {
                return Err(error(format!(
                    "field '{}': a relation cannot be the primary key",
                    spec.name
                )));
            }
            Some(spec) => (
                spec.kind.clone(),
                spec.column.clone().unwrap_or_else(|| spec.name.clone()),
            ),
            None => (FieldKind::BigInteger, "id".to_string()),
        };
        let mut names = HashSet::new();
        let mut key_attributes = std::collections::HashMap::new();
        let mut columns = HashSet::new();
        let mut fields = Vec::with_capacity(specs.len() + 1);
        let mut pk = None;
        for spec in specs {
            let name = spec.name;
            if !is_identifier(&name) {
                return Err(error(format!(
                    "field name '{name}' is not a valid identifier"
                )));
            }
            if name.starts_with('_') || RESERVED_NAMES.contains(&name.as_str()) {
                return Err(error(format!("field name '{name}' is reserved")));
            }
            if let Some(relation) = key_attributes.get(&name) {
                return Err(error(format!(
                    "field '{name}' clashes with the key attribute of relation '{relation}'"
                )));
            }
            if !names.insert(name.clone()) {
                return Err(error(format!("duplicate field '{name}'")));
            }
            let (kind, attname, relation, references, unique, index) = match spec.relation {
                None => {
                    spec.kind
                        .validate()
                        .map_err(|message| error(format!("field '{name}': {message}")))?;
                    (
                        spec.kind,
                        name.clone(),
                        None,
                        None,
                        spec.unique || spec.primary_key,
                        spec.index && !spec.primary_key,
                    )
                }
                Some(relation) => {
                    let (target, target_table, (kind, target_column)) =
                        if relation.target == "self" || relation.target == qualname {
                            (qualname.to_string(), table.to_string(), own_key.clone())
                        } else {
                            let target = resolve(&relation.target).ok_or_else(|| {
                                error(format!(
                                    "field '{name}': related model '{}' is not defined",
                                    relation.target
                                ))
                            })?;
                            let key = target.pk_field();
                            (
                                target.qualname.clone(),
                                target.table.clone(),
                                (key.kind.clone(), key.column.clone()),
                            )
                        };
                    if relation.on_delete == OnDelete::SetNull && !spec.null {
                        return Err(error(format!(
                            "field '{name}': on_delete=SET_NULL needs null=True"
                        )));
                    }
                    if let Some(related) = &relation.related_name
                        && related != "+"
                        && !is_identifier(related)
                    {
                        return Err(error(format!(
                            "field '{name}': related_name '{related}' is not a valid identifier"
                        )));
                    }
                    let attname = format!("{name}_id");
                    key_attributes.insert(attname.clone(), name.clone());
                    if !names.insert(attname.clone()) {
                        return Err(error(format!(
                            "field '{attname}' clashes with the key attribute of relation '{name}'"
                        )));
                    }
                    let unique = spec.unique || relation.one_to_one;
                    (
                        kind,
                        attname,
                        Some(Relation {
                            target,
                            one_to_one: relation.one_to_one,
                            related_name: relation.related_name,
                        }),
                        Some(ForeignKeyRef {
                            table: target_table,
                            column: target_column,
                            on_delete: relation.on_delete,
                        }),
                        unique,
                        !unique,
                    )
                }
            };
            let column = spec.column.unwrap_or_else(|| attname.clone());
            if !is_identifier(&column) {
                return Err(error(format!(
                    "column name '{column}' of field '{name}' is not a valid identifier"
                )));
            }
            if !columns.insert(column.clone()) {
                return Err(error(format!("duplicate column '{column}'")));
            }
            if spec.primary_key {
                if pk.is_some() {
                    return Err(error("more than one primary key".to_string()));
                }
                if spec.null {
                    return Err(error(format!(
                        "field '{name}': a primary key cannot be null"
                    )));
                }
                pk = Some(fields.len());
            }
            fields.push(FieldMeta {
                name,
                attname,
                column,
                kind,
                null: spec.null,
                unique,
                index,
                primary_key: spec.primary_key,
                auto: false,
                relation,
                references,
            });
        }
        let pk = match pk {
            Some(pk) => pk,
            None => {
                if names.contains("id") || columns.contains("id") {
                    return Err(error(
                        "'id' is reserved for the automatic primary key; add primary_key=True \
                         to make it the key"
                            .to_string(),
                    ));
                }
                let mut id = FieldMeta::column("id", FieldKind::BigInteger);
                id.unique = true;
                id.primary_key = true;
                id.auto = true;
                fields.insert(0, id);
                0
            }
        };
        let mut indexes = Vec::with_capacity(unique_together.len());
        for group in unique_together {
            if group.is_empty() {
                return Err(error(
                    "unique_together needs at least one field".to_string(),
                ));
            }
            let mut group_columns = Vec::with_capacity(group.len());
            for field_name in group {
                let field = fields
                    .iter()
                    .find(|f| f.name == *field_name || f.attname == *field_name)
                    .ok_or_else(|| {
                        error(format!(
                            "unique_together names unknown field '{field_name}'"
                        ))
                    })?;
                group_columns.push(field.column.clone());
            }
            indexes.push(CompositeIndex {
                name: format!("{table}_{}_uniq", group_columns.join("_")),
                columns: group_columns,
                unique: true,
            });
        }
        Ok(Self {
            qualname: qualname.to_string(),
            label: label.to_string(),
            table: table.to_string(),
            fields,
            pk,
            indexes,
        })
    }

    /// Index of a field by name or attribute name (`category_id`); `pk` names the primary key.
    pub fn field(&self, name: &str) -> Option<usize> {
        if name == "pk" {
            return Some(self.pk);
        }
        self.fields
            .iter()
            .position(|f| f.name == name)
            .or_else(|| self.fields.iter().position(|f| f.attname == name))
    }

    pub fn pk_field(&self) -> &FieldMeta {
        &self.fields[self.pk]
    }

    pub fn field_names(&self) -> Vec<&str> {
        self.fields.iter().map(|f| f.name.as_str()).collect()
    }

    /// Foreign keys of this model pointing at `table`.
    pub fn references_to<'a>(&'a self, table: &'a str) -> impl Iterator<Item = &'a FieldMeta> {
        self.fields
            .iter()
            .filter(move |f| f.references.as_ref().is_some_and(|r| r.table == table))
    }
}
