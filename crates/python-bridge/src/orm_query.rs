//! The native `Query` object behind `QuerySet` (Feature 002, extended by Feature 005): lookups
//! across relations, `Q` trees, `F` expressions, `select_related`, annotations, `values()` and
//! `aggregate()`.

use std::sync::Arc;

use ferro_database::{Job, Value};
use ferro_orm::compile::{self, Assignment};
use ferro_orm::{
    AggFunc, Aggregate, ArithOp, Condition, Expr, FieldKind, FieldMeta, Lock, Lookup, ModelMeta,
    Node, Operand, Query, Registry, Selected, Shape, Target, Transform, resolve_lookup_key,
};
use pyo3::exceptions::PyRuntimeError;
use pyo3::intern;
use pyo3::prelude::*;
use pyo3::types::{PyBool, PyDict, PyFloat, PyInt, PyList, PyString, PyTuple};

use crate::db::{self, Converter, current, submit};
use crate::orm::{
    InstanceBuilder, affected, build_instances, instance_dict, key_model, model_class, registry,
    rows, store_value, to_py, unwrap_instance,
};
use crate::orm_values::{
    Mode, delete_error, field_error, from_value, orm_error, orm_types, raw, to_value,
};

#[pyclass(module = "ferro._ferro", name = "Query", frozen)]
pub(crate) struct PyQuery {
    query: Query,
    cls: Py<PyAny>,
}

impl PyQuery {
    pub(crate) fn new(query: Query, cls: Py<PyAny>) -> Self {
        Self { query, cls }
    }

    fn derive(&self, py: Python<'_>, query: Query) -> Self {
        Self {
            query,
            cls: self.cls.clone_ref(py),
        }
    }

    fn meta(&self) -> Arc<ModelMeta> {
        self.query.model.clone()
    }

    /// Refuses a query that uses `OuterRef` without being inside another query.
    fn check_outer(&self, py: Python<'_>) -> PyResult<()> {
        match self.query.unresolved_outer() {
            None => Ok(()),
            Some(name) => Err(field_error(
                py,
                format!(
                    "{}: OuterRef('{name}') can only be used in a queryset passed to \
                     Subquery() or Exists()",
                    self.query.model.label
                ),
            )),
        }
    }

    /// `count()`, `exists()` and `aggregate()` read no rows to lock.
    fn refuse_lock(&self, py: Python<'_>, method: &str) -> PyResult<()> {
        if self.query.is_locking() {
            return Err(field_error(
                py,
                format!(
                    "{}: {method}() cannot be used on a select_for_update() query; it locks the                      rows it fetches, so fetch them (`await qs`, `get()`, `first()`)",
                    self.query.model.label
                ),
            ));
        }
        Ok(())
    }
}

fn is_numeric(kind: &FieldKind) -> bool {
    matches!(
        kind,
        FieldKind::Integer | FieldKind::BigInteger | FieldKind::Float | FieldKind::Decimal { .. }
    )
}

/// A constant inside an `F()` expression: plain numbers keep their type, anything else is
/// converted like a value of `field`.
fn constant(label: &str, field: &FieldMeta, value: &Bound<'_, PyAny>) -> PyResult<Value> {
    let py = value.py();
    if !value.is_instance_of::<PyBool>() {
        if let Ok(int) = value.cast::<PyInt>() {
            return Ok(Value::Integer(int.extract()?));
        }
        if let Ok(float) = value.cast::<PyFloat>() {
            return Ok(Value::Real(float.value()));
        }
        if value.is_instance(orm_types(py)?.decimal.bind(py))? {
            return Ok(Value::Decimal(value.str()?.to_string()));
        }
    }
    to_value(label, field, value, Mode::Compare)
}

/// Converts `F()` objects (anything with `_ferro_expr()`) to an expression over the query's own
/// fields; `field` is the field the expression is compared with or assigned to.
fn expr(query: &Query, field: &FieldMeta, value: &Bound<'_, PyAny>) -> PyResult<Option<Expr>> {
    let py = value.py();
    let Ok(method) = value.getattr(intern!(py, "_ferro_expr")) else {
        return Ok(None);
    };
    let tree = method.call0()?;
    let label = &query.model.label;
    fn convert(
        query: &Query,
        label: &str,
        field: &FieldMeta,
        tree: &Bound<'_, PyAny>,
    ) -> PyResult<Expr> {
        let py = tree.py();
        let tag: String = tree.get_item(0)?.extract()?;
        match tag.as_str() {
            "F" => {
                let name: String = tree.get_item(1)?.extract()?;
                if name.contains("__") {
                    return Err(field_error(
                        py,
                        format!("{label}: F('{name}') cannot follow relations (not supported)"),
                    ));
                }
                let index = query.model.field(&name).ok_or_else(|| {
                    field_error(
                        py,
                        format!(
                            "{label} has no field '{name}'; fields: {}",
                            query.model.field_names().join(", ")
                        ),
                    )
                })?;
                Ok(Expr::Field(index))
            }
            "V" => Ok(Expr::Value(constant(label, field, &tree.get_item(1)?)?)),
            "OUTER" => Ok(Expr::OuterName(tree.get_item(1)?.extract()?)),
            "OP" => {
                let symbol: String = tree.get_item(1)?.extract()?;
                let op = ArithOp::parse(&symbol).ok_or_else(|| {
                    field_error(py, format!("{label}: unknown operator '{symbol}'"))
                })?;
                let left = convert(query, label, field, &tree.get_item(2)?)?;
                let right = convert(query, label, field, &tree.get_item(3)?)?;
                let mut fields = Vec::new();
                left.fields(&mut fields);
                right.fields(&mut fields);
                for index in fields {
                    let used = &query.model.fields[index];
                    if !is_numeric(&used.kind) {
                        return Err(field_error(
                            py,
                            format!(
                                "{label}: arithmetic needs numeric fields; '{}' is {}",
                                used.name,
                                used.kind.name()
                            ),
                        ));
                    }
                }
                Ok(Expr::Binary(Box::new(left), op, Box::new(right)))
            }
            other => Err(field_error(
                py,
                format!("{label}: unknown expression '{other}'"),
            )),
        }
    }
    convert(query, label, field, &tree).map(Some)
}

/// A literal inside an annotation expression, typed by its Python type.
fn literal(value: &Bound<'_, PyAny>) -> PyResult<Expr> {
    let py = value.py();
    let types = orm_types(py)?;
    let kind = if value.is_none() {
        return Ok(Expr::Literal(Value::Null, FieldKind::Text));
    } else if value.is_instance_of::<PyBool>() {
        return Ok(Expr::Literal(
            Value::Integer(i64::from(value.is_truthy()?)),
            FieldKind::Boolean,
        ));
    } else if value.is_instance_of::<PyInt>() {
        FieldKind::BigInteger
    } else if value.is_instance_of::<PyFloat>() {
        FieldKind::Float
    } else if value.is_instance_of::<PyString>() {
        FieldKind::Text
    } else if value.is_instance(types.decimal.bind(py))? {
        let text = value.str()?.to_string();
        let places = text.split_once('.').map_or(0, |(_, f)| f.len());
        return Ok(Expr::Literal(
            Value::Decimal(text),
            FieldKind::Decimal {
                max_digits: 38,
                decimal_places: u8::try_from(places).unwrap_or(0),
            },
        ));
    } else if value.is_instance(types.datetime.bind(py))? {
        FieldKind::DateTime
    } else if value.is_instance(types.date.bind(py))? {
        FieldKind::Date
    } else if value.is_instance(types.time.bind(py))? {
        FieldKind::Time
    } else if value.is_instance(types.uuid.bind(py))? {
        FieldKind::Uuid
    } else {
        FieldKind::Json
    };
    let field = FieldMeta::column("value", kind.clone());
    Ok(Expr::Literal(
        to_value("Value()", &field, value, Mode::Compare)?,
        kind,
    ))
}

/// An annotation expression tree from Python (`F`, literals, arithmetic, functions, `Case`,
/// `Subquery`, `Exists`) over `query`'s model (Feature 022).
fn value_expr(
    py: Python<'_>,
    registry: &Registry,
    query: &Query,
    tree: &Bound<'_, PyAny>,
) -> PyResult<Expr> {
    let label = &query.model.label;
    if !tree.is_instance_of::<PyTuple>() {
        return literal(tree);
    }
    let tag: String = tree.get_item(0)?.extract()?;
    let item = |index: usize| tree.get_item(index);
    match tag.as_str() {
        "F" => {
            let name: String = item(1)?.extract()?;
            let index = if name == "pk" {
                Some(query.model.pk)
            } else {
                query.model.field(&name)
            };
            index.map(Expr::Field).ok_or_else(|| {
                field_error(
                    py,
                    format!(
                        "{label} has no field '{name}' (F() in expressions names the model's own                          fields); fields: {}",
                        query.model.field_names().join(", ")
                    ),
                )
            })
        }
        "V" | "L" => literal(&item(1)?),
        "OP" => {
            let symbol: String = item(1)?.extract()?;
            let op = ArithOp::parse(&symbol)
                .ok_or_else(|| field_error(py, format!("{label}: unknown operator '{symbol}'")))?;
            let left = value_expr(py, registry, query, &item(2)?)?;
            let right = value_expr(py, registry, query, &item(3)?)?;
            Ok(Expr::Binary(Box::new(left), op, Box::new(right)))
        }
        "FN" => {
            let name: String = item(1)?.extract()?;
            let func = ferro_orm::Func::parse(&name)
                .ok_or_else(|| field_error(py, format!("unknown function '{name}'")))?;
            let args: Vec<Expr> = item(2)?
                .try_iter()?
                .map(|arg| value_expr(py, registry, query, &arg?))
                .collect::<PyResult<_>>()?;
            let (low, high) = func.arity();
            if args.len() < low || args.len() > high {
                return Err(field_error(
                    py,
                    format!(
                        "{name}() takes {} argument(s), got {}",
                        if low == high {
                            low.to_string()
                        } else {
                            format!("at least {low}")
                        },
                        args.len()
                    ),
                ));
            }
            Ok(Expr::Func(func, args))
        }
        "CASE" => {
            let mut whens = Vec::new();
            for when in item(1)?.try_iter()? {
                let when = when?;
                let condition = when.get_item(0)?.cast_into::<PyTuple>()?;
                let condition = node(py, registry, query, &condition)?
                    .ok_or_else(|| field_error(py, "When() needs a condition"))?;
                let then = value_expr(py, registry, query, &when.get_item(1)?)?;
                whens.push((condition, then));
            }
            if whens.is_empty() {
                return Err(field_error(py, "Case() needs at least one When()"));
            }
            let default = value_expr(py, registry, query, &item(2)?)?;
            Ok(Expr::Case(whens, Box::new(default)))
        }
        "SUB" | "EXISTS" => {
            let inner = queryset(&item(1)?)?
                .ok_or_else(|| field_error(py, format!("{tag}: expected a queryset")))?;
            if tag == "SUB" {
                inner.subquery_field().map_err(|e| orm_error(py, e))?;
                Ok(Expr::Subquery(Box::new(inner)))
            } else {
                Ok(Expr::Exists(Box::new(inner)))
            }
        }
        "OUTER" => Err(field_error(
            py,
            "OuterRef() can only be used in filters of a Subquery() or Exists() queryset",
        )),
        other => Err(field_error(
            py,
            format!("{label}: unknown expression '{other}'"),
        )),
    }
}

/// The operand of a lookup on `field` (owned by `owner`; `None` for annotations).
fn operand(
    query: &Query,
    owner: Option<&ModelMeta>,
    field: &FieldMeta,
    lookup: Lookup,
    value: &Bound<'_, PyAny>,
) -> PyResult<Operand> {
    let py = value.py();
    let label = &query.model.label;
    let fail = |problem: &str| {
        field_error(
            py,
            format!("{label}.{}__{}: {problem}", field.name, lookup.name()),
        )
    };
    if let Some(expression) = expr(query, field, value)? {
        return Ok(Operand::Expr(expression));
    }
    let expected = owner.and_then(|o| key_model(o, field));
    let convert = |item: &Bound<'_, PyAny>| -> PyResult<Value> {
        let item = match owner {
            Some(_) => unwrap_instance(label, field, expected, item)?,
            None => item.clone(),
        };
        to_value(label, field, &item, Mode::Compare)
    };
    match lookup {
        Lookup::In => {
            if let Some(inner) = queryset(value)? {
                let selected = inner.subquery_field().map_err(|e| orm_error(py, e))?;
                if selected.kind.is_text() != field.kind.is_text() {
                    return Err(fail(&format!(
                        "the queryset selects {} values, not {} values",
                        selected.kind.name(),
                        field.kind.name()
                    )));
                }
                return Ok(Operand::Subquery(Box::new(inner)));
            }
            if value.is_instance_of::<PyString>() || value.is_none() {
                return Err(fail("needs a list, tuple or set of values"));
            }
            let items = value
                .try_iter()
                .map_err(|_| fail("needs a list, tuple or set of values"))?;
            let mut values = Vec::new();
            for item in items {
                values.push(convert(&item?)?);
            }
            Ok(Operand::List(values))
        }
        Lookup::IsNull => {
            if !value.is_instance_of::<PyBool>() {
                return Err(fail("needs True or False"));
            }
            Ok(Operand::Flag(value.is_truthy()?))
        }
        Lookup::Range => {
            let pair: Vec<Bound<'_, PyAny>> = match value.try_iter() {
                Ok(items) if !value.is_instance_of::<PyString>() => {
                    items.collect::<PyResult<_>>()?
                }
                _ => return Err(fail("needs two values (low, high)")),
            };
            if pair.len() != 2 {
                return Err(fail("needs two values (low, high)"));
            }
            Ok(Operand::List(vec![convert(&pair[0])?, convert(&pair[1])?]))
        }
        lookup if lookup.is_text() || lookup.is_regex() => {
            let text = value
                .cast::<PyString>()
                .map_err(|_| fail("needs a string"))?;
            Ok(Operand::Value(Value::Text(text.to_str()?.to_string())))
        }
        _ => Ok(Operand::Value(convert(value)?)),
    }
}

/// The native query of a `QuerySet` (or a native `Query`) given as a lookup value.
fn queryset(value: &Bound<'_, PyAny>) -> PyResult<Option<Query>> {
    if let Ok(native) = value.cast::<PyQuery>() {
        return Ok(Some(native.get().query.clone()));
    }
    let py = value.py();
    if value.hasattr(intern!(py, "_query"))?
        && let Ok(native) = value.getattr(intern!(py, "_query"))?.cast_into::<PyQuery>()
    {
        return Ok(Some(native.get().query.clone()));
    }
    Ok(None)
}

/// JSON text for a value compared with a JSON document or a value inside one.
fn json_text(label: &str, field: &FieldMeta, value: &Bound<'_, PyAny>) -> PyResult<Value> {
    let py = value.py();
    let bytes = crate::json::encode(value)
        .map_err(|err| field_error(py, format!("{label}.{}: {}", field.name, err.message(py))))?;
    Ok(Value::Text(String::from_utf8_lossy(&bytes).into_owned()))
}

/// The operand of a lookup on a JSON field, or on a key path inside it (Feature 022).
fn json_operand(
    label: &str,
    field: &FieldMeta,
    lookup: Lookup,
    value: &Bound<'_, PyAny>,
) -> PyResult<Operand> {
    let py = value.py();
    let fail = |problem: &str| {
        field_error(
            py,
            format!("{label}.{}__{}: {problem}", field.name, lookup.name()),
        )
    };
    let text = |item: &Bound<'_, PyAny>| -> PyResult<Value> {
        Ok(Value::Text(
            item.cast::<PyString>()
                .map_err(|_| fail("needs a string"))?
                .to_str()?
                .to_string(),
        ))
    };
    let items = || -> PyResult<Vec<Bound<'_, PyAny>>> {
        if value.is_instance_of::<PyString>() || value.is_none() {
            return Err(fail("needs a list"));
        }
        value
            .try_iter()
            .map_err(|_| fail("needs a list"))?
            .collect::<PyResult<_>>()
    };
    if value.hasattr(intern!(py, "_ferro_expr"))? {
        return Err(fail("cannot compare a JSON value with an F() expression"));
    }
    Ok(match lookup {
        Lookup::IsNull => {
            if !value.is_instance_of::<PyBool>() {
                return Err(fail("needs True or False"));
            }
            Operand::Flag(value.is_truthy()?)
        }
        Lookup::HasKey => Operand::Value(text(value)?),
        Lookup::HasKeys | Lookup::HasAnyKeys => {
            Operand::List(items()?.iter().map(&text).collect::<PyResult<_>>()?)
        }
        Lookup::Contains => Operand::Value(json_text(label, field, value)?),
        lookup if lookup.is_text() || lookup.is_regex() => Operand::Value(text(value)?),
        Lookup::In | Lookup::Range => Operand::List(
            items()?
                .iter()
                .map(|item| json_text(label, field, item))
                .collect::<PyResult<_>>()?,
        ),
        _ if value.is_none() => Operand::Value(Value::Null),
        _ => Operand::Value(json_text(label, field, value)?),
    })
}

fn leaf(
    py: Python<'_>,
    registry: &Registry,
    query: &Query,
    key: &str,
    value: &Bound<'_, PyAny>,
) -> PyResult<Node> {
    let label = &query.model.label;
    let (head, rest) = match key.split_once("__") {
        Some((head, rest)) => (head, Some(rest)),
        None => (key, None),
    };
    let condition = if let Some(index) = query.annotation(head) {
        let lookup = match rest {
            None => Lookup::Exact,
            Some(name) => Lookup::parse(name).ok_or_else(|| {
                field_error(py, format!("{label}: unknown lookup '{name}' in '{key}'"))
            })?,
        };
        let annotation = &query.annotations[index];
        let field = annotation.output_field();
        if lookup.is_text() || lookup.is_regex() || lookup.is_json_key() {
            return Err(field_error(
                py,
                format!(
                    "{label}: lookup '{}' cannot be used on annotation '{head}'",
                    lookup.name()
                ),
            ));
        }
        let operand = operand(query, None, &field, lookup, value)?;
        Condition::on(label, &field, Target::Annotation(index), lookup, operand)
    } else {
        let resolved =
            resolve_lookup_key(registry, &query.model, key).map_err(|e| orm_error(py, e))?;
        let lookup = resolved.lookup;
        let stored = resolved.path.meta().clone();
        let json = stored.kind == FieldKind::Json
            && (resolved.transform.is_some() || lookup.is_json_key() || lookup == Lookup::Contains);
        let (field, operand) = if json {
            let operand = json_operand(label, &stored, lookup, value)?;
            (stored, operand)
        } else if let Some(Transform::Date(part)) = &resolved.transform {
            if value.hasattr(intern!(py, "_ferro_expr"))? {
                return Err(field_error(
                    py,
                    format!("{label}: '{key}' cannot be compared with an F() expression"),
                ));
            }
            let mut field = FieldMeta::column(
                format!("{}__{}", stored.name, part.name()),
                part.output_kind(),
            );
            field.null = true;
            let operand = operand(query, None, &field, lookup, value)?;
            (field, operand)
        } else {
            let operand = operand(query, Some(&resolved.path.model), &stored, lookup, value)?;
            (stored, operand)
        };
        Condition::on(label, &field, Target::Field(resolved.path), lookup, operand)
            .map(|condition| condition.transformed(resolved.transform))
    };
    Ok(Node::Leaf(condition.map_err(|e| orm_error(py, e))?))
}

/// A filter tree from Python: `(connector, negated, children)` where each child is a
/// `(key, value)` pair or another tree (`Q` objects and keyword lookups).
fn node(
    py: Python<'_>,
    registry: &Registry,
    query: &Query,
    tree: &Bound<'_, PyTuple>,
) -> PyResult<Option<Node>> {
    let connector: String = tree.get_item(0)?.extract()?;
    let negated: bool = tree.get_item(1)?.extract()?;
    let mut children = Vec::new();
    for child in tree.get_item(2)?.try_iter()? {
        let child = child?;
        let child = child
            .cast_into::<PyTuple>()
            .map_err(|_| field_error(py, "filter conditions must be (key, value) pairs or Q"))?;
        match child.len() {
            2 => {
                let key: String = child.get_item(0)?.extract()?;
                children.push(leaf(py, registry, query, &key, &child.get_item(1)?)?);
            }
            3 => {
                if let Some(sub) = node(py, registry, query, &child)? {
                    children.push(sub);
                }
            }
            _ => return Err(field_error(py, "malformed filter condition")),
        }
    }
    let combined = if connector == "OR" {
        Node::or(children)
    } else {
        Node::and(children)
    };
    Ok(combined.map(|n| if negated { n.negate() } else { n }))
}

/// `(name, func, path, distinct, default)` → a resolved aggregate.
type AggregateSpec<'py> = (
    String,
    String,
    Option<String>,
    bool,
    Option<Bound<'py, PyAny>>,
);

fn aggregates(
    py: Python<'_>,
    registry: &Registry,
    query: &Query,
    specs: Vec<AggregateSpec<'_>>,
) -> PyResult<Vec<(String, Aggregate)>> {
    let mut out = Vec::with_capacity(specs.len());
    for (name, func, path, distinct, default) in specs {
        let func = AggFunc::parse(&func)
            .ok_or_else(|| field_error(py, format!("unknown aggregate '{func}'")))?;
        let mut aggregate =
            Aggregate::resolve(registry, &query.model, func, path.as_deref(), distinct)
                .map_err(|e| orm_error(py, e))?;
        if let Some(default) = default {
            let field = aggregate.output_field(&name);
            aggregate.default = Some(constant(&query.model.label, &field, &default)?);
        }
        out.push((name, aggregate));
    }
    Ok(out)
}

/// Converts an aggregate's result cell: `Count` → int, `Avg` → float, others keep the field's
/// type (PostgreSQL returns integer sums as numeric).
/// Numbers from computed values arrive as the database chose (PostgreSQL sums integers as
/// numeric); converts them to the kind the value is declared as.
fn normalize_number(kind: &FieldKind, value: Value) -> PyResult<Value> {
    Ok(match (kind, value) {
        (FieldKind::Integer | FieldKind::BigInteger, Value::Decimal(text)) => {
            let whole = text.split('.').next().unwrap_or("0");
            Value::Integer(whole.parse().map_err(|_| {
                PyRuntimeError::new_err(format!("integer value out of range: {text}"))
            })?)
        }
        (FieldKind::Integer | FieldKind::BigInteger, Value::Real(number)) => {
            Value::Integer(number as i64)
        }
        (FieldKind::Float, Value::Decimal(text)) => Value::Real(
            text.parse()
                .map_err(|_| PyRuntimeError::new_err(format!("invalid number {text}")))?,
        ),
        (FieldKind::Float, Value::Integer(number)) => Value::Real(number as f64),
        (_, value) => value,
    })
}

/// An annotation's value as Python: aggregates by their function, expressions by their kind.
fn annotation_value<'py>(
    py: Python<'py>,
    annotation: &ferro_orm::Annotation,
    value: Value,
) -> PyResult<Bound<'py, PyAny>> {
    match &annotation.value {
        ferro_orm::AnnotationValue::Aggregate(aggregate) => aggregate_value(py, aggregate, value),
        ferro_orm::AnnotationValue::Expr(_, kind) => {
            if value == Value::Null {
                return Ok(py.None().into_bound(py));
            }
            let value = normalize_number(kind, value)?;
            from_value(py, &annotation.output_field(), value)
        }
    }
}

fn aggregate_value<'py>(
    py: Python<'py>,
    aggregate: &Aggregate,
    value: Value,
) -> PyResult<Bound<'py, PyAny>> {
    if value == Value::Null {
        return Ok(py.None().into_bound(py));
    }
    let value = normalize_number(&aggregate.output_kind(), value)?;
    let field = match &aggregate.path {
        Some(path) if aggregate.func != AggFunc::Count && aggregate.func != AggFunc::Avg => {
            path.meta().clone()
        }
        _ => aggregate.output_field("value"),
    };
    from_value(py, &field, value)
}

fn shape(py: Python<'_>, name: &str) -> PyResult<Shape> {
    Ok(match name {
        "dicts" => Shape::Dicts,
        "tuples" => Shape::Tuples,
        "flat" => Shape::Flat,
        other => return Err(field_error(py, format!("unknown values() shape '{other}'"))),
    })
}

/// Rows of a `values()` query as dicts, tuples or single values.
fn value_rows<'py>(
    py: Python<'py>,
    query: &Query,
    rows: ferro_database::Rows,
) -> PyResult<Bound<'py, PyList>> {
    let values = query.values.as_ref().expect("values() query");
    let names: Vec<Bound<'py, PyString>> = values
        .names
        .iter()
        .map(|n| PyString::intern(py, n))
        .collect();
    let list = PyList::empty(py);
    for row in rows.rows {
        let mut converted = Vec::with_capacity(values.fields.len());
        for (selected, cell) in values.fields.iter().zip(row) {
            converted.push(match selected {
                Selected::Field(path) => from_value(py, path.meta(), cell)?,
                Selected::Annotation(index) => {
                    annotation_value(py, &query.annotations[*index], cell)?
                }
            });
        }
        match values.shape {
            Shape::Dicts => {
                let dict = PyDict::new(py);
                for (name, value) in names.iter().zip(converted) {
                    dict.set_item(name, value)?;
                }
                list.append(dict)?;
            }
            Shape::Tuples => list.append(PyTuple::new(py, converted)?)?,
            Shape::Flat => list.append(
                converted
                    .into_iter()
                    .next()
                    .unwrap_or(py.None().into_bound(py)),
            )?,
        }
    }
    Ok(list)
}

#[pymethods]
impl PyQuery {
    /// Adds a filter tree (`filter()` / `exclude()` / `Q`).
    fn filter(&self, py: Python<'_>, tree: &Bound<'_, PyTuple>) -> PyResult<Self> {
        let registry = registry();
        let node = node(py, &registry, &self.query, tree)?;
        Ok(self.derive(py, self.query.with_node(node)))
    }

    fn order_by(&self, py: Python<'_>, names: Vec<String>) -> PyResult<Self> {
        let names: Vec<&str> = names.iter().map(String::as_str).collect();
        let query = self
            .query
            .order_by_in(&registry(), &names)
            .map_err(|e| orm_error(py, e))?;
        Ok(self.derive(py, query))
    }

    fn select_related(&self, py: Python<'_>, names: Vec<String>) -> PyResult<Self> {
        let names: Vec<&str> = names.iter().map(String::as_str).collect();
        let query = self
            .query
            .select_related(&registry(), &names)
            .map_err(|e| orm_error(py, e))?;
        Ok(self.derive(py, query))
    }

    fn annotate(&self, py: Python<'_>, specs: Vec<AggregateSpec<'_>>) -> PyResult<Self> {
        let resolved = aggregates(py, &registry(), &self.query, specs)?;
        let query = self
            .query
            .annotate(resolved)
            .map_err(|e| orm_error(py, e))?;
        Ok(self.derive(py, query))
    }

    fn values(&self, py: Python<'_>, names: Vec<String>, shape_name: &str) -> PyResult<Self> {
        let names: Vec<&str> = names.iter().map(String::as_str).collect();
        let query = self
            .query
            .values(&registry(), &names, shape(py, shape_name)?)
            .map_err(|e| orm_error(py, e))?;
        Ok(self.derive(py, query))
    }

    #[pyo3(signature = (nowait=false, skip_locked=false))]
    fn select_for_update(&self, py: Python<'_>, nowait: bool, skip_locked: bool) -> PyResult<Self> {
        let lock = match (nowait, skip_locked) {
            (false, false) => Lock::Wait,
            (true, false) => Lock::NoWait,
            (false, true) => Lock::SkipLocked,
            (true, true) => {
                return Err(field_error(
                    py,
                    "select_for_update(): nowait and skip_locked cannot be used together",
                ));
            }
        };
        let query = self.query.for_update(lock).map_err(|e| orm_error(py, e))?;
        Ok(self.derive(py, query))
    }

    #[pyo3(signature = (offset=None, limit=None))]
    fn window(&self, py: Python<'_>, offset: Option<u64>, limit: Option<u64>) -> Self {
        self.derive(py, self.query.window(offset, limit))
    }

    fn reverse(&self, py: Python<'_>) -> Self {
        self.derive(py, self.query.reversed())
    }

    /// The database's plan for this query (`EXPLAIN`; SQLite: `EXPLAIN QUERY PLAN`), as text.
    fn explain<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyAny>> {
        self.check_outer(py)?;
        let database = current(py)?;
        let statement = compile::select(database.dialect, &self.query);
        let prefix = if database.dialect.name() == "postgresql" {
            "EXPLAIN "
        } else {
            "EXPLAIN QUERY PLAN "
        };
        let statement =
            ferro_database::Statement::new(format!("{prefix}{}", statement.sql), statement.params);
        submit(
            py,
            &database,
            Job::Query(statement),
            Box::new(|py, result| {
                let rows = rows(py, result, None)?;
                let lines: Vec<String> = rows
                    .rows
                    .into_iter()
                    .filter_map(|row| match row.into_iter().last() {
                        Some(Value::Text(text)) => Some(text),
                        _ => None,
                    })
                    .collect();
                to_py(py, lines.join("\n"))
            }),
        )
    }

    /// Adds a per-row expression annotation (Feature 022).
    fn annotate_expr(&self, py: Python<'_>, name: &str, tree: &Bound<'_, PyAny>) -> PyResult<Self> {
        let expr = value_expr(py, &registry(), &self.query, tree)?;
        let query = self
            .query
            .annotate_expr(name, expr)
            .map_err(|e| orm_error(py, e))?;
        Ok(self.derive(py, query))
    }

    /// `SELECT DISTINCT` (Feature 022).
    fn distinct(&self, py: Python<'_>) -> Self {
        self.derive(py, self.query.distinct())
    }

    /// The same query ordered additionally by the primary key (Feature 021).
    fn with_pk_tiebreak(&self, py: Python<'_>) -> Self {
        self.derive(py, self.query.with_pk_tiebreak())
    }

    #[getter]
    fn ordered(&self) -> bool {
        self.query.is_ordered()
    }

    #[getter]
    fn windowed(&self) -> bool {
        self.query.is_windowed()
    }

    #[getter]
    fn grouped(&self) -> bool {
        self.query.is_grouped()
    }

    #[getter]
    fn locking(&self) -> bool {
        self.query.is_locking()
    }

    #[getter]
    fn returns_values(&self) -> bool {
        self.query.values.is_some()
    }

    /// The SELECT this query runs, as `(sql, params)`.
    fn sql(&self, py: Python<'_>) -> PyResult<(String, Vec<Py<PyAny>>)> {
        self.check_outer(py)?;
        let statement = compile::select(db::dialect(), &self.query);
        let params = statement
            .params
            .into_iter()
            .map(|v| raw(py, v).map(Bound::unbind))
            .collect::<PyResult<Vec<_>>>()?;
        Ok((statement.sql, params))
    }

    fn fetch<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyAny>> {
        self.check_outer(py)?;
        let database = current(py)?;
        let statement = compile::select(database.dialect, &self.query);
        let query = self.query.clone();
        let meta = self.meta();
        if query.values.is_some() {
            let convert: Converter = Box::new(move |py, result| {
                let rows = rows(py, result, Some(&meta))?;
                Ok(value_rows(py, &query, rows)?.into_any().unbind())
            });
            return submit(py, &database, Job::Query(statement), convert);
        }
        let cls = self.cls.clone_ref(py);
        if query.related.is_empty() && query.annotations.is_empty() {
            let convert: Converter = Box::new(move |py, result| {
                let rows = rows(py, result, Some(&meta))?;
                Ok(build_instances(py, cls.bind(py), &meta, rows)?
                    .into_any()
                    .unbind())
            });
            return submit(py, &database, Job::Query(statement), convert);
        }
        let mut related = Vec::with_capacity(query.related.len());
        for path in &query.related {
            let target = path.last().expect("non-empty path").target().clone();
            let class = model_class(py, &target.qualname).ok_or_else(|| {
                field_error(
                    py,
                    format!("model '{}' has no Python class", target.qualname),
                )
            })?;
            related.push((class, target));
        }
        let convert: Converter = Box::new(move |py, result| {
            let rows = rows(py, result, Some(&meta))?;
            let root = InstanceBuilder::new(py, cls.bind(py).clone(), meta.clone())?;
            let builders = related
                .iter()
                .map(|(class, target)| {
                    InstanceBuilder::new(py, class.bind(py).clone(), target.clone())
                })
                .collect::<PyResult<Vec<_>>>()?;
            let related_key = intern!(py, "_related");
            let list = PyList::empty(py);
            for row in rows.rows {
                let mut cells = row.into_iter();
                let instance = root.build(py, &mut cells)?;
                // Objects per select_related path; a path's parent is its prefix (or the root).
                let mut built: Vec<Option<Bound<'_, PyAny>>> = Vec::with_capacity(builders.len());
                for (index, builder) in builders.iter().enumerate() {
                    let object = builder.build_optional(py, &mut cells)?;
                    let path = &query.related[index];
                    let parent = if path.len() == 1 {
                        Some(instance.clone())
                    } else {
                        query.related[..index]
                            .iter()
                            .position(|p| {
                                p.len() == path.len() - 1
                                    && p.iter().zip(path.iter()).all(|(a, b)| a.same(b))
                            })
                            .and_then(|parent| built[parent].clone())
                    };
                    if let Some(parent) = parent {
                        let dict = instance_dict(&parent)?;
                        let cache = match dict.get_item(related_key)? {
                            Some(cache) => cache.cast_into::<PyDict>()?,
                            None => {
                                let cache = PyDict::new(py);
                                dict.set_item(related_key, &cache)?;
                                cache
                            }
                        };
                        let name = path.last().expect("non-empty path").name();
                        cache.set_item(
                            name,
                            object
                                .as_ref()
                                .map_or_else(|| py.None().into_bound(py), Clone::clone),
                        )?;
                    }
                    built.push(object);
                }
                if !query.annotations.is_empty() {
                    let dict = instance_dict(&instance)?;
                    for annotation in &query.annotations {
                        let cell = cells.next().unwrap_or(Value::Null);
                        dict.set_item(&annotation.name, annotation_value(py, annotation, cell)?)?;
                    }
                }
                list.append(instance)?;
            }
            Ok(list.into_any().unbind())
        });
        submit(py, &database, Job::Query(statement), convert)
    }

    fn count<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyAny>> {
        self.check_outer(py)?;
        self.refuse_lock(py, "count")?;
        let database = current(py)?;
        let statement = compile::count(database.dialect, &self.query);
        let meta = self.meta();
        submit(
            py,
            &database,
            Job::Query(statement),
            Box::new(move |py, result| {
                let rows = rows(py, result, Some(&meta))?;
                let count = match rows.rows.first().and_then(|r| r.first()) {
                    Some(Value::Integer(n)) => *n,
                    _ => 0,
                };
                to_py(py, count)
            }),
        )
    }

    fn exists<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyAny>> {
        self.check_outer(py)?;
        self.refuse_lock(py, "exists")?;
        let database = current(py)?;
        let statement = compile::exists(database.dialect, &self.query);
        let meta = self.meta();
        submit(
            py,
            &database,
            Job::Query(statement),
            Box::new(move |py, result| {
                let found = !rows(py, result, Some(&meta))?.rows.is_empty();
                to_py(py, found)
            }),
        )
    }

    /// One row of aggregates as a dict.
    fn aggregate<'py>(
        &self,
        py: Python<'py>,
        specs: Vec<AggregateSpec<'py>>,
    ) -> PyResult<Bound<'py, PyAny>> {
        self.check_outer(py)?;
        self.refuse_lock(py, "aggregate")?;
        let resolved = aggregates(py, &registry(), &self.query, specs)?;
        let database = current(py)?;
        let list: Vec<Aggregate> = resolved.iter().map(|(_, a)| a.clone()).collect();
        let statement = compile::aggregate(database.dialect, &self.query, &list)
            .map_err(|e| orm_error(py, e))?;
        let meta = self.meta();
        submit(
            py,
            &database,
            Job::Query(statement),
            Box::new(move |py, result| {
                let rows = rows(py, result, Some(&meta))?;
                let row = rows.rows.into_iter().next().unwrap_or_default();
                let dict = PyDict::new(py);
                let mut cells = row.into_iter();
                for (name, aggregate) in &resolved {
                    let cell = cells.next().unwrap_or(Value::Null);
                    dict.set_item(name, aggregate_value(py, aggregate, cell)?)?;
                }
                Ok(dict.into_any().unbind())
            }),
        )
    }

    fn update<'py>(
        &self,
        py: Python<'py>,
        values: &Bound<'py, PyDict>,
    ) -> PyResult<Bound<'py, PyAny>> {
        self.check_outer(py)?;
        let meta = self.meta();
        let mut assignments = Vec::with_capacity(values.len());
        for (key, value) in values.iter() {
            let name: String = key.extract()?;
            let index = meta.field(&name).ok_or_else(|| {
                field_error(
                    py,
                    format!(
                        "{} has no field '{name}'; fields: {}",
                        meta.label,
                        meta.field_names().join(", ")
                    ),
                )
            })?;
            let field = &meta.fields[index];
            if field.auto {
                return Err(field_error(
                    py,
                    format!(
                        "{}.{}: the automatic primary key cannot be updated",
                        meta.label, field.name
                    ),
                ));
            }
            let assignment = match expr(&self.query, field, &value)? {
                Some(expression) => Assignment::Expr(expression),
                None => Assignment::Value(store_value(&meta, field, &value)?),
            };
            assignments.push((index, assignment));
        }
        let database = current(py)?;
        let statement = compile::update(database.dialect, &self.query, &assignments)
            .map_err(|e| orm_error(py, e))?;
        submit(
            py,
            &database,
            Job::Execute(statement),
            Box::new(move |py, result| to_py(py, affected(py, result, Some(&meta))?)),
        )
    }

    fn delete<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyAny>> {
        self.check_outer(py)?;
        let meta = self.meta();
        let database = current(py)?;
        let statement =
            compile::delete(database.dialect, &self.query).map_err(|e| orm_error(py, e))?;
        submit(
            py,
            &database,
            Job::Execute(statement),
            Box::new(move |py, result| match result {
                Ok(ferro_database::Outcome::Affected(n)) => to_py(py, n),
                Ok(other) => Err(PyRuntimeError::new_err(format!(
                    "unexpected database outcome {other:?}"
                ))),
                Err(err) => Err(delete_error(py, err, &meta)),
            }),
        )
    }

    fn __repr__(&self) -> String {
        format!(
            "<Query {}>",
            compile::select(db::dialect(), &self.query).sql
        )
    }
}
