use std::sync::Arc;

use ferro_database::Value;

use crate::error::OrmError;
use crate::field::{FieldKind, FieldMeta};
use crate::lookup::{Lookup, parse_key};
use crate::meta::{ModelMeta, is_identifier};
use crate::path::{FieldPath, Hop, resolve_field, resolve_related};
use crate::registry::Registry;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ArithOp {
    Add,
    Sub,
    Mul,
    Div,
}

impl ArithOp {
    pub fn parse(symbol: &str) -> Option<Self> {
        Some(match symbol {
            "+" => Self::Add,
            "-" => Self::Sub,
            "*" => Self::Mul,
            "/" => Self::Div,
            _ => return None,
        })
    }

    pub fn symbol(self) -> &'static str {
        match self {
            Self::Add => "+",
            Self::Sub => "-",
            Self::Mul => "*",
            Self::Div => "/",
        }
    }
}

/// An `F()` expression over fields of the query's model.
#[derive(Debug, Clone, PartialEq)]
pub enum Expr {
    Field(usize),
    Value(Value),
    Binary(Box<Expr>, ArithOp, Box<Expr>),
}

impl Expr {
    /// Fields referenced by the expression.
    pub fn fields(&self, out: &mut Vec<usize>) {
        match self {
            Self::Field(index) => out.push(*index),
            Self::Value(_) => {}
            Self::Binary(left, _, right) => {
                left.fields(out);
                right.fields(out);
            }
        }
    }

    pub fn is_arithmetic(&self) -> bool {
        matches!(self, Self::Binary(..))
    }
}

#[derive(Debug, Clone, PartialEq)]
pub enum Operand {
    Value(Value),
    List(Vec<Value>),
    Flag(bool),
    Expr(Expr),
}

/// What a condition compares: a field (possibly across relations) or an annotation.
#[derive(Debug, Clone)]
pub enum Target {
    Field(FieldPath),
    Annotation(usize),
}

#[derive(Debug, Clone)]
pub struct Condition {
    pub target: Target,
    pub lookup: Lookup,
    pub operand: Operand,
}

impl Condition {
    /// A condition on a field of `meta` itself.
    pub fn new(
        meta: &Arc<ModelMeta>,
        field: usize,
        lookup: Lookup,
        operand: Operand,
    ) -> Result<Self, OrmError> {
        Self::on(
            &meta.label,
            &meta.fields[field],
            Target::Field(FieldPath::local(meta, field)),
            lookup,
            operand,
        )
    }

    /// Validates the operand shape for the lookup. `field=None` becomes `field__isnull=True`.
    /// `field` describes the compared value (for annotations, a synthesized field).
    pub fn on(
        label: &str,
        field: &FieldMeta,
        target: Target,
        lookup: Lookup,
        operand: Operand,
    ) -> Result<Self, OrmError> {
        let name = &field.name;
        let error = |problem: &str| {
            OrmError(format!(
                "{label}: lookup '{}' on field '{name}' {problem}",
                lookup.name()
            ))
        };
        let (lookup, operand) = match (lookup, operand) {
            (Lookup::Exact, Operand::Value(Value::Null)) => (Lookup::IsNull, Operand::Flag(true)),
            (Lookup::IsNull, operand @ Operand::Flag(_)) => (Lookup::IsNull, operand),
            (Lookup::IsNull, _) => return Err(error("needs True or False")),
            (Lookup::In, operand @ Operand::List(_)) => (Lookup::In, operand),
            (Lookup::In, _) => return Err(error("needs a list of values")),
            (_, Operand::List(_)) => {
                return Err(error(&format!("cannot take a list; use '{name}__in'")));
            }
            (_, Operand::Flag(_)) => return Err(error("needs a value")),
            (lookup, operand @ Operand::Expr(_)) => {
                if lookup.is_text() {
                    return Err(error("cannot compare with an F() expression"));
                }
                (lookup, operand)
            }
            (_, Operand::Value(Value::Null)) => return Err(error("cannot compare with None")),
            (lookup, Operand::Value(value)) => {
                if lookup.is_text() && !matches!(value, Value::Text(_)) {
                    return Err(error("needs a string"));
                }
                (lookup, Operand::Value(value))
            }
        };
        Ok(Self {
            target,
            lookup,
            operand,
        })
    }

    pub fn hops(&self) -> &[Hop] {
        match &self.target {
            Target::Field(path) => &path.hops,
            Target::Annotation(_) => &[],
        }
    }
}

/// A boolean combination of conditions (`filter()` calls and `Q` objects).
#[derive(Debug, Clone)]
pub enum Node {
    Leaf(Condition),
    And(Vec<Node>),
    Or(Vec<Node>),
    Not(Box<Node>),
}

impl Node {
    /// AND of `children`; `None` when there is nothing to test. A single child is returned as is.
    pub fn and(children: Vec<Node>) -> Option<Node> {
        Self::combine(children, Node::And)
    }

    pub fn or(children: Vec<Node>) -> Option<Node> {
        Self::combine(children, Node::Or)
    }

    fn combine(mut children: Vec<Node>, build: fn(Vec<Node>) -> Node) -> Option<Node> {
        match children.len() {
            0 => None,
            1 => children.pop(),
            _ => Some(build(children)),
        }
    }

    pub fn negate(self) -> Node {
        Node::Not(Box::new(self))
    }

    pub fn uses_annotation(&self) -> bool {
        match self {
            Self::Leaf(condition) => matches!(condition.target, Target::Annotation(_)),
            Self::And(children) | Self::Or(children) => children.iter().any(Node::uses_annotation),
            Self::Not(child) => child.uses_annotation(),
        }
    }
}

#[derive(Debug, Clone)]
pub enum OrderTarget {
    Field(FieldPath),
    Annotation(usize),
}

#[derive(Debug, Clone)]
pub struct Order {
    pub target: OrderTarget,
    pub descending: bool,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum AggFunc {
    Count,
    Sum,
    Avg,
    Min,
    Max,
}

impl AggFunc {
    pub fn parse(name: &str) -> Option<Self> {
        Some(match name {
            "Count" => Self::Count,
            "Sum" => Self::Sum,
            "Avg" => Self::Avg,
            "Min" => Self::Min,
            "Max" => Self::Max,
            _ => return None,
        })
    }

    pub fn name(self) -> &'static str {
        match self {
            Self::Count => "Count",
            Self::Sum => "Sum",
            Self::Avg => "Avg",
            Self::Min => "Min",
            Self::Max => "Max",
        }
    }

    pub fn sql(self) -> &'static str {
        match self {
            Self::Count => "COUNT",
            Self::Sum => "SUM",
            Self::Avg => "AVG",
            Self::Min => "MIN",
            Self::Max => "MAX",
        }
    }
}

fn is_numeric(kind: &FieldKind) -> bool {
    matches!(
        kind,
        FieldKind::Integer | FieldKind::BigInteger | FieldKind::Float | FieldKind::Decimal { .. }
    )
}

#[derive(Debug, Clone)]
pub struct Aggregate {
    pub func: AggFunc,
    /// `None` counts rows (`Count("*")`).
    pub path: Option<FieldPath>,
    pub distinct: bool,
    pub default: Option<Value>,
}

impl Aggregate {
    pub fn resolve(
        registry: &Registry,
        root: &Arc<ModelMeta>,
        func: AggFunc,
        path: Option<&str>,
        distinct: bool,
    ) -> Result<Self, OrmError> {
        let path = match path {
            None | Some("*") => {
                if func != AggFunc::Count {
                    return Err(OrmError(format!(
                        "{}: {}() needs a field",
                        root.label,
                        func.name()
                    )));
                }
                None
            }
            Some(name) => Some(resolve_field(registry, root, name, false, func.name())?),
        };
        if let Some(path) = &path {
            let field = path.meta();
            let ok = match func {
                AggFunc::Count => true,
                AggFunc::Sum | AggFunc::Avg => is_numeric(&field.kind),
                AggFunc::Min | AggFunc::Max => field.kind.is_ordered(),
            };
            if !ok {
                return Err(OrmError(format!(
                    "{}: {}() cannot be used on {} field '{}'",
                    root.label,
                    func.name(),
                    field.kind.name(),
                    field.name
                )));
            }
        }
        Ok(Self {
            func,
            path,
            distinct,
            default: None,
        })
    }

    /// The kind of the aggregate's value: `Count` → BigInteger, `Avg` → Float, others keep the
    /// field's kind.
    pub fn output_kind(&self) -> FieldKind {
        match (self.func, &self.path) {
            (AggFunc::Count, _) | (_, None) => FieldKind::BigInteger,
            (AggFunc::Avg, _) => FieldKind::Float,
            (_, Some(path)) => path.meta().kind.clone(),
        }
    }

    /// A field describing the aggregate's value (for converting operands and results).
    pub fn output_field(&self, name: &str) -> FieldMeta {
        let mut field = FieldMeta::column(name, self.output_kind());
        field.null = true;
        field
    }

    fn single_valued(&self) -> bool {
        self.path.as_ref().is_none_or(FieldPath::single_valued)
    }
}

#[derive(Debug, Clone)]
pub struct Annotation {
    pub name: String,
    pub aggregate: Aggregate,
    /// Computed per group of a `values()` query instead of per record.
    pub grouped: bool,
}

#[derive(Debug, Clone)]
pub enum Selected {
    Field(FieldPath),
    Annotation(usize),
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Shape {
    Dicts,
    Tuples,
    Flat,
}

#[derive(Debug, Clone)]
pub struct Values {
    pub names: Vec<String>,
    pub fields: Vec<Selected>,
    pub shape: Shape,
}

/// Row locking requested by `select_for_update()`.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Lock {
    /// Wait for rows locked by other transactions.
    Wait,
    /// Fail at once when a row is locked.
    NoWait,
    /// Leave locked rows out of the result.
    SkipLocked,
}

/// An immutable query description. Every method returns a new query.
#[derive(Debug, Clone)]
pub struct Query {
    pub model: Arc<ModelMeta>,
    pub filters: Vec<Node>,
    pub ordering: Vec<Order>,
    pub limit: Option<u64>,
    pub offset: Option<u64>,
    /// `select_related` paths (prefix-closed, in join order).
    pub related: Vec<Vec<Hop>>,
    pub annotations: Vec<Annotation>,
    pub values: Option<Values>,
    /// `select_for_update()`: lock the fetched rows of the model until the transaction ends.
    pub lock: Option<Lock>,
}

fn same_path(a: &[Hop], b: &[Hop]) -> bool {
    a.len() == b.len() && a.iter().zip(b).all(|(x, y)| x.same(y))
}

impl Query {
    pub fn new(model: Arc<ModelMeta>) -> Self {
        Self {
            model,
            filters: Vec::new(),
            ordering: Vec::new(),
            limit: None,
            offset: None,
            related: Vec::new(),
            annotations: Vec::new(),
            values: None,
            lock: None,
        }
    }

    /// Locks the rows this query fetches (only the model's own rows, not joined ones).
    pub fn for_update(&self, lock: Lock) -> Result<Self, OrmError> {
        if !self.annotations.is_empty() {
            return Err(self.lock_error());
        }
        let mut next = self.clone();
        next.lock = Some(lock);
        Ok(next)
    }

    pub fn is_locking(&self) -> bool {
        self.lock.is_some()
    }

    fn lock_error(&self) -> OrmError {
        OrmError(format!(
            "{}: select_for_update() cannot be combined with annotate(); lock the rows, then              aggregate in a separate query",
            self.model.label
        ))
    }

    pub fn with_node(&self, node: Option<Node>) -> Self {
        let mut next = self.clone();
        if let Some(node) = node {
            next.filters.push(node);
        }
        next
    }

    /// Conditions on the model's own fields combined with AND (`exclude()` when negated). Used by
    /// tests and by callers that already hold database values.
    pub fn filter(
        &self,
        negated: bool,
        conditions: Vec<(&str, Operand)>,
    ) -> Result<Self, OrmError> {
        let mut leaves = Vec::with_capacity(conditions.len());
        for (key, operand) in conditions {
            let (field, lookup) = parse_key(&self.model, key)?;
            leaves.push(Node::Leaf(Condition::new(
                &self.model,
                field,
                lookup,
                operand,
            )?));
        }
        let node = Node::and(leaves).map(|node| if negated { node.negate() } else { node });
        Ok(self.with_node(node))
    }

    pub fn annotation(&self, name: &str) -> Option<usize> {
        self.annotations.iter().position(|a| a.name == name)
    }

    /// Replaces the ordering of the model's own fields. `-name` sorts descending.
    pub fn order_by(&self, names: &[&str]) -> Result<Self, OrmError> {
        self.order_by_in(&Registry::default(), names)
    }

    /// Replaces the ordering: fields, forward relation paths (`category__name`) or annotations.
    pub fn order_by_in(&self, registry: &Registry, names: &[&str]) -> Result<Self, OrmError> {
        let mut ordering = Vec::with_capacity(names.len());
        for raw in names {
            let (name, descending) = match raw.strip_prefix('-') {
                Some(name) => (name, true),
                None => (*raw, false),
            };
            if name.is_empty() {
                return Err(OrmError(format!(
                    "{}: empty ordering name",
                    self.model.label
                )));
            }
            let target = match self.annotation(name) {
                Some(index) => OrderTarget::Annotation(index),
                None => OrderTarget::Field(
                    resolve_field(registry, &self.model, name, true, "order_by()").map_err(
                        |err| {
                            if name.contains("__") {
                                err
                            } else {
                                OrmError(format!(
                                    "{}: cannot order by unknown field '{name}'; fields: {}",
                                    self.model.label,
                                    self.model.field_names().join(", ")
                                ))
                            }
                        },
                    )?,
                ),
            };
            ordering.push(Order { target, descending });
        }
        let mut next = self.clone();
        next.ordering = ordering;
        Ok(next)
    }

    /// Applies a window relative to the current one (slicing a sliced queryset composes).
    pub fn window(&self, offset: Option<u64>, limit: Option<u64>) -> Self {
        let skip = offset.unwrap_or(0);
        let new_offset = self.offset.unwrap_or(0).saturating_add(skip);
        let remaining = self.limit.map(|l| l.saturating_sub(skip));
        let new_limit = match (remaining, limit) {
            (None, limit) => limit,
            (Some(remaining), None) => Some(remaining),
            (Some(remaining), Some(limit)) => Some(remaining.min(limit)),
        };
        let mut next = self.clone();
        next.offset = (new_offset > 0).then_some(new_offset);
        next.limit = new_limit;
        next
    }

    /// Reverses the ordering; an unordered query becomes primary key descending.
    pub fn reversed(&self) -> Self {
        let mut next = self.clone();
        if next.ordering.is_empty() {
            next.ordering.push(Order {
                target: OrderTarget::Field(FieldPath::local(&self.model, self.model.pk)),
                descending: true,
            });
        } else {
            for order in &mut next.ordering {
                order.descending = !order.descending;
            }
        }
        next
    }

    /// The ordering with the primary key appended as a last tie-breaker, so that consecutive
    /// windows of the result never skip or repeat rows (`QuerySet.iterator()`, Feature 021).
    pub fn with_pk_tiebreak(&self) -> Self {
        let mut next = self.clone();
        let has_pk = next.ordering.iter().any(|order| {
            matches!(&order.target, OrderTarget::Field(path)
                if path.hops.is_empty() && path.field == self.model.pk)
        });
        if !has_pk {
            next.ordering.push(Order {
                target: OrderTarget::Field(FieldPath::local(&self.model, self.model.pk)),
                descending: false,
            });
        }
        next
    }

    pub fn is_ordered(&self) -> bool {
        !self.ordering.is_empty()
    }

    pub fn is_windowed(&self) -> bool {
        self.limit.is_some() || self.offset.is_some()
    }

    /// `values(...).annotate(...)`: one row per group.
    pub fn is_grouped(&self) -> bool {
        self.annotations.iter().any(|a| a.grouped)
    }

    /// Adds `select_related` paths (each prefix too).
    pub fn select_related(&self, registry: &Registry, names: &[&str]) -> Result<Self, OrmError> {
        let mut next = self.clone();
        for name in names {
            let hops = resolve_related(registry, &self.model, name)?;
            for end in 1..=hops.len() {
                let prefix = &hops[..end];
                if !next.related.iter().any(|known| same_path(known, prefix)) {
                    next.related.push(prefix.to_vec());
                }
            }
        }
        Ok(next)
    }

    fn check_new_name(&self, name: &str) -> Result<(), OrmError> {
        if !is_identifier(name) || name.contains("__") {
            return Err(OrmError(format!(
                "{}: '{name}' is not a valid annotation name",
                self.model.label
            )));
        }
        if self.model.field(name).is_some() || self.annotation(name).is_some() {
            return Err(OrmError(format!(
                "{}: annotation '{name}' clashes with a field or another annotation",
                self.model.label
            )));
        }
        Ok(())
    }

    /// Adds aggregates per record (relation paths) or, after `values()`, per group.
    pub fn annotate(&self, annotations: Vec<(String, Aggregate)>) -> Result<Self, OrmError> {
        if self.is_locking() {
            return Err(self.lock_error());
        }
        let mut next = self.clone();
        let grouped = self.values.is_some();
        for (name, aggregate) in annotations {
            next.check_new_name(&name)?;
            if grouped {
                if next.annotations.iter().any(|a| !a.grouped) {
                    return Err(OrmError(format!(
                        "{}: annotate() before values() cannot be combined with a grouped \
                         annotate()",
                        self.model.label
                    )));
                }
                if !aggregate.single_valued() {
                    return Err(OrmError(format!(
                        "{}: grouped annotate() cannot follow many-valued relations; annotate \
                         each record instead (before values())",
                        self.model.label
                    )));
                }
            } else if aggregate.path.as_ref().is_none_or(FieldPath::is_local) {
                return Err(OrmError(format!(
                    "{}: annotate('{name}') needs a relation path (e.g. Count('items')); to \
                     aggregate fields per group call values() first",
                    self.model.label
                )));
            }
            next.annotations.push(Annotation {
                name,
                aggregate,
                grouped,
            });
        }
        if grouped && let Some(values) = &mut next.values {
            for (index, annotation) in next.annotations.iter().enumerate() {
                if !values.names.contains(&annotation.name) {
                    values.names.push(annotation.name.clone());
                    values.fields.push(Selected::Annotation(index));
                }
            }
        }
        Ok(next)
    }

    /// Returns rows as dicts / tuples / single values of the named fields (all fields by
    /// default), following forward relations.
    pub fn values(
        &self,
        registry: &Registry,
        names: &[&str],
        shape: Shape,
    ) -> Result<Self, OrmError> {
        if self.is_grouped() {
            return Err(OrmError(format!(
                "{}: values() cannot follow a grouped annotate()",
                self.model.label
            )));
        }
        let mut selected_names = Vec::new();
        let mut fields = Vec::new();
        if names.is_empty() {
            for (index, field) in self.model.fields.iter().enumerate() {
                selected_names.push(field.attname.clone());
                fields.push(Selected::Field(FieldPath::local(&self.model, index)));
            }
            for (index, annotation) in self.annotations.iter().enumerate() {
                selected_names.push(annotation.name.clone());
                fields.push(Selected::Annotation(index));
            }
        } else {
            for name in names {
                selected_names.push((*name).to_string());
                fields.push(match self.annotation(name) {
                    Some(index) => Selected::Annotation(index),
                    None => Selected::Field(resolve_field(
                        registry,
                        &self.model,
                        name,
                        true,
                        "values()",
                    )?),
                });
            }
        }
        if shape == Shape::Flat && fields.len() != 1 {
            return Err(OrmError(format!(
                "{}: values_list(flat=True) needs exactly one field",
                self.model.label
            )));
        }
        let mut next = self.clone();
        next.values = Some(Values {
            names: selected_names,
            fields,
            shape,
        });
        Ok(next)
    }
}
