//! Names → fields across relations (`category__name`, `items__product__price__gte`).

use std::sync::Arc;

use crate::error::OrmError;
use crate::field::{FieldKind, FieldMeta};
use crate::lookup::{DatePart, JsonKey, Lookup, Transform, check_lookup};
use crate::meta::ModelMeta;
use crate::registry::{Registry, Reverse};

/// One step from a model to a related model.
#[derive(Debug, Clone)]
pub enum Hop {
    /// `from.fields[field]` is a foreign key / one-to-one to `to`.
    Forward {
        from: Arc<ModelMeta>,
        field: usize,
        to: Arc<ModelMeta>,
    },
    /// `source.fields[field]` points at `from` (reverse foreign key / one-to-one).
    Reverse {
        from: Arc<ModelMeta>,
        source: Arc<ModelMeta>,
        field: usize,
    },
    /// Through the link model: `through.fields[from_field]` points at `from`,
    /// `through.fields[to_field]` at `to`.
    ManyToMany {
        from: Arc<ModelMeta>,
        through: Arc<ModelMeta>,
        from_field: usize,
        to_field: usize,
        to: Arc<ModelMeta>,
    },
}

impl Hop {
    pub fn from(&self) -> &Arc<ModelMeta> {
        match self {
            Self::Forward { from, .. }
            | Self::Reverse { from, .. }
            | Self::ManyToMany { from, .. } => from,
        }
    }

    pub fn target(&self) -> &Arc<ModelMeta> {
        match self {
            Self::Forward { to, .. } | Self::ManyToMany { to, .. } => to,
            Self::Reverse { source, .. } => source,
        }
    }

    /// At most one related row per row (forward keys and reverse one-to-one).
    pub fn single_valued(&self) -> bool {
        match self {
            Self::Forward { .. } => true,
            Self::Reverse { source, field, .. } => source.fields[*field].unique,
            Self::ManyToMany { .. } => false,
        }
    }

    /// Identity of the relation, for grouping conditions that follow the same hop.
    pub fn same(&self, other: &Hop) -> bool {
        match (self, other) {
            (
                Self::Forward {
                    from: a, field: f, ..
                },
                Self::Forward {
                    from: b, field: g, ..
                },
            ) => a.qualname == b.qualname && f == g,
            (
                Self::Reverse {
                    source: a,
                    field: f,
                    ..
                },
                Self::Reverse {
                    source: b,
                    field: g,
                    ..
                },
            ) => a.qualname == b.qualname && f == g,
            (
                Self::ManyToMany {
                    through: a,
                    from_field: f,
                    ..
                },
                Self::ManyToMany {
                    through: b,
                    from_field: g,
                    ..
                },
            ) => a.qualname == b.qualname && f == g,
            _ => false,
        }
    }

    /// Name of the relation on `from` (for messages and select_related caches).
    pub fn name(&self) -> &str {
        match self {
            Self::Forward { from, field, .. } => &from.fields[*field].name,
            Self::Reverse { source, .. } => &source.label,
            Self::ManyToMany { through, .. } => &through.label,
        }
    }
}

/// A field reached through zero or more hops from the query's model.
#[derive(Debug, Clone)]
pub struct FieldPath {
    pub hops: Vec<Hop>,
    /// The model owning `field` (the root when there are no hops).
    pub model: Arc<ModelMeta>,
    pub field: usize,
}

impl FieldPath {
    pub fn local(model: &Arc<ModelMeta>, field: usize) -> Self {
        Self {
            hops: Vec::new(),
            model: model.clone(),
            field,
        }
    }

    pub fn meta(&self) -> &FieldMeta {
        &self.model.fields[self.field]
    }

    pub fn is_local(&self) -> bool {
        self.hops.is_empty()
    }

    pub fn single_valued(&self) -> bool {
        self.hops.iter().all(Hop::single_valued)
    }

    /// Whether the path names the primary key of the last related model (never NULL there).
    pub fn is_related_pk(&self) -> bool {
        !self.hops.is_empty() && self.field == self.model.pk
    }
}

enum Step {
    Field(usize),
    /// A foreign key: usable as its local column or followed as a hop.
    Forward(usize, Hop),
    Hop(Hop),
}

fn step(registry: &Registry, model: &Arc<ModelMeta>, name: &str) -> Option<Step> {
    if let Some(index) = model.field(name) {
        let field = &model.fields[index];
        if let Some(relation) = &field.relation
            && field.name == name
        {
            let target = if relation.target == model.qualname {
                Some(
                    registry
                        .get(&model.qualname)
                        .unwrap_or_else(|| model.clone()),
                )
            } else {
                registry.get(&relation.target)
            };
            return Some(match target {
                Some(to) => Step::Forward(
                    index,
                    Hop::Forward {
                        from: model.clone(),
                        field: index,
                        to,
                    },
                ),
                None => Step::Field(index),
            });
        }
        return Some(Step::Field(index));
    }
    if let Some(relation) = registry.forward_many_to_many(&model.qualname, name) {
        return Some(Step::Hop(Hop::ManyToMany {
            from: model.clone(),
            through: registry.get(&relation.through)?,
            from_field: relation.from_field,
            to_field: relation.to_field,
            to: registry.get(&relation.target)?,
        }));
    }
    match registry.reverse(&model.qualname, name)? {
        Reverse::Field { source, field, .. } => Some(Step::Hop(Hop::Reverse {
            from: model.clone(),
            source,
            field,
        })),
        Reverse::ManyToMany(relation) => Some(Step::Hop(Hop::ManyToMany {
            from: model.clone(),
            through: registry.get(&relation.through)?,
            from_field: relation.to_field,
            to_field: relation.from_field,
            to: registry.get(&relation.source)?,
        })),
    }
}

fn unknown(registry: &Registry, model: &ModelMeta, name: &str) -> OrmError {
    let mut names: Vec<String> = model.fields.iter().map(|f| f.name.clone()).collect();
    names.extend(registry.relation_names(&model.qualname));
    OrmError(format!(
        "{} has no field '{name}'; fields: {}",
        model.label,
        names.join(", ")
    ))
}

fn is_lookup(registry: &Registry, model: &Arc<ModelMeta>, rest: &[&str]) -> bool {
    rest.len() == 1 && Lookup::parse(rest[0]).is_some() && step(registry, model, rest[0]).is_none()
}

/// A resolved lookup key: the field, an optional transform of its value, and the lookup.
#[derive(Debug, Clone)]
pub struct ResolvedLookup {
    pub path: FieldPath,
    pub transform: Option<Transform>,
    pub lookup: Lookup,
}

impl ResolvedLookup {
    /// The kind of the compared value (after the transform).
    pub fn compared_kind(&self) -> FieldKind {
        match &self.transform {
            None => self.path.meta().kind.clone(),
            Some(Transform::Date(part)) => part.output_kind(),
            Some(Transform::Json(_)) => FieldKind::Json,
        }
    }
}

fn finish(
    hops: Vec<Hop>,
    model: Arc<ModelMeta>,
    field: usize,
    rest: &[&str],
    key: &str,
) -> Result<ResolvedLookup, OrmError> {
    let meta = &model.fields[field];
    let unknown_lookup = |name: &str| {
        let names: Vec<&str> = Lookup::ALL.iter().map(|l| l.name()).collect();
        OrmError(format!(
            "{}: unknown lookup '{name}' in '{key}'; lookups: {}",
            model.label,
            names.join(", ")
        ))
    };
    let (transform, lookup) = if meta.kind == FieldKind::Json {
        // Every part before a final lookup name is a key of the document.
        let (keys, lookup) = match rest.split_last() {
            None => (&[][..], Lookup::Exact),
            Some((last, keys)) => match Lookup::parse(last) {
                Some(lookup) => (keys, lookup),
                None => (rest, Lookup::Exact),
            },
        };
        let transform = (!keys.is_empty())
            .then(|| Transform::Json(keys.iter().map(|k| JsonKey::parse(k)).collect()));
        if transform.is_some() {
            // A value inside the document: any lookup except JSON-only ones on scalars.
            return Ok(ResolvedLookup {
                path: FieldPath { hops, model, field },
                transform,
                lookup,
            });
        }
        (None, lookup)
    } else {
        match rest {
            [] => (None, Lookup::Exact),
            [name] => match DatePart::parse(name) {
                Some(part) if part.applies_to(&meta.kind) => {
                    (Some(Transform::Date(part)), Lookup::Exact)
                }
                _ => (
                    None,
                    Lookup::parse(name).ok_or_else(|| unknown_lookup(name))?,
                ),
            },
            [part, name] if DatePart::parse(part).is_some_and(|p| p.applies_to(&meta.kind)) => {
                let part = DatePart::parse(part).expect("checked above");
                (
                    Some(Transform::Date(part)),
                    Lookup::parse(name).ok_or_else(|| unknown_lookup(name))?,
                )
            }
            [next, ..] => {
                return Err(OrmError(format!(
                    "{}: '{}' is not a relation; cannot follow '{next}' in '{key}'",
                    model.label, meta.name
                )));
            }
        }
    };
    let kind = match transform {
        Some(Transform::Date(part)) => part.output_kind(),
        _ => meta.kind.clone(),
    };
    check_lookup(&model.label, &meta.name, &kind, lookup)?;
    Ok(ResolvedLookup {
        path: FieldPath { hops, model, field },
        transform,
        lookup,
    })
}

/// Resolves a lookup key (`category__name__icontains`) from `root`; transforms are refused.
pub fn resolve_lookup(
    registry: &Registry,
    root: &Arc<ModelMeta>,
    key: &str,
) -> Result<(FieldPath, Lookup), OrmError> {
    let resolved = resolve_lookup_key(registry, root, key)?;
    if resolved.transform.is_some() {
        return Err(OrmError(format!(
            "{}: '{key}' uses a transform here",
            root.label
        )));
    }
    Ok((resolved.path, resolved.lookup))
}

/// Resolves a lookup key with transforms: `created__year__gte`, `data__owner__name__iexact`.
pub fn resolve_lookup_key(
    registry: &Registry,
    root: &Arc<ModelMeta>,
    key: &str,
) -> Result<ResolvedLookup, OrmError> {
    let parts: Vec<&str> = key.split("__").collect();
    let mut model = root.clone();
    let mut hops = Vec::new();
    let mut i = 0;
    loop {
        let name = parts[i];
        let rest = &parts[i + 1..];
        match step(registry, &model, name).ok_or_else(|| unknown(registry, &model, name))? {
            Step::Field(field) => return finish(hops, model, field, rest, key),
            Step::Forward(field, hop) => {
                let target = hop.target().clone();
                if rest.is_empty() || is_lookup(registry, &target, rest) {
                    return finish(hops, model, field, rest, key);
                }
                // `category__pk` / `category__id` compare the local key column.
                if (rest[0] == "pk" || rest[0] == target.pk_field().name)
                    && (rest.len() == 1 || is_lookup(registry, &target, &rest[1..]))
                {
                    return finish(hops, model, field, &rest[1..], key);
                }
                hops.push(hop);
                model = target;
            }
            Step::Hop(hop) => {
                let target = hop.target().clone();
                hops.push(hop);
                if rest.is_empty() || is_lookup(registry, &target, rest) {
                    let pk = target.pk;
                    return finish(hops, target, pk, rest, key);
                }
                model = target;
            }
        }
        i += 1;
    }
}

/// Resolves a field path without a lookup (`order_by`, `values`, aggregates). With
/// `single_valued`, paths through reverse foreign keys and many-to-many relations are refused.
pub fn resolve_field(
    registry: &Registry,
    root: &Arc<ModelMeta>,
    name: &str,
    single_valued: bool,
    purpose: &str,
) -> Result<FieldPath, OrmError> {
    let parts: Vec<&str> = name.split("__").collect();
    let mut model = root.clone();
    let mut hops = Vec::new();
    for (i, part) in parts.iter().enumerate() {
        let last = i + 1 == parts.len();
        match step(registry, &model, part).ok_or_else(|| unknown(registry, &model, part))? {
            Step::Field(field) => {
                if !last {
                    return Err(OrmError(format!(
                        "{}: '{part}' is not a relation; cannot follow '{}' in '{name}'",
                        model.label,
                        parts[i + 1]
                    )));
                }
                return Ok(FieldPath { hops, model, field });
            }
            Step::Forward(field, hop) => {
                if last {
                    return Ok(FieldPath { hops, model, field });
                }
                model = hop.target().clone();
                hops.push(hop);
            }
            Step::Hop(hop) => {
                if single_valued && !hop.single_valued() {
                    return Err(OrmError(format!(
                        "{}: {purpose} cannot follow the many-valued relation '{part}' in '{name}'",
                        model.label
                    )));
                }
                model = hop.target().clone();
                hops.push(hop);
                if last {
                    let pk = model.pk;
                    return Ok(FieldPath {
                        hops,
                        model,
                        field: pk,
                    });
                }
            }
        }
    }
    unreachable!("a name has at least one part")
}

/// Resolves a `select_related` path: every part must be a foreign key or one-to-one field.
pub fn resolve_related(
    registry: &Registry,
    root: &Arc<ModelMeta>,
    name: &str,
) -> Result<Vec<Hop>, OrmError> {
    let mut model = root.clone();
    let mut hops = Vec::new();
    for part in name.split("__") {
        match step(registry, &model, part) {
            Some(Step::Forward(_, hop)) => {
                model = hop.target().clone();
                hops.push(hop);
            }
            Some(_) => {
                return Err(OrmError(format!(
                    "{}: select_related() follows foreign keys and one-to-one fields only; \
                     '{part}' is not one (use prefetch_related('{name}'))",
                    model.label
                )));
            }
            None => return Err(unknown(registry, &model, part)),
        }
    }
    Ok(hops)
}
