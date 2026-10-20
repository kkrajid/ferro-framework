//! Native ORM surface (contracts/rust-python-orm.md): model registration, relations, row
//! operations and schema helpers. Queries live in [`crate::orm_query`]. Lookup parsing, value
//! conversion, SQL compilation and execution all happen here; Python only wraps these objects.

use std::collections::HashMap;
use std::sync::{Arc, LazyLock, Mutex};

use ferro_database::{Job, JobResult, Outcome, Rows, Statement, Value};
use ferro_orm::{
    FieldKind, FieldMeta, FieldSpec, ManyToMany, ModelMeta, OnDelete, OrmError, Query, Registry,
    RelationSpec, compile, ddl,
};
use pyo3::IntoPyObjectExt;
use pyo3::exceptions::PyRuntimeError;
use pyo3::intern;
use pyo3::prelude::*;
use pyo3::types::{PyDict, PyList, PyString};

use crate::db::{current, submit};
use crate::orm_query::PyQuery;
use crate::orm_values::{
    Mode, db_error, delete_error, field_error, from_value, orm_error, orm_types, to_value,
};

/// The registry is replaced, never mutated in place: readers take a cheap snapshot and may call
/// Python code while holding it without blocking registrations (or deadlocking on the GIL).
static REGISTRY: LazyLock<Mutex<Arc<Registry>>> = LazyLock::new(Default::default);

/// Python classes of registered models, for building related instances.
static CLASSES: LazyLock<Mutex<HashMap<String, Py<PyAny>>>> = LazyLock::new(Default::default);

pub(crate) fn registry() -> Arc<Registry> {
    REGISTRY.lock().unwrap_or_else(|e| e.into_inner()).clone()
}

fn update_registry<T>(
    change: impl FnOnce(&mut Registry) -> Result<T, OrmError>,
) -> Result<T, OrmError> {
    let mut guard = REGISTRY.lock().unwrap_or_else(|e| e.into_inner());
    let mut next = (**guard).clone();
    let result = change(&mut next)?;
    *guard = Arc::new(next);
    Ok(result)
}

pub(crate) fn model_class(py: Python<'_>, qualname: &str) -> Option<Py<PyAny>> {
    CLASSES
        .lock()
        .unwrap_or_else(|e| e.into_inner())
        .get(qualname)
        .map(|cls| cls.clone_ref(py))
}

#[pyclass(module = "ferro._ferro", name = "ModelMeta", frozen)]
pub(crate) struct PyModelMeta {
    pub(crate) meta: Arc<ModelMeta>,
    cls: Py<PyAny>,
}

#[pymethods]
impl PyModelMeta {
    #[getter]
    fn qualname(&self) -> &str {
        &self.meta.qualname
    }

    #[getter]
    fn label(&self) -> &str {
        &self.meta.label
    }

    #[getter]
    fn table(&self) -> &str {
        &self.meta.table
    }

    #[getter]
    fn pk_name(&self) -> &str {
        &self.meta.pk_field().name
    }

    #[getter]
    fn auto_pk(&self) -> bool {
        self.meta.pk_field().auto
    }

    #[getter]
    fn field_names(&self) -> Vec<String> {
        self.meta.fields.iter().map(|f| f.name.clone()).collect()
    }

    /// Instance attributes holding stored values (`category_id` for relations).
    #[getter]
    fn attnames(&self) -> Vec<String> {
        self.meta.fields.iter().map(|f| f.attname.clone()).collect()
    }

    fn query(&self, py: Python<'_>) -> PyQuery {
        PyQuery::new(Query::new(self.meta.clone()), self.cls.clone_ref(py))
    }

    fn __repr__(&self) -> String {
        format!(
            "<ModelMeta {} table={}>",
            self.meta.qualname, self.meta.table
        )
    }
}

pub(crate) fn optional<'py, T: for<'a> FromPyObject<'a, 'py>>(
    dict: &Bound<'py, PyDict>,
    key: &str,
) -> PyResult<Option<T>> {
    match dict.get_item(key)? {
        Some(value) if !value.is_none() => Ok(Some(value.extract().map_err(Into::into)?)),
        _ => Ok(None),
    }
}

/// Field kind from its Python name plus options (`max_length`, `max_digits`, ...).
pub(crate) fn kind_from_dict(
    py: Python<'_>,
    context: &str,
    dict: &Bound<'_, PyDict>,
) -> PyResult<FieldKind> {
    let kind: String = optional(dict, "kind")?
        .ok_or_else(|| field_error(py, format!("{context}: field without a kind")))?;
    let need = |option: &str| field_error(py, format!("{context}: {kind} needs {option}"));
    Ok(match kind.as_str() {
        "String" => FieldKind::String {
            max_length: optional(dict, "max_length")?.ok_or_else(|| need("max_length"))?,
        },
        "Text" => FieldKind::Text,
        "Integer" => FieldKind::Integer,
        "BigInteger" => FieldKind::BigInteger,
        "Float" => FieldKind::Float,
        "Decimal" => FieldKind::Decimal {
            max_digits: optional(dict, "max_digits")?.ok_or_else(|| need("max_digits"))?,
            decimal_places: optional(dict, "decimal_places")?
                .ok_or_else(|| need("decimal_places"))?,
        },
        "Boolean" => FieldKind::Boolean,
        "Date" => FieldKind::Date,
        "DateTime" => FieldKind::DateTime,
        "Time" => FieldKind::Time,
        "UUID" => FieldKind::Uuid,
        "JSON" => FieldKind::Json,
        other => {
            return Err(field_error(
                py,
                format!("{context}: unknown field kind '{other}'"),
            ));
        }
    })
}

/// Writes a field kind back as its Python name plus options.
pub(crate) fn kind_into_dict(kind: &FieldKind, dict: &Bound<'_, PyDict>) -> PyResult<()> {
    let name = match kind {
        FieldKind::String { max_length } => {
            dict.set_item("max_length", max_length)?;
            "String"
        }
        FieldKind::Text => "Text",
        FieldKind::Integer => "Integer",
        FieldKind::BigInteger => "BigInteger",
        FieldKind::Float => "Float",
        FieldKind::Decimal {
            max_digits,
            decimal_places,
        } => {
            dict.set_item("max_digits", max_digits)?;
            dict.set_item("decimal_places", decimal_places)?;
            "Decimal"
        }
        FieldKind::Boolean => "Boolean",
        FieldKind::Date => "Date",
        FieldKind::DateTime => "DateTime",
        FieldKind::Time => "Time",
        FieldKind::Uuid => "UUID",
        FieldKind::Json => "JSON",
    };
    dict.set_item("kind", name)
}

pub(crate) fn on_delete(py: Python<'_>, context: &str, name: &str) -> PyResult<OnDelete> {
    OnDelete::parse(name).ok_or_else(|| {
        let names: Vec<&str> = OnDelete::ALL.iter().map(|o| o.name()).collect();
        field_error(
            py,
            format!(
                "{context}: unknown on_delete '{name}'; use one of {}",
                names.join(", ")
            ),
        )
    })
}

fn field_spec(py: Python<'_>, label: &str, dict: &Bound<'_, PyDict>) -> PyResult<FieldSpec> {
    let name: String = optional(dict, "name")?
        .ok_or_else(|| field_error(py, format!("{label}: field without a name")))?;
    let context = format!("{label}.{name}");
    let kind: Option<String> = optional(dict, "kind")?;
    let mut spec = match kind.as_deref() {
        Some(relation @ ("ForeignKey" | "OneToOne")) => {
            let target: String = optional(dict, "target")?
                .ok_or_else(|| field_error(py, format!("{context}: relation without a target")))?;
            let rule: String = optional(dict, "on_delete")?
                .ok_or_else(|| field_error(py, format!("{context}: on_delete is required")))?;
            let mut spec = FieldSpec::new(name, FieldKind::BigInteger);
            spec.relation = Some(RelationSpec {
                target,
                one_to_one: relation == "OneToOne",
                on_delete: on_delete(py, &context, &rule)?,
                related_name: optional(dict, "related_name")?,
            });
            spec
        }
        _ => FieldSpec::new(name, kind_from_dict(py, &context, dict)?),
    };
    spec.column = optional(dict, "column")?;
    spec.null = optional(dict, "null")?.unwrap_or(false);
    spec.unique = optional(dict, "unique")?.unwrap_or(false);
    spec.index = optional(dict, "index")?.unwrap_or(false);
    spec.primary_key = optional(dict, "primary_key")?.unwrap_or(false);
    Ok(spec)
}

/// Registered models, all or the named ones (used by migrations).
pub(crate) fn registered_metas(
    py: Python<'_>,
    qualnames: Option<Vec<String>>,
) -> PyResult<Vec<Arc<ModelMeta>>> {
    let registry = registry();
    match qualnames {
        None => Ok(registry.all()),
        Some(names) => names
            .iter()
            .map(|name| {
                registry
                    .get(name)
                    .ok_or_else(|| field_error(py, format!("model '{name}' is not registered")))
            })
            .collect(),
    }
}

/// Validates and registers a model; returns its native metadata. Relation targets must already
/// be registered (Python defers models whose targets are not defined yet).
#[pyfunction]
#[pyo3(signature = (cls, qualname, label, table, fields, unique_together=None))]
pub(crate) fn register_model(
    py: Python<'_>,
    cls: Bound<'_, PyAny>,
    qualname: &str,
    label: &str,
    table: &str,
    fields: Vec<Bound<'_, PyDict>>,
    unique_together: Option<Vec<Vec<String>>>,
) -> PyResult<PyModelMeta> {
    let specs = fields
        .iter()
        .map(|dict| field_spec(py, label, dict))
        .collect::<PyResult<Vec<_>>>()?;
    let snapshot = registry();
    let resolve = |q: &str| snapshot.get(q);
    let meta = ModelMeta::build(
        qualname,
        label,
        table,
        specs,
        &unique_together.unwrap_or_default(),
        &resolve,
    )
    .map_err(|e| orm_error(py, e))?;
    let meta = update_registry(|registry| registry.register(meta)).map_err(|e| orm_error(py, e))?;
    CLASSES
        .lock()
        .unwrap_or_else(|e| e.into_inner())
        .insert(meta.qualname.clone(), cls.clone().unbind());
    Ok(PyModelMeta {
        meta,
        cls: cls.unbind(),
    })
}

/// Records `source.name` as a many-to-many relation to `target` through the link model.
#[pyfunction]
#[pyo3(signature = (source, name, target, through, from_field, to_field, related_name=None))]
#[allow(clippy::too_many_arguments)]
pub(crate) fn register_many_to_many(
    py: Python<'_>,
    source: PyRef<'_, PyModelMeta>,
    name: String,
    target: PyRef<'_, PyModelMeta>,
    through: PyRef<'_, PyModelMeta>,
    from_field: &str,
    to_field: &str,
    related_name: Option<String>,
) -> PyResult<()> {
    let field = |field_name: &str| {
        through.meta.field(field_name).ok_or_else(|| {
            field_error(
                py,
                format!("{} has no field '{field_name}'", through.meta.label),
            )
        })
    };
    let relation = ManyToMany {
        source: source.meta.qualname.clone(),
        name,
        target: target.meta.qualname.clone(),
        through: through.meta.qualname.clone(),
        from_field: field(from_field)?,
        to_field: field(to_field)?,
        related_name,
    };
    update_registry(|registry| registry.register_many_to_many(relation))
        .map_err(|e| orm_error(py, e))
}

/// `(qualname, table)` of registered models, all or the named ones.
#[pyfunction]
#[pyo3(signature = (qualnames=None))]
pub(crate) fn registered_models(
    py: Python<'_>,
    qualnames: Option<Vec<String>>,
) -> PyResult<Vec<(String, String)>> {
    Ok(registered_metas(py, qualnames)?
        .iter()
        .map(|m| (m.qualname.clone(), m.table.clone()))
        .collect())
}

pub(crate) fn rows(py: Python<'_>, result: JobResult, meta: Option<&ModelMeta>) -> PyResult<Rows> {
    match result {
        Ok(Outcome::Rows(rows)) => Ok(rows),
        Ok(other) => Err(PyRuntimeError::new_err(format!(
            "unexpected database outcome {other:?}"
        ))),
        Err(err) => Err(db_error(py, err, meta)),
    }
}

pub(crate) fn affected(
    py: Python<'_>,
    result: JobResult,
    meta: Option<&ModelMeta>,
) -> PyResult<u64> {
    match result {
        Ok(Outcome::Affected(n)) => Ok(n),
        Ok(Outcome::Batch { affected, .. }) => Ok(affected.iter().sum()),
        Ok(other) => Err(PyRuntimeError::new_err(format!(
            "unexpected database outcome {other:?}"
        ))),
        Err(err) => Err(db_error(py, err, meta)),
    }
}

pub(crate) fn to_py<'py, T: IntoPyObject<'py>>(py: Python<'py>, value: T) -> PyResult<Py<PyAny>> {
    value.into_py_any(py)
}

/// Builds model instances without running Python code per row: `cls.__new__(cls)` plus direct
/// `__dict__` population (research R10). Takes `meta.fields.len()` values from `cells`.
pub(crate) struct InstanceBuilder<'py> {
    new: Bound<'py, PyAny>,
    cls: Bound<'py, PyAny>,
    names: Vec<Bound<'py, PyString>>,
    meta: Arc<ModelMeta>,
}

impl<'py> InstanceBuilder<'py> {
    pub(crate) fn new(
        py: Python<'py>,
        cls: Bound<'py, PyAny>,
        meta: Arc<ModelMeta>,
    ) -> PyResult<Self> {
        Ok(Self {
            new: cls.getattr(intern!(py, "__new__"))?,
            names: meta
                .fields
                .iter()
                .map(|f| PyString::intern(py, &f.attname))
                .collect(),
            cls,
            meta,
        })
    }

    pub(crate) fn build(
        &self,
        py: Python<'py>,
        cells: &mut impl Iterator<Item = Value>,
    ) -> PyResult<Bound<'py, PyAny>> {
        let instance = self.new.call1((&self.cls,))?;
        let dict = instance
            .getattr(intern!(py, "__dict__"))?
            .cast_into::<PyDict>()?;
        for (field, name) in self.meta.fields.iter().zip(&self.names) {
            let value = cells.next().unwrap_or(Value::Null);
            dict.set_item(name, from_value(py, field, value)?)?;
        }
        dict.set_item(intern!(py, "_persisted"), true)?;
        Ok(instance)
    }

    /// Like [`build`](Self::build), but `None` when the row's primary key is NULL (no related
    /// row in a LEFT JOIN). Always consumes the model's cells.
    pub(crate) fn build_optional(
        &self,
        py: Python<'py>,
        cells: &mut impl Iterator<Item = Value>,
    ) -> PyResult<Option<Bound<'py, PyAny>>> {
        let values: Vec<Value> = cells.take(self.meta.fields.len()).collect();
        if values.get(self.meta.pk).is_none_or(|v| *v == Value::Null) {
            return Ok(None);
        }
        self.build(py, &mut values.into_iter()).map(Some)
    }
}

pub(crate) fn build_instances<'py>(
    py: Python<'py>,
    cls: &Bound<'py, PyAny>,
    meta: &Arc<ModelMeta>,
    rows: Rows,
) -> PyResult<Bound<'py, PyList>> {
    let builder = InstanceBuilder::new(py, cls.clone(), meta.clone())?;
    let list = PyList::empty(py);
    for row in rows.rows {
        list.append(builder.build(py, &mut row.into_iter())?)?;
    }
    Ok(list)
}

/// A model instance given where a key is expected becomes its primary key. `expected` is the
/// qualname of the model whose key the field holds.
pub(crate) fn unwrap_instance<'py>(
    label: &str,
    field: &FieldMeta,
    expected: Option<&str>,
    value: &Bound<'py, PyAny>,
) -> PyResult<Bound<'py, PyAny>> {
    let py = value.py();
    if !value.is_instance(orm_types(py)?.model_base.bind(py))? {
        return Ok(value.clone());
    }
    let fail = |problem: String| field_error(py, format!("{label}.{}: {problem}", field.name));
    let meta = value.getattr(intern!(py, "_meta"))?;
    let qualname: String = meta.getattr(intern!(py, "qualname"))?.extract()?;
    let given: String = meta.getattr(intern!(py, "label"))?.extract()?;
    match expected {
        Some(expected) if expected == qualname => {}
        Some(expected) => {
            let wanted = expected.rsplit('.').next().unwrap_or(expected);
            return Err(fail(format!(
                "expected a {wanted} instance, got a {given} instance"
            )));
        }
        None => return Err(fail(format!("expected a value, got a {given} instance"))),
    }
    let pk = value.getattr(intern!(py, "pk"))?;
    if pk.is_none() {
        return Err(fail(format!(
            "the {given} instance is not saved yet (it has no primary key)"
        )));
    }
    Ok(pk)
}

/// The model whose key `field` stores, for [`unwrap_instance`]: the relation target, or `owner`
/// itself for its primary key.
pub(crate) fn key_model<'a>(owner: &'a ModelMeta, field: &'a FieldMeta) -> Option<&'a str> {
    match &field.relation {
        Some(relation) => Some(&relation.target),
        None if field.primary_key => Some(&owner.qualname),
        None => None,
    }
}

/// A value to store in `field` of `meta`, accepting model instances for relations.
pub(crate) fn store_value(
    meta: &ModelMeta,
    field: &FieldMeta,
    value: &Bound<'_, PyAny>,
) -> PyResult<Value> {
    let value = unwrap_instance(&meta.label, field, key_model(meta, field), value)?;
    to_value(&meta.label, field, &value, Mode::Store)
}

pub(crate) fn instance_dict<'py>(instance: &Bound<'py, PyAny>) -> PyResult<Bound<'py, PyDict>> {
    Ok(instance
        .getattr(intern!(instance.py(), "__dict__"))?
        .cast_into::<PyDict>()?)
}

fn field_value<'py>(dict: &Bound<'py, PyDict>, name: &str) -> PyResult<Bound<'py, PyAny>> {
    Ok(dict
        .get_item(name)?
        .unwrap_or_else(|| dict.py().None().into_bound(dict.py())))
}

/// Values to insert for one instance: every field except an unset automatic primary key.
fn insert_values(meta: &ModelMeta, instance: &Bound<'_, PyAny>) -> PyResult<Vec<(usize, Value)>> {
    let dict = instance_dict(instance)?;
    let mut values = Vec::with_capacity(meta.fields.len());
    for (index, field) in meta.fields.iter().enumerate() {
        let value = field_value(&dict, &field.attname)?;
        if field.auto && value.is_none() {
            continue;
        }
        values.push((index, to_value(&meta.label, field, &value, Mode::Store)?));
    }
    Ok(values)
}

fn pk_value(meta: &ModelMeta, instance: &Bound<'_, PyAny>) -> PyResult<Value> {
    let dict = instance_dict(instance)?;
    let pk = meta.pk_field();
    let value = field_value(&dict, &pk.attname)?;
    if value.is_none() {
        return Err(field_error(
            instance.py(),
            format!(
                "{} instance has no primary key value; save it first",
                meta.label
            ),
        ));
    }
    to_value(&meta.label, pk, &value, Mode::Store)
}

fn mark_saved(
    py: Python<'_>,
    meta: &ModelMeta,
    instance: &Bound<'_, PyAny>,
    pk: Option<Value>,
) -> PyResult<()> {
    let dict = instance_dict(instance)?;
    if let Some(pk) = pk {
        dict.set_item(
            &meta.pk_field().attname,
            from_value(py, meta.pk_field(), pk)?,
        )?;
    }
    dict.set_item(intern!(py, "_persisted"), true)?;
    Ok(())
}

/// INSERT one instance; sets its primary key and marks it persisted.
#[pyfunction]
pub(crate) fn insert<'py>(
    py: Python<'py>,
    meta: PyRef<'py, PyModelMeta>,
    instance: Bound<'py, PyAny>,
) -> PyResult<Bound<'py, PyAny>> {
    let meta = meta.meta.clone();
    let values = insert_values(&meta, &instance)?;
    let database = current(py)?;
    let statement = compile::insert(database.dialect, &meta, &values, true);
    let instance = instance.unbind();
    // An explicit value for an automatic key: keep the sequence ahead of it (PostgreSQL).
    let explicit_key = values.iter().any(|(i, _)| *i == meta.pk);
    if explicit_key && let Some(sync) = compile::sync_sequence(database.dialect, &meta) {
        return submit(
            py,
            &database,
            Job::Batch {
                statements: vec![statement, sync],
                returning: true,
            },
            Box::new(move |py, result| match result {
                Ok(Outcome::Batch { returned, .. }) => {
                    let pk = returned.into_iter().next().flatten();
                    mark_saved(py, &meta, instance.bind(py), pk)?;
                    Ok(py.None())
                }
                Ok(other) => Err(PyRuntimeError::new_err(format!(
                    "unexpected database outcome {other:?}"
                ))),
                Err(err) => Err(db_error(py, err, Some(&meta))),
            }),
        );
    }
    submit(
        py,
        &database,
        Job::Query(statement),
        Box::new(move |py, result| {
            let rows = rows(py, result, Some(&meta))?;
            let pk = rows
                .rows
                .into_iter()
                .next()
                .and_then(|r| r.into_iter().next());
            mark_saved(py, &meta, instance.bind(py), pk)?;
            Ok(py.None())
        }),
    )
}

/// Moves every automatic primary key sequence up to its table's highest key (PostgreSQL;
/// `ferro db sequence-reset`). Resolves to the tables checked; nothing to do on SQLite.
#[pyfunction]
#[pyo3(signature = (qualnames=None))]
pub(crate) fn reset_sequences<'py>(
    py: Python<'py>,
    qualnames: Option<Vec<String>>,
) -> PyResult<Bound<'py, PyAny>> {
    let database = current(py)?;
    let mut tables = Vec::new();
    let mut statements = Vec::new();
    for meta in registered_metas(py, qualnames)? {
        if let Some(sync) = compile::sync_sequence(database.dialect, &meta) {
            tables.push(meta.table.clone());
            statements.push(sync);
        }
    }
    submit(
        py,
        &database,
        Job::Batch {
            statements,
            returning: true,
        },
        Box::new(move |py, result| match result {
            Ok(Outcome::Batch { returned, .. }) => {
                let moved: Vec<&String> = tables
                    .iter()
                    .zip(returned)
                    .filter(|(_, value)| value.is_some())
                    .map(|(table, _)| table)
                    .collect();
                Ok((tables.clone(), moved)
                    .into_pyobject(py)?
                    .into_any()
                    .unbind())
            }
            Ok(other) => Err(PyRuntimeError::new_err(format!(
                "unexpected database outcome {other:?}"
            ))),
            Err(err) => Err(db_error(py, err, None)),
        }),
    )
}

/// UPDATE every non-key field of one instance; resolves to the number of rows matched.
#[pyfunction]
pub(crate) fn update_instance<'py>(
    py: Python<'py>,
    meta: PyRef<'py, PyModelMeta>,
    instance: Bound<'py, PyAny>,
) -> PyResult<Bound<'py, PyAny>> {
    let meta = meta.meta.clone();
    let pk = pk_value(&meta, &instance)?;
    let dict = instance_dict(&instance)?;
    let mut values = Vec::with_capacity(meta.fields.len());
    for (index, field) in meta.fields.iter().enumerate() {
        if index != meta.pk {
            values.push((
                index,
                to_value(
                    &meta.label,
                    field,
                    &field_value(&dict, &field.attname)?,
                    Mode::Store,
                )?,
            ));
        }
    }
    let database = current(py)?;
    let (job, counts_rows) = if values.is_empty() {
        let query = Query::new(meta.clone())
            .filter(false, vec![("pk", ferro_orm::Operand::Value(pk))])
            .map_err(|e| orm_error(py, e))?;
        (Job::Query(compile::exists(database.dialect, &query)), true)
    } else {
        (
            Job::Execute(compile::update_row(database.dialect, &meta, &values, pk)),
            false,
        )
    };
    submit(
        py,
        &database,
        job,
        Box::new(move |py, result| {
            if counts_rows {
                to_py(py, rows(py, result, Some(&meta))?.rows.len())
            } else {
                to_py(py, affected(py, result, Some(&meta))?)
            }
        }),
    )
}

/// DELETE one instance by primary key; resolves to the number of rows deleted.
#[pyfunction]
pub(crate) fn delete_instance<'py>(
    py: Python<'py>,
    meta: PyRef<'py, PyModelMeta>,
    instance: Bound<'py, PyAny>,
) -> PyResult<Bound<'py, PyAny>> {
    let meta = meta.meta.clone();
    let pk = pk_value(&meta, &instance)?;
    let database = current(py)?;
    let statement = compile::delete_row(database.dialect, &meta, pk);
    submit(
        py,
        &database,
        Job::Execute(statement),
        Box::new(move |py, result| match result {
            Ok(Outcome::Affected(n)) => to_py(py, n),
            Ok(other) => Err(PyRuntimeError::new_err(format!(
                "unexpected database outcome {other:?}"
            ))),
            Err(err) => Err(delete_error(py, err, &meta)),
        }),
    )
}

/// Inserts all instances in one transaction and assigns automatic primary keys. With
/// `ignore_conflicts`, rows violating a unique constraint are skipped (their key stays unset).
#[pyfunction]
#[pyo3(signature = (meta, instances, batch_size=None, ignore_conflicts=false))]
pub(crate) fn bulk_insert<'py>(
    py: Python<'py>,
    meta: PyRef<'py, PyModelMeta>,
    instances: Vec<Bound<'py, PyAny>>,
    batch_size: Option<usize>,
    ignore_conflicts: bool,
) -> PyResult<Bound<'py, PyAny>> {
    let _ = batch_size; // one transaction per call; row statements are cached and cheap (R7)
    let meta = meta.meta.clone();
    let database = current(py)?;
    let mut statements: Vec<Statement> = Vec::with_capacity(instances.len());
    let mut auto_needed = Vec::with_capacity(instances.len());
    for instance in &instances {
        let values = insert_values(&meta, instance)?;
        auto_needed.push(meta.pk_field().auto && !values.iter().any(|(i, _)| *i == meta.pk));
        statements.push(compile::insert_with(
            database.dialect,
            &meta,
            &values,
            true,
            ignore_conflicts,
        ));
    }
    if meta.pk_field().auto
        && auto_needed.iter().any(|needed| !needed)
        && let Some(sync) = compile::sync_sequence(database.dialect, &meta)
    {
        statements.push(sync);
    }
    let instances: Vec<Py<PyAny>> = instances.into_iter().map(Bound::unbind).collect();
    submit(
        py,
        &database,
        Job::Batch {
            statements,
            returning: true,
        },
        Box::new(move |py, result| {
            let (returned, affected) = match result {
                Ok(Outcome::Batch { returned, affected }) => (returned, affected),
                Ok(other) => {
                    return Err(PyRuntimeError::new_err(format!(
                        "unexpected database outcome {other:?}"
                    )));
                }
                Err(err) => return Err(db_error(py, err, Some(&meta))),
            };
            for (((instance, pk), needs_pk), inserted) in instances
                .iter()
                .zip(returned)
                .zip(auto_needed)
                .zip(affected)
            {
                if inserted > 0 {
                    mark_saved(py, &meta, instance.bind(py), pk.filter(|_| needs_pk))?;
                }
            }
            Ok(py.None())
        }),
    )
}

/// Updates the listed fields of every instance in one transaction; resolves to rows updated.
#[pyfunction]
#[pyo3(signature = (meta, instances, fields, batch_size=None))]
pub(crate) fn bulk_update<'py>(
    py: Python<'py>,
    meta: PyRef<'py, PyModelMeta>,
    instances: Vec<Bound<'py, PyAny>>,
    fields: Vec<String>,
    batch_size: Option<usize>,
) -> PyResult<Bound<'py, PyAny>> {
    let _ = batch_size;
    let meta = meta.meta.clone();
    if fields.is_empty() {
        return Err(field_error(
            py,
            format!("{}: bulk_update() needs at least one field", meta.label),
        ));
    }
    let mut indexes = Vec::with_capacity(fields.len());
    for name in &fields {
        let index = meta.field(name).filter(|i| *i != meta.pk).ok_or_else(|| {
            field_error(
                py,
                format!("{}: bulk_update() cannot update field '{name}'", meta.label),
            )
        })?;
        indexes.push(index);
    }
    let database = current(py)?;
    let mut statements = Vec::with_capacity(instances.len());
    for instance in &instances {
        let pk = pk_value(&meta, instance)?;
        let dict = instance_dict(instance)?;
        let mut values = Vec::with_capacity(indexes.len());
        for &index in &indexes {
            let field = &meta.fields[index];
            values.push((
                index,
                to_value(
                    &meta.label,
                    field,
                    &field_value(&dict, &field.attname)?,
                    Mode::Store,
                )?,
            ));
        }
        statements.push(compile::update_row(database.dialect, &meta, &values, pk));
    }
    submit(
        py,
        &database,
        Job::Batch {
            statements,
            returning: false,
        },
        Box::new(move |py, result| to_py(py, affected(py, result, Some(&meta))?)),
    )
}

/// Names of the tables that exist in the database.
#[pyfunction]
pub(crate) fn list_tables<'py>(py: Python<'py>) -> PyResult<Bound<'py, PyAny>> {
    let database = current(py)?;
    submit(
        py,
        &database,
        Job::ListTables,
        Box::new(|py, result| match result {
            Ok(Outcome::Tables(tables)) => to_py(py, tables),
            Ok(other) => Err(PyRuntimeError::new_err(format!(
                "unexpected database outcome {other:?}"
            ))),
            Err(err) => Err(db_error(py, err, None)),
        }),
    )
}

/// Creates the tables and indexes of the named registered models in one transaction, each
/// after the tables it references.
#[pyfunction]
pub(crate) fn create_tables<'py>(
    py: Python<'py>,
    qualnames: Vec<String>,
) -> PyResult<Bound<'py, PyAny>> {
    let database = current(py)?;
    let metas = registered_metas(py, Some(qualnames))?;
    let ordered = ddl::creation_order(&metas).map_err(|e| orm_error(py, e))?;
    let statements = ordered
        .iter()
        .flat_map(|meta| ddl::create_table(database.dialect, meta))
        .collect();
    submit(
        py,
        &database,
        Job::Schema(statements),
        Box::new(|py, result| {
            affected(py, result, None)?;
            Ok(py.None())
        }),
    )
}
