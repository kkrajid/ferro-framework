use std::sync::Arc;

use ferro_database::Value;

use crate::error::OrmError;
use crate::field::{FieldKind, FieldMeta};
use crate::lookup::{Lookup, Transform, parse_key};
use crate::meta::{ModelMeta, is_identifier};
use crate::path::{FieldPath, Hop, resolve_field, resolve_related};
use crate::registry::Registry;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ArithOp {
    Add,
    Sub,
    Mul,
    Div,
    Mod,
}

impl ArithOp {
    pub fn parse(symbol: &str) -> Option<Self> {
        Some(match symbol {
            "+" => Self::Add,
            "-" => Self::Sub,
            "*" => Self::Mul,
            "/" => Self::Div,
            "%" => Self::Mod,
            _ => return None,
        })
    }

    pub fn symbol(self) -> &'static str {
        match self {
            Self::Add => "+",
            Self::Sub => "-",
            Self::Mul => "*",
            Self::Div => "/",
            Self::Mod => "%",
        }
    }
}

/// Database functions usable in expressions (Feature 022).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Func {
    Lower,
    Upper,
    Length,
    Trim,
    Concat,
    Coalesce,
    Greatest,
    Least,
    Abs,
    Now,
}

impl Func {
    pub const ALL: [Func; 10] = [
        Self::Lower,
        Self::Upper,
        Self::Length,
        Self::Trim,
        Self::Concat,
        Self::Coalesce,
        Self::Greatest,
        Self::Least,
        Self::Abs,
        Self::Now,
    ];

    pub fn name(self) -> &'static str {
        match self {
            Self::Lower => "Lower",
            Self::Upper => "Upper",
            Self::Length => "Length",
            Self::Trim => "Trim",
            Self::Concat => "Concat",
            Self::Coalesce => "Coalesce",
            Self::Greatest => "Greatest",
            Self::Least => "Least",
            Self::Abs => "Abs",
            Self::Now => "Now",
        }
    }

    pub fn parse(name: &str) -> Option<Self> {
        Self::ALL.into_iter().find(|f| f.name() == name)
    }

    /// How many arguments the function takes: `(min, max)`.
    pub fn arity(self) -> (usize, usize) {
        match self {
            Self::Now => (0, 0),
            Self::Lower | Self::Upper | Self::Length | Self::Trim | Self::Abs => (1, 1),
            Self::Concat | Self::Coalesce | Self::Greatest | Self::Least => (2, usize::MAX),
        }
    }
}

/// An expression over fields of the query's model: `F()` arithmetic in filters and updates;
/// functions, `Case`/`When`, literals and subqueries in annotations (Feature 022).
#[derive(Debug, Clone)]
pub enum Expr {
    Field(usize),
    Value(Value),
    Binary(Box<Expr>, ArithOp, Box<Expr>),
    /// A literal of a known kind (PostgreSQL needs the type of a bare parameter).
    Literal(Value, FieldKind),
    Func(Func, Vec<Expr>),
    /// `CASE WHEN <condition> THEN <value> ... ELSE <default> END`.
    Case(Vec<(Node, Expr)>, Box<Expr>),
    /// `OuterRef("field")` inside a subquery, before it is attached to its outer query.
    OuterName(String),
    /// A field of the outer query's model (resolved [`Expr::OuterName`]).
    Outer(usize),
    /// A one-column subquery's first value.
    Subquery(Box<Query>),
    Exists(Box<Query>),
}

impl PartialEq for Expr {
    /// Structural equality of the arithmetic forms; conditions and subqueries never compare
    /// equal.
    fn eq(&self, other: &Self) -> bool {
        match (self, other) {
            (Self::Field(a), Self::Field(b)) | (Self::Outer(a), Self::Outer(b)) => a == b,
            (Self::Value(a), Self::Value(b)) => a == b,
            (Self::Literal(a, x), Self::Literal(b, y)) => a == b && x == y,
            (Self::Binary(a, x, b), Self::Binary(c, y, d)) => a == c && x == y && b == d,
            (Self::Func(f, a), Self::Func(g, b)) => f == g && a == b,
            (Self::OuterName(a), Self::OuterName(b)) => a == b,
            _ => false,
        }
    }
}

impl Expr {
    /// Fields of the query's own model referenced directly (not inside conditions).
    pub fn fields(&self, out: &mut Vec<usize>) {
        match self {
            Self::Field(index) => out.push(*index),
            Self::Binary(left, _, right) => {
                left.fields(out);
                right.fields(out);
            }
            Self::Func(_, args) => args.iter().for_each(|arg| arg.fields(out)),
            Self::Case(whens, default) => {
                whens.iter().for_each(|(_, then)| then.fields(out));
                default.fields(out);
            }
            Self::Value(_)
            | Self::Literal(..)
            | Self::OuterName(_)
            | Self::Outer(_)
            | Self::Subquery(_)
            | Self::Exists(_) => {}
        }
    }

    pub fn is_arithmetic(&self) -> bool {
        matches!(self, Self::Binary(..))
    }

    /// The kind of the expression's value, for converting results.
    pub fn output_kind(&self, model: &ModelMeta) -> FieldKind {
        match self {
            Self::Field(index) => model.fields[*index].kind.clone(),
            Self::Value(value) | Self::Literal(value, FieldKind::Json) => value_kind(value),
            Self::Literal(_, kind) => kind.clone(),
            Self::Binary(left, _, right) => {
                let left_kind = left.output_kind(model);
                if is_numeric(&left_kind) {
                    left_kind
                } else {
                    right.output_kind(model)
                }
            }
            Self::Func(func, args) => match func {
                Func::Lower | Func::Upper | Func::Trim | Func::Concat => FieldKind::Text,
                Func::Length => FieldKind::Integer,
                Func::Now => FieldKind::DateTime,
                Func::Abs | Func::Coalesce | Func::Greatest | Func::Least => args
                    .iter()
                    .find(|arg| !matches!(arg, Self::Literal(Value::Null, _)))
                    .map_or(FieldKind::Text, |arg| arg.output_kind(model)),
            },
            Self::Case(whens, default) => whens
                .iter()
                .map(|(_, then)| then)
                .chain(std::iter::once(&**default))
                .find(|e| !matches!(e, Self::Literal(Value::Null, _)))
                .map_or(FieldKind::Text, |e| e.output_kind(model)),
            Self::OuterName(_) | Self::Outer(_) => FieldKind::Text,
            Self::Subquery(inner) => inner
                .subquery_field()
                .map_or(FieldKind::Text, |field| field.kind),
            Self::Exists(_) => FieldKind::Boolean,
        }
    }

    /// Resolves `OuterRef` names inside subqueries against the outer model.
    fn resolve_outer(&mut self, outer: &ModelMeta) -> Result<(), OrmError> {
        match self {
            Self::Subquery(inner) | Self::Exists(inner) => inner.resolve_outer(outer),
            Self::Binary(left, _, right) => {
                left.resolve_outer(outer)?;
                right.resolve_outer(outer)
            }
            Self::Func(_, args) => args.iter_mut().try_for_each(|arg| arg.resolve_outer(outer)),
            Self::Case(whens, default) => {
                for (_, then) in whens.iter_mut() {
                    then.resolve_outer(outer)?;
                }
                default.resolve_outer(outer)
            }
            _ => Ok(()),
        }
    }
}

fn value_kind(value: &Value) -> FieldKind {
    match value {
        Value::Integer(_) => FieldKind::BigInteger,
        Value::Real(_) => FieldKind::Float,
        Value::Decimal(text) => FieldKind::Decimal {
            max_digits: 38,
            decimal_places: u8::try_from(text.split_once('.').map_or(0, |(_, f)| f.len()))
                .unwrap_or(0),
        },
        _ => FieldKind::Text,
    }
}

#[derive(Debug, Clone)]
pub enum Operand {
    Value(Value),
    List(Vec<Value>),
    Flag(bool),
    Expr(Expr),
    /// `field__in=<queryset>`: the values of a one-column query.
    Subquery(Box<Query>),
}

impl PartialEq for Operand {
    /// Subquery operands are never equal (queries have no value equality).
    fn eq(&self, other: &Self) -> bool {
        match (self, other) {
            (Self::Value(a), Self::Value(b)) => a == b,
            (Self::List(a), Self::List(b)) => a == b,
            (Self::Flag(a), Self::Flag(b)) => a == b,
            (Self::Expr(a), Self::Expr(b)) => a == b,
            _ => false,
        }
    }
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
    /// Applied to the target's value before comparing (`__year`, JSON key paths).
    pub transform: Option<Transform>,
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
        let text_like = lookup.is_text() || lookup.is_regex() || lookup == Lookup::HasKey;
        let (lookup, operand) = match (lookup, operand) {
            (Lookup::Exact, Operand::Value(Value::Null)) => (Lookup::IsNull, Operand::Flag(true)),
            (Lookup::IsNull, operand @ Operand::Flag(_)) => (Lookup::IsNull, operand),
            (Lookup::IsNull, _) => return Err(error("needs True or False")),
            (Lookup::In, operand @ (Operand::List(_) | Operand::Subquery(_))) => {
                (Lookup::In, operand)
            }
            (Lookup::In, _) => return Err(error("needs a list of values or a queryset")),
            (Lookup::Range, Operand::List(values)) => {
                if values.len() != 2 || values.contains(&Value::Null) {
                    return Err(error("needs two values (low, high)"));
                }
                (Lookup::Range, Operand::List(values))
            }
            (Lookup::Range, _) => return Err(error("needs two values (low, high)")),
            (Lookup::HasKeys | Lookup::HasAnyKeys, Operand::List(values)) => {
                if values.is_empty() || !values.iter().all(|v| matches!(v, Value::Text(_))) {
                    return Err(error("needs a list of strings"));
                }
                (lookup, Operand::List(values))
            }
            (Lookup::HasKeys | Lookup::HasAnyKeys, _) => {
                return Err(error("needs a list of strings"));
            }
            (_, Operand::Subquery(_)) => {
                return Err(error(&format!("cannot take a queryset; use '{name}__in'")));
            }
            (_, Operand::List(_)) => {
                return Err(error(&format!("cannot take a list; use '{name}__in'")));
            }
            (_, Operand::Flag(_)) => return Err(error("needs a value")),
            (lookup, operand @ Operand::Expr(_)) => {
                if text_like || lookup.is_json_key() {
                    return Err(error("cannot compare with an F() expression"));
                }
                (lookup, operand)
            }
            (_, Operand::Value(Value::Null)) => return Err(error("cannot compare with None")),
            (lookup, Operand::Value(value)) => {
                if text_like && !matches!(value, Value::Text(_)) {
                    return Err(error("needs a string"));
                }
                (lookup, Operand::Value(value))
            }
        };
        Ok(Self {
            target,
            lookup,
            operand,
            transform: None,
        })
    }

    /// The condition with a transform of the target's value.
    pub fn transformed(mut self, transform: Option<Transform>) -> Self {
        self.transform = transform;
        self
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

/// What an annotation computes.
#[derive(Debug, Clone)]
pub enum AnnotationValue {
    Aggregate(Aggregate),
    /// A per-row expression (functions, `Case`, subqueries) and the kind of its value.
    Expr(Expr, FieldKind),
}

#[derive(Debug, Clone)]
pub struct Annotation {
    pub name: String,
    pub value: AnnotationValue,
    /// Computed per group of a `values()` query instead of per record.
    pub grouped: bool,
}

impl Annotation {
    /// A field describing the annotation's value (for converting operands and results).
    pub fn output_field(&self) -> FieldMeta {
        match &self.value {
            AnnotationValue::Aggregate(aggregate) => aggregate.output_field(&self.name),
            AnnotationValue::Expr(_, kind) => {
                let mut field = FieldMeta::column(&self.name, kind.clone());
                field.null = true;
                field
            }
        }
    }

    pub fn aggregate(&self) -> Option<&Aggregate> {
        match &self.value {
            AnnotationValue::Aggregate(aggregate) => Some(aggregate),
            AnnotationValue::Expr(..) => None,
        }
    }
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
    /// `distinct()`: leave out duplicate rows.
    pub distinct: bool,
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
            distinct: false,
        }
    }

    /// `SELECT DISTINCT`: duplicate rows (from joins or `values()`) appear once.
    pub fn distinct(&self) -> Self {
        let mut next = self.clone();
        next.distinct = true;
        next
    }

    /// The single column this query selects when used as `field__in=<queryset>`: its
    /// `values_list()` field, or the model's primary key.
    pub fn subquery_field(&self) -> Result<FieldMeta, OrmError> {
        match &self.values {
            None => Ok(self.model.pk_field().clone()),
            Some(values) if values.fields.len() == 1 => match &values.fields[0] {
                Selected::Field(path) => Ok(path.meta().clone()),
                Selected::Annotation(index) => Ok(self.annotations[*index].output_field()),
            },
            Some(_) => Err(OrmError(format!(
                "{}: a queryset used with __in must select one field (values_list('field'))",
                self.model.label
            ))),
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
                value: AnnotationValue::Aggregate(aggregate),
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

    /// Adds a per-row expression annotation (Feature 022). After `values()` it joins the
    /// selected columns; it cannot be combined with grouped aggregates.
    pub fn annotate_expr(&self, name: &str, mut expr: Expr) -> Result<Self, OrmError> {
        if self.is_locking() {
            return Err(self.lock_error());
        }
        if self.is_grouped() {
            return Err(OrmError(format!(
                "{}: expression annotations cannot follow a grouped annotate()",
                self.model.label
            )));
        }
        self.check_new_name(name)?;
        expr.resolve_outer(&self.model)?;
        let kind = expr.output_kind(&self.model);
        let mut next = self.clone();
        next.annotations.push(Annotation {
            name: name.to_string(),
            value: AnnotationValue::Expr(expr, kind),
            grouped: false,
        });
        if let Some(values) = &mut next.values {
            values.names.push(name.to_string());
            values
                .fields
                .push(Selected::Annotation(next.annotations.len() - 1));
        }
        Ok(next)
    }

    /// The first `OuterRef` name left unresolved: the query uses `OuterRef` without being a
    /// `Subquery()`/`Exists()` of another query.
    pub fn unresolved_outer(&self) -> Option<String> {
        fn in_expr(expr: &Expr) -> Option<String> {
            match expr {
                Expr::OuterName(name) => Some(name.clone()),
                Expr::Binary(left, _, right) => in_expr(left).or_else(|| in_expr(right)),
                _ => None,
            }
        }
        fn in_node(node: &Node) -> Option<String> {
            match node {
                Node::Leaf(condition) => match &condition.operand {
                    Operand::Expr(expr) => in_expr(expr),
                    _ => None,
                },
                Node::And(children) | Node::Or(children) => children.iter().find_map(in_node),
                Node::Not(child) => in_node(child),
            }
        }
        self.filters.iter().find_map(in_node)
    }

    /// Resolves `OuterRef` names in this (inner) query's conditions against `outer`.
    pub fn resolve_outer(&mut self, outer: &ModelMeta) -> Result<(), OrmError> {
        fn walk(node: &mut Node, inner: &ModelMeta, outer: &ModelMeta) -> Result<(), OrmError> {
            match node {
                Node::Leaf(condition) => {
                    if let Operand::Expr(expr) = &mut condition.operand {
                        resolve(expr, inner, outer)?;
                    }
                    Ok(())
                }
                Node::And(children) | Node::Or(children) => children
                    .iter_mut()
                    .try_for_each(|child| walk(child, inner, outer)),
                Node::Not(child) => walk(child, inner, outer),
            }
        }
        fn resolve(expr: &mut Expr, inner: &ModelMeta, outer: &ModelMeta) -> Result<(), OrmError> {
            match expr {
                Expr::OuterName(name) => {
                    if inner.table == outer.table {
                        return Err(OrmError(format!(
                            "{}: OuterRef('{name}') to the same model is not supported",
                            outer.label
                        )));
                    }
                    let index = if name == "pk" {
                        Some(outer.pk)
                    } else {
                        outer.field(name)
                    };
                    let index = index.ok_or_else(|| {
                        OrmError(format!(
                            "{} has no field '{name}' (OuterRef); fields: {}",
                            outer.label,
                            outer.field_names().join(", ")
                        ))
                    })?;
                    *expr = Expr::Outer(index);
                    Ok(())
                }
                Expr::Binary(left, _, right) => {
                    resolve(left, inner, outer)?;
                    resolve(right, inner, outer)
                }
                _ => Ok(()),
            }
        }
        let inner = self.model.clone();
        for node in &mut self.filters {
            walk(node, &inner, outer)?;
        }
        Ok(())
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
