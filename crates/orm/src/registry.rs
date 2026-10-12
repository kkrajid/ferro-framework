use std::collections::HashMap;
use std::sync::Arc;

use crate::error::OrmError;
use crate::meta::{ModelMeta, snake_case};

/// A many-to-many relation: `source.name` links to `target` through the link model `through`,
/// whose fields `from_field` / `to_field` point at the source and the target.
#[derive(Debug, Clone, PartialEq)]
pub struct ManyToMany {
    pub source: String,
    pub name: String,
    pub target: String,
    pub through: String,
    pub from_field: usize,
    pub to_field: usize,
    pub related_name: Option<String>,
}

impl ManyToMany {
    /// Name used for lookups from the target side (`None` for `related_name="+"`).
    pub fn reverse_name(&self, source_label: &str) -> Option<String> {
        reverse_name(self.related_name.as_deref(), source_label)
    }
}

/// A relation seen from the model it points at.
#[derive(Debug, Clone, PartialEq)]
pub enum Reverse {
    /// A foreign key / one-to-one field of `source` pointing here.
    Field {
        source: Arc<ModelMeta>,
        field: usize,
        one_to_one: bool,
    },
    /// The target side of a many-to-many relation.
    ManyToMany(ManyToMany),
}

fn reverse_name(related_name: Option<&str>, source_label: &str) -> Option<String> {
    match related_name {
        Some("+") => None,
        Some(name) => Some(name.to_string()),
        None => Some(snake_case(source_label)),
    }
}

/// Every model known to the process.
///
/// Registering the same `qualname` again replaces the earlier definition (module reloads, models
/// defined inside test functions). A different model claiming an existing table is an error.
#[derive(Debug, Default, Clone)]
pub struct Registry {
    order: Vec<String>,
    models: HashMap<String, Arc<ModelMeta>>,
    tables: HashMap<String, String>,
    many_to_many: Vec<ManyToMany>,
}

impl Registry {
    pub fn register(&mut self, meta: ModelMeta) -> Result<Arc<ModelMeta>, OrmError> {
        if let Some(owner) = self.tables.get(&meta.table)
            && *owner != meta.qualname
        {
            return Err(OrmError(format!(
                "tables clash: {owner} and {} both use table '{}'; set Meta.table on one of them",
                meta.qualname, meta.table
            )));
        }
        let meta = Arc::new(meta);
        for field in &meta.fields {
            let Some(relation) = &field.relation else {
                continue;
            };
            let Some(name) = reverse_name(relation.related_name.as_deref(), &meta.label) else {
                continue;
            };
            let target = if relation.target == meta.qualname {
                meta.clone()
            } else {
                self.get(&relation.target).ok_or_else(|| {
                    OrmError(format!(
                        "{}.{}: related model '{}' is not registered",
                        meta.label, field.name, relation.target
                    ))
                })?
            };
            self.check_reverse_name(&target, &name, &meta.qualname, &field.name, Some(&meta))
                .map_err(|other| {
                    OrmError(format!(
                        "{}.{}: reverse name '{name}' clashes with {other} on {}; set \
                         related_name",
                        meta.label, field.name, target.label
                    ))
                })?;
        }
        match self.models.get(&meta.qualname) {
            Some(previous) => {
                self.tables.remove(&previous.table);
            }
            None => self.order.push(meta.qualname.clone()),
        }
        self.tables
            .insert(meta.table.clone(), meta.qualname.clone());
        self.models.insert(meta.qualname.clone(), meta.clone());
        Ok(meta)
    }

    /// Records a many-to-many relation (after its source, target and link model are registered).
    pub fn register_many_to_many(&mut self, relation: ManyToMany) -> Result<(), OrmError> {
        let source = self.existing(&relation.source)?;
        let target = self.existing(&relation.target)?;
        self.existing(&relation.through)?;
        if source.field(&relation.name).is_some() {
            return Err(OrmError(format!(
                "{}: many-to-many '{}' clashes with a field of the same name",
                source.label, relation.name
            )));
        }
        if let Some(name) = relation.reverse_name(&source.label) {
            self.check_reverse_name(&target, &name, &source.qualname, &relation.name, None)
                .map_err(|other| {
                    OrmError(format!(
                        "{}.{}: reverse name '{name}' clashes with {other} on {}; set \
                         related_name",
                        source.label, relation.name, target.label
                    ))
                })?;
        }
        self.many_to_many
            .retain(|m| !(m.source == relation.source && m.name == relation.name));
        self.many_to_many.push(relation);
        Ok(())
    }

    /// Fails with a description of what already uses `name` on `target`. Relations declared by
    /// `source.field` itself (an earlier definition of the same model) do not count. `pending` is a
    /// model being registered whose fields are not in the registry yet.
    fn check_reverse_name(
        &self,
        target: &ModelMeta,
        name: &str,
        source: &str,
        field: &str,
        pending: Option<&Arc<ModelMeta>>,
    ) -> Result<(), String> {
        if let Some(index) = target.field(name) {
            return Err(format!("field '{}'", target.fields[index].name));
        }
        if self.forward_many_to_many(&target.qualname, name).is_some() {
            return Err(format!("many-to-many '{name}'"));
        }
        let same =
            |other_source: &str, other_field: &str| other_source == source && other_field == field;
        for model in self.all() {
            if model.qualname == source && pending.is_some() {
                continue; // replaced by the pending definition
            }
            for other in &model.fields {
                if let Some(relation) = &other.relation
                    && relation.target == target.qualname
                    && reverse_name(relation.related_name.as_deref(), &model.label).as_deref()
                        == Some(name)
                    && !same(&model.qualname, &other.name)
                {
                    return Err(format!("the reverse of {}.{}", model.label, other.name));
                }
            }
        }
        if let Some(pending) = pending {
            for other in &pending.fields {
                if let Some(relation) = &other.relation
                    && relation.target == target.qualname
                    && reverse_name(relation.related_name.as_deref(), &pending.label).as_deref()
                        == Some(name)
                    && other.name != field
                {
                    return Err(format!("the reverse of {}.{}", pending.label, other.name));
                }
            }
        }
        for relation in &self.many_to_many {
            if relation.target == target.qualname
                && !same(&relation.source, &relation.name)
                && let Some(source_meta) = self.get(&relation.source)
                && relation.reverse_name(&source_meta.label).as_deref() == Some(name)
            {
                return Err(format!(
                    "the reverse of {}.{}",
                    source_meta.label, relation.name
                ));
            }
        }
        Ok(())
    }

    fn existing(&self, qualname: &str) -> Result<Arc<ModelMeta>, OrmError> {
        self.get(qualname)
            .ok_or_else(|| OrmError(format!("model '{qualname}' is not registered")))
    }

    pub fn get(&self, qualname: &str) -> Option<Arc<ModelMeta>> {
        self.models.get(qualname).cloned()
    }

    pub fn by_table(&self, table: &str) -> Option<Arc<ModelMeta>> {
        self.tables.get(table).and_then(|q| self.get(q))
    }

    /// Registered models in first-registration order.
    pub fn all(&self) -> Vec<Arc<ModelMeta>> {
        self.order.iter().filter_map(|q| self.get(q)).collect()
    }

    /// The many-to-many relation `qualname.name`, declared on that model.
    pub fn forward_many_to_many(&self, qualname: &str, name: &str) -> Option<&ManyToMany> {
        self.many_to_many
            .iter()
            .find(|m| m.source == qualname && m.name == name)
    }

    pub fn many_to_many(&self) -> &[ManyToMany] {
        &self.many_to_many
    }

    /// The relation reaching `qualname` under the reverse name `name`.
    pub fn reverse(&self, qualname: &str, name: &str) -> Option<Reverse> {
        for model in self.all() {
            for (index, field) in model.fields.iter().enumerate() {
                if let Some(relation) = &field.relation
                    && relation.target == qualname
                    && reverse_name(relation.related_name.as_deref(), &model.label).as_deref()
                        == Some(name)
                {
                    return Some(Reverse::Field {
                        source: model.clone(),
                        field: index,
                        one_to_one: relation.one_to_one,
                    });
                }
            }
        }
        self.many_to_many.iter().find_map(|relation| {
            let source = self.get(&relation.source)?;
            (relation.target == qualname
                && relation.reverse_name(&source.label).as_deref() == Some(name))
            .then(|| Reverse::ManyToMany(relation.clone()))
        })
    }

    /// Names usable in lookups on `qualname` besides its fields (for error messages).
    pub fn relation_names(&self, qualname: &str) -> Vec<String> {
        let mut names: Vec<String> = self
            .many_to_many
            .iter()
            .filter(|m| m.source == qualname)
            .map(|m| m.name.clone())
            .collect();
        for model in self.all() {
            for field in &model.fields {
                if let Some(relation) = &field.relation
                    && relation.target == qualname
                    && let Some(name) = reverse_name(relation.related_name.as_deref(), &model.label)
                {
                    names.push(name);
                }
            }
        }
        for relation in self.many_to_many.iter().filter(|m| m.target == qualname) {
            if let Some(source) = self.get(&relation.source)
                && let Some(name) = relation.reverse_name(&source.label)
            {
                names.push(name);
            }
        }
        names
    }
}
