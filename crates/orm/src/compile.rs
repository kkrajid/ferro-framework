//! SQL compiler: queries and row operations → [`Statement`]s. Values only ever become parameters;
//! identifiers come from validated metadata and are quoted by the dialect (constitution IV).

use std::fmt::Debug;
use std::sync::Arc;

use ferro_database::{Statement, Value};

use crate::error::OrmError;
use crate::field::{FieldKind, FieldMeta};
use crate::lookup::Lookup;
use crate::meta::ModelMeta;
use crate::path::{FieldPath, Hop};
use crate::query::{
    AggFunc, Aggregate, Annotation, Condition, Expr, Lock, Node, Operand, OrderTarget, Query,
    Selected, Target,
};

/// Database-specific SQL spelling. Adding PostgreSQL (Feature 003) means one more implementation.
pub trait Dialect: Send + Sync + Debug {
    fn name(&self) -> &'static str;

    fn quote(&self, identifier: &str) -> String {
        format!("\"{}\"", identifier.replace('"', "\"\""))
    }

    /// Placeholder for the `index`-th parameter (1-based).
    fn placeholder(&self, index: usize) -> String;

    fn column_type(&self, kind: &FieldKind) -> String;

    /// Full column definition suffix for the implicit auto-incrementing primary key.
    fn auto_primary_key(&self) -> &'static str;

    /// SQL for a text lookup on `column` using `placeholder`, plus the parameter to bind.
    fn text_lookup(
        &self,
        column: &str,
        lookup: Lookup,
        placeholder: &str,
        value: &str,
    ) -> (String, Value);

    /// Clause used when a query has an offset but no limit.
    fn offset_only(&self, placeholder: &str) -> String;

    /// Row-locking clause ending a `select_for_update()` query on `table` (quoted). SQLite has
    /// none: its transactions hold the database write lock from their start.
    fn lock_clause(&self, _table: &str, _lock: Lock) -> String {
        String::new()
    }
}

#[derive(Debug, Clone, Copy, Default)]
pub struct SqliteDialect;

fn like_escape(value: &str) -> String {
    let mut out = String::with_capacity(value.len() + 4);
    for c in value.chars() {
        if matches!(c, '\\' | '%' | '_') {
            out.push('\\');
        }
        out.push(c);
    }
    out
}

fn glob_escape(value: &str) -> String {
    let mut out = String::with_capacity(value.len() + 4);
    for c in value.chars() {
        match c {
            '*' => out.push_str("[*]"),
            '?' => out.push_str("[?]"),
            '[' => out.push_str("[[]"),
            c => out.push(c),
        }
    }
    out
}

impl Dialect for SqliteDialect {
    fn name(&self) -> &'static str {
        "sqlite"
    }

    fn placeholder(&self, _index: usize) -> String {
        "?".to_string()
    }

    fn column_type(&self, kind: &FieldKind) -> String {
        match kind {
            FieldKind::String { max_length } => format!("varchar({max_length})"),
            FieldKind::Text | FieldKind::Json => "text".into(),
            FieldKind::Integer => "integer".into(),
            FieldKind::BigInteger => "bigint".into(),
            FieldKind::Float => "real".into(),
            FieldKind::Decimal {
                max_digits,
                decimal_places,
            } => format!("decimal({max_digits}, {decimal_places})"),
            FieldKind::Boolean => "bool".into(),
            FieldKind::Date => "date".into(),
            FieldKind::DateTime => "datetime".into(),
            FieldKind::Time => "time".into(),
            FieldKind::Uuid => "char(36)".into(),
        }
    }

    fn auto_primary_key(&self) -> &'static str {
        "INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT"
    }

    fn text_lookup(
        &self,
        column: &str,
        lookup: Lookup,
        placeholder: &str,
        value: &str,
    ) -> (String, Value) {
        // GLOB is case-sensitive; LIKE folds ASCII case only (research R6).
        let like = |pattern: String| {
            (
                format!("{column} LIKE {placeholder} ESCAPE '\\'"),
                Value::Text(pattern),
            )
        };
        let glob = |pattern: String| (format!("{column} GLOB {placeholder}"), Value::Text(pattern));
        match lookup {
            Lookup::IExact => like(like_escape(value)),
            Lookup::IContains => like(format!("%{}%", like_escape(value))),
            Lookup::Contains => glob(format!("*{}*", glob_escape(value))),
            Lookup::StartsWith => glob(format!("{}*", glob_escape(value))),
            Lookup::EndsWith => glob(format!("*{}", glob_escape(value))),
            other => unreachable!("{other:?} is not a text lookup"),
        }
    }

    fn offset_only(&self, placeholder: &str) -> String {
        format!(" LIMIT -1 OFFSET {placeholder}")
    }
}

#[derive(Debug, Clone, Copy, Default)]
pub struct PostgresDialect;

impl Dialect for PostgresDialect {
    fn name(&self) -> &'static str {
        "postgresql"
    }

    fn placeholder(&self, index: usize) -> String {
        format!("${index}")
    }

    fn column_type(&self, kind: &FieldKind) -> String {
        match kind {
            FieldKind::String { max_length } => format!("varchar({max_length})"),
            FieldKind::Text => "text".into(),
            FieldKind::Integer => "integer".into(),
            FieldKind::BigInteger => "bigint".into(),
            FieldKind::Float => "double precision".into(),
            FieldKind::Decimal {
                max_digits,
                decimal_places,
            } => format!("numeric({max_digits}, {decimal_places})"),
            FieldKind::Boolean => "boolean".into(),
            FieldKind::Date => "date".into(),
            FieldKind::DateTime => "timestamp with time zone".into(),
            FieldKind::Time => "time".into(),
            FieldKind::Uuid => "uuid".into(),
            FieldKind::Json => "jsonb".into(),
        }
    }

    fn auto_primary_key(&self) -> &'static str {
        "bigint GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY"
    }

    fn text_lookup(
        &self,
        column: &str,
        lookup: Lookup,
        placeholder: &str,
        value: &str,
    ) -> (String, Value) {
        // LIKE is case-sensitive; ILIKE folds case using the database's character type.
        let (operator, pattern) = match lookup {
            Lookup::IExact => ("ILIKE", like_escape(value)),
            Lookup::IContains => ("ILIKE", format!("%{}%", like_escape(value))),
            Lookup::Contains => ("LIKE", format!("%{}%", like_escape(value))),
            Lookup::StartsWith => ("LIKE", format!("{}%", like_escape(value))),
            Lookup::EndsWith => ("LIKE", format!("%{}", like_escape(value))),
            other => unreachable!("{other:?} is not a text lookup"),
        };
        (
            format!("{column} {operator} {placeholder} ESCAPE '\\'"),
            Value::Text(pattern),
        )
    }

    fn offset_only(&self, placeholder: &str) -> String {
        format!(" OFFSET {placeholder}")
    }

    fn lock_clause(&self, table: &str, lock: Lock) -> String {
        // `OF` keeps joined rows (select_related, related ordering) unlocked, which also allows
        // the nullable side of the LEFT JOINs.
        let wait = match lock {
            Lock::Wait => "",
            Lock::NoWait => " NOWAIT",
            Lock::SkipLocked => " SKIP LOCKED",
        };
        format!(" FOR UPDATE OF {table}{wait}")
    }
}

/// Accumulates SQL text and parameters together so placeholders always match. Text is written
/// strictly left to right, so parameters are bound in placeholder order.
struct Sql<'d> {
    dialect: &'d dyn Dialect,
    text: String,
    params: Vec<Value>,
    aliases: usize,
}

impl<'d> Sql<'d> {
    fn new(dialect: &'d dyn Dialect) -> Self {
        Self {
            dialect,
            text: String::with_capacity(128),
            params: Vec::new(),
            aliases: 0,
        }
    }

    fn push(&mut self, text: &str) {
        self.text.push_str(text);
    }

    fn next_placeholder(&self) -> String {
        self.dialect.placeholder(self.params.len() + 1)
    }

    fn bind(&mut self, value: Value) {
        let placeholder = self.next_placeholder();
        self.text.push_str(&placeholder);
        self.params.push(value);
    }

    fn quote(&self, identifier: &str) -> String {
        self.dialect.quote(identifier)
    }

    /// A fresh table alias (`"T1"`, `"T2"`, ...), unique within the statement.
    fn alias(&mut self) -> String {
        self.aliases += 1;
        self.quote(&format!("T{}", self.aliases))
    }

    fn finish(self) -> Statement {
        Statement::new(self.text, self.params)
    }
}

fn int(value: u64) -> Value {
    Value::Integer(i64::try_from(value).unwrap_or(i64::MAX))
}

/// A table in the statement: the model and how its columns are referenced.
#[derive(Clone)]
struct Scope {
    model: Arc<ModelMeta>,
    /// Quoted alias or table name; `None` writes bare column names.
    prefix: Option<String>,
}

impl Scope {
    fn col(&self, sql: &Sql<'_>, field: &FieldMeta) -> String {
        let column = sql.quote(&field.column);
        match &self.prefix {
            Some(prefix) => format!("{prefix}.{column}"),
            None => column,
        }
    }

    fn pk(&self, sql: &Sql<'_>) -> String {
        self.col(sql, self.model.pk_field())
    }
}

/// Entering `hop` from `outer` in a subquery: the FROM text (with the link-table join for
/// many-to-many), the correlation condition and the scope of the related rows.
fn hop_from(sql: &mut Sql<'_>, outer: &Scope, hop: &Hop) -> (String, String, Scope) {
    match hop {
        Hop::Forward { from, field, to } => {
            let alias = sql.alias();
            let inner = Scope {
                model: to.clone(),
                prefix: Some(alias.clone()),
            };
            let from_sql = format!("{} AS {alias}", sql.quote(&to.table));
            let correlation = format!(
                "{} = {}",
                inner.pk(sql),
                outer.col(sql, &from.fields[*field])
            );
            (from_sql, correlation, inner)
        }
        Hop::Reverse { source, field, .. } => {
            let alias = sql.alias();
            let inner = Scope {
                model: source.clone(),
                prefix: Some(alias.clone()),
            };
            let from_sql = format!("{} AS {alias}", sql.quote(&source.table));
            let correlation = format!(
                "{} = {}",
                inner.col(sql, &source.fields[*field]),
                outer.pk(sql)
            );
            (from_sql, correlation, inner)
        }
        Hop::ManyToMany {
            through,
            from_field,
            to_field,
            to,
            ..
        } => {
            let link_alias = sql.alias();
            let link = Scope {
                model: through.clone(),
                prefix: Some(link_alias.clone()),
            };
            let alias = sql.alias();
            let inner = Scope {
                model: to.clone(),
                prefix: Some(alias.clone()),
            };
            let from_sql = format!(
                "{} AS {link_alias} INNER JOIN {} AS {alias} ON {} = {}",
                sql.quote(&through.table),
                sql.quote(&to.table),
                inner.pk(sql),
                link.col(sql, &through.fields[*to_field])
            );
            let correlation = format!(
                "{} = {}",
                link.col(sql, &through.fields[*from_field]),
                outer.pk(sql)
            );
            (from_sql, correlation, inner)
        }
    }
}

/// `[LEFT ]JOIN` text following `hop` from `outer`.
fn hop_join(sql: &mut Sql<'_>, outer: &Scope, hop: &Hop, kind: &str) -> (String, Scope) {
    let (from_sql, correlation, inner) = hop_from(sql, outer, hop);
    match hop {
        // `from_sql` already joins the target to the link table; join the link table itself.
        Hop::ManyToMany { .. } => {
            let (link, rest) = from_sql
                .split_once(" INNER JOIN ")
                .expect("many-to-many FROM joins the target");
            (
                format!("{kind} {link} ON {correlation} {kind} {rest}"),
                inner,
            )
        }
        _ => (format!("{kind} {from_sql} ON {correlation}"), inner),
    }
}

/// LEFT JOINs for single-valued paths (select_related, related ordering, values, aggregates).
struct Joins {
    paths: Vec<(Vec<Hop>, Scope)>,
    text: String,
}

impl Joins {
    fn none() -> Self {
        Self {
            paths: Vec::new(),
            text: String::new(),
        }
    }

    fn plan<'h>(
        sql: &mut Sql<'_>,
        root: &Scope,
        wanted: impl IntoIterator<Item = &'h [Hop]>,
    ) -> Self {
        let mut joins = Self::none();
        for hops in wanted {
            for end in 1..=hops.len() {
                let prefix = &hops[..end];
                if joins.find(prefix).is_some() {
                    continue;
                }
                let parent = joins.scope(root, &prefix[..end - 1]);
                let (text, scope) = hop_join(sql, &parent, &prefix[end - 1], "LEFT JOIN");
                joins.text.push(' ');
                joins.text.push_str(&text);
                joins.paths.push((prefix.to_vec(), scope));
            }
        }
        joins
    }

    fn find(&self, hops: &[Hop]) -> Option<&Scope> {
        self.paths
            .iter()
            .find(|(path, _)| {
                path.len() == hops.len() && path.iter().zip(hops).all(|(a, b)| a.same(b))
            })
            .map(|(_, scope)| scope)
    }

    fn scope(&self, root: &Scope, hops: &[Hop]) -> Scope {
        if hops.is_empty() {
            return root.clone();
        }
        self.find(hops).cloned().expect("planned join")
    }

    fn is_empty(&self) -> bool {
        self.paths.is_empty()
    }
}

/// Everything a statement's expressions need: the root table (as written in this statement and
/// as referenced from subqueries), joins and the query's annotations.
struct Ctx<'q> {
    query: &'q Query,
    root: Scope,
    /// The root qualified by its table name, for correlated subqueries and F() inside them.
    outer: Scope,
    joins: Joins,
}

impl<'q> Ctx<'q> {
    fn new(query: &'q Query, joins: Joins) -> Self {
        let qualified = Scope {
            model: query.model.clone(),
            prefix: Some(quote_table(query)),
        };
        let root = if joins.is_empty() {
            Scope {
                model: query.model.clone(),
                prefix: None,
            }
        } else {
            qualified.clone()
        };
        Self {
            query,
            root,
            outer: qualified,
            joins,
        }
    }

    fn field_column(&self, sql: &Sql<'_>, path: &FieldPath) -> String {
        self.joins
            .scope(&self.root, &path.hops)
            .col(sql, path.meta())
    }
}

fn quote_table(query: &Query) -> String {
    format!("\"{}\"", query.model.table.replace('"', "\"\""))
}

fn write_aggregate_call(sql: &mut Sql<'_>, aggregate: &Aggregate, column: Option<&str>) {
    if aggregate.default.is_some() {
        sql.push("COALESCE(");
    }
    sql.push(aggregate.func.sql());
    sql.push("(");
    match column {
        None => sql.push("*"),
        Some(column) => {
            if aggregate.distinct {
                sql.push("DISTINCT ");
            }
            sql.push(column);
        }
    }
    sql.push(")");
}

fn finish_aggregate(sql: &mut Sql<'_>, aggregate: &Aggregate) {
    if let Some(default) = &aggregate.default {
        sql.push(", ");
        sql.bind(default.clone());
        sql.push(")");
    }
}

/// Writes an annotation's value: a correlated subquery per record, or an aggregate over the
/// joined rows of a group.
fn write_annotation(sql: &mut Sql<'_>, ctx: &Ctx<'_>, index: usize) {
    let annotation = &ctx.query.annotations[index];
    let aggregate = &annotation.aggregate;
    if annotation.grouped {
        let column = aggregate.path.as_ref().map(|p| ctx.field_column(sql, p));
        write_aggregate_call(sql, aggregate, column.as_deref());
        finish_aggregate(sql, aggregate);
        return;
    }
    let path = aggregate
        .path
        .as_ref()
        .expect("per-record annotations follow a relation");
    let (first, rest) = path.hops.split_first().expect("relation path");
    let (from_sql, correlation, mut scope) = hop_from(sql, &ctx.outer, first);
    let mut joins = String::new();
    for hop in rest {
        let (text, inner) = hop_join(sql, &scope, hop, "INNER JOIN");
        joins.push(' ');
        joins.push_str(&text);
        scope = inner;
    }
    let column = scope.col(sql, path.meta());
    if aggregate.default.is_some() {
        sql.push("COALESCE(");
    }
    sql.push("(SELECT ");
    let call = Aggregate {
        default: None,
        ..aggregate.clone()
    };
    write_aggregate_call(sql, &call, Some(&column));
    sql.push(&format!(" FROM {from_sql}{joins} WHERE {correlation})"));
    finish_aggregate(sql, aggregate);
}

fn nullable_annotation(annotation: &Annotation) -> bool {
    annotation.aggregate.func != AggFunc::Count && annotation.aggregate.default.is_none()
}

/// Where a leaf's compared value comes from.
enum Column {
    Sql(String),
    Annotation(usize),
}

fn write_column(sql: &mut Sql<'_>, ctx: &Ctx<'_>, column: &Column) {
    match column {
        Column::Sql(text) => sql.push(text),
        Column::Annotation(index) => write_annotation(sql, ctx, *index),
    }
}

fn write_expr(sql: &mut Sql<'_>, scope: &Scope, expr: &Expr) {
    match expr {
        Expr::Field(index) => {
            let column = scope.col(sql, &scope.model.fields[*index]);
            sql.push(&column);
        }
        Expr::Value(value) => sql.bind(value.clone()),
        Expr::Binary(left, op, right) => {
            sql.push("(");
            write_expr(sql, scope, left);
            sql.push(&format!(" {} ", op.symbol()));
            write_expr(sql, scope, right);
            sql.push(")");
        }
    }
}

/// One comparison. `scope` holds the compared field; `expr_scope` the root fields F() names.
/// Under an odd number of NOTs a nullable comparison also requires non-NULL values, so `NOT`
/// keeps rows whose value is NULL (Django's semantics).
#[allow(clippy::too_many_arguments)]
fn leaf(
    sql: &mut Sql<'_>,
    ctx: &Ctx<'_>,
    scope: &Scope,
    expr_scope: &Scope,
    condition: &Condition,
    negations: usize,
    in_or: bool,
) {
    let (column, nullable) = match &condition.target {
        Target::Field(path) => (Column::Sql(scope.col(sql, path.meta())), path.meta().null),
        Target::Annotation(index) => (
            Column::Annotation(*index),
            nullable_annotation(&ctx.query.annotations[*index]),
        ),
    };
    let mut guards: Vec<Column> = Vec::new();
    if negations % 2 == 1 && condition.lookup != Lookup::IsNull {
        if nullable {
            guards.push(match &column {
                Column::Sql(text) => Column::Sql(text.clone()),
                Column::Annotation(index) => Column::Annotation(*index),
            });
        }
        if let Operand::Expr(expr) = &condition.operand {
            let mut fields = Vec::new();
            expr.fields(&mut fields);
            for index in fields {
                let field = &expr_scope.model.fields[index];
                if field.null {
                    guards.push(Column::Sql(expr_scope.col(sql, field)));
                }
            }
        }
    }
    let wrap = in_or && !guards.is_empty();
    if wrap {
        sql.push("(");
    }
    match (&condition.lookup, &condition.operand) {
        (Lookup::IsNull, Operand::Flag(flag)) => {
            write_column(sql, ctx, &column);
            sql.push(if *flag { " IS NULL" } else { " IS NOT NULL" });
        }
        (Lookup::In, Operand::List(values)) if values.is_empty() => sql.push("0 = 1"),
        (Lookup::In, Operand::List(values)) => {
            write_column(sql, ctx, &column);
            sql.push(" IN (");
            for (i, value) in values.iter().enumerate() {
                if i > 0 {
                    sql.push(", ");
                }
                sql.bind(value.clone());
            }
            sql.push(")");
        }
        (lookup, Operand::Value(Value::Text(text))) if lookup.is_text() => {
            let Column::Sql(column_sql) = &column else {
                unreachable!("text lookups are refused on annotations")
            };
            let placeholder = sql.next_placeholder();
            let (fragment, param) =
                sql.dialect
                    .text_lookup(column_sql, *lookup, &placeholder, text);
            sql.push(&fragment);
            sql.params.push(param);
        }
        (lookup, operand @ (Operand::Value(_) | Operand::Expr(_))) => {
            let op = match lookup {
                Lookup::Gt => ">",
                Lookup::Gte => ">=",
                Lookup::Lt => "<",
                Lookup::Lte => "<=",
                _ => "=",
            };
            write_column(sql, ctx, &column);
            sql.push(&format!(" {op} "));
            match operand {
                Operand::Value(value) => sql.bind(value.clone()),
                Operand::Expr(expr) => write_expr(sql, expr_scope, expr),
                _ => unreachable!(),
            }
        }
        (lookup, operand) => unreachable!("validated condition {lookup:?} {operand:?}"),
    }
    for guard in &guards {
        sql.push(" AND ");
        write_column(sql, ctx, guard);
        sql.push(" IS NOT NULL");
    }
    if wrap {
        sql.push(")");
    }
}

/// `EXISTS (SELECT 1 FROM <hop> ... [AND EXISTS (<next hop> ...)])`, calling `last` inside the
/// innermost subquery to add its conditions.
fn exists_chain(
    sql: &mut Sql<'_>,
    outer: &Scope,
    hops: &[Hop],
    last: &mut dyn FnMut(&mut Sql<'_>, &Scope),
) {
    let (from_sql, correlation, inner) = hop_from(sql, outer, &hops[0]);
    sql.push(&format!(
        "EXISTS (SELECT 1 FROM {from_sql} WHERE {correlation}"
    ));
    if hops.len() > 1 {
        sql.push(" AND ");
        exists_chain(sql, &inner, &hops[1..], last);
    } else {
        last(sql, &inner);
    }
    sql.push(")");
}

/// `path__isnull=True` across relations: no related row, or a related row whose value is NULL.
fn related_isnull(sql: &mut Sql<'_>, outer: &Scope, condition: &Condition, depth: usize) {
    let Target::Field(path) = &condition.target else {
        unreachable!("relation paths are fields")
    };
    let hops = &path.hops[depth..];
    if path.is_related_pk() {
        sql.push("NOT ");
        exists_chain(sql, outer, hops, &mut |_, _| {});
        return;
    }
    sql.push("(NOT ");
    exists_chain(sql, outer, hops, &mut |_, _| {});
    sql.push(" OR ");
    let field = path.meta().clone();
    exists_chain(sql, outer, hops, &mut |sql, inner| {
        let column = inner.col(sql, &field);
        sql.push(&format!(" AND {column} IS NULL"));
    });
    sql.push(")");
}

fn is_related_isnull(condition: &Condition, depth: usize) -> bool {
    condition.hops().len() > depth
        && condition.lookup == Lookup::IsNull
        && condition.operand == Operand::Flag(true)
}

/// Conditions combined with AND at `scope` (the root, or a related row inside a subquery when
/// `depth > 0`). Conditions following the same relation share one EXISTS, so they must match
/// the same related row (Django's same-`filter()` rule).
#[allow(clippy::too_many_arguments)]
fn conjunction(
    sql: &mut Sql<'_>,
    ctx: &Ctx<'_>,
    scope: &Scope,
    conditions: &[&Condition],
    depth: usize,
    negations: usize,
    in_or: bool,
    first: &mut bool,
) {
    let expr_scope = if depth == 0 {
        ctx.root.clone()
    } else {
        ctx.outer.clone()
    };
    let mut done = vec![false; conditions.len()];
    for i in 0..conditions.len() {
        if done[i] {
            continue;
        }
        done[i] = true;
        let condition = conditions[i];
        if !*first {
            sql.push(" AND ");
        }
        *first = false;
        let remaining = &condition.hops()[depth.min(condition.hops().len())..];
        if remaining.is_empty() {
            leaf(sql, ctx, scope, &expr_scope, condition, negations, in_or);
            continue;
        }
        // Subqueries refer to the root by its table name: bare column names inside them would
        // resolve to the subquery's own tables.
        let correlated = if depth == 0 { &ctx.outer } else { scope };
        if is_related_isnull(condition, depth) {
            related_isnull(sql, correlated, condition, depth);
            continue;
        }
        let mut group = vec![condition];
        for j in i + 1..conditions.len() {
            let other = conditions[j];
            if !done[j]
                && !is_related_isnull(other, depth)
                && other.hops().len() > depth
                && other.hops()[depth].same(&remaining[0])
            {
                done[j] = true;
                group.push(other);
            }
        }
        let (from_sql, correlation, inner) = hop_from(sql, correlated, &remaining[0]);
        sql.push(&format!(
            "EXISTS (SELECT 1 FROM {from_sql} WHERE {correlation}"
        ));
        let mut inner_first = false;
        conjunction(
            sql,
            ctx,
            &inner,
            &group,
            depth + 1,
            0,
            false,
            &mut inner_first,
        );
        sql.push(")");
    }
}

fn node(sql: &mut Sql<'_>, ctx: &Ctx<'_>, node: &Node, negations: usize, in_or: bool) {
    match node {
        Node::Leaf(condition) => {
            let mut first = true;
            conjunction(
                sql,
                ctx,
                &ctx.root.clone(),
                &[condition],
                0,
                negations,
                in_or,
                &mut first,
            );
        }
        Node::And(children) => {
            sql.push("(");
            let leaves: Vec<&Condition> = children
                .iter()
                .filter_map(|c| match c {
                    Node::Leaf(condition) => Some(condition),
                    _ => None,
                })
                .collect();
            let mut first = true;
            conjunction(
                sql,
                ctx,
                &ctx.root.clone(),
                &leaves,
                0,
                negations,
                false,
                &mut first,
            );
            for child in children.iter().filter(|c| !matches!(c, Node::Leaf(_))) {
                if !first {
                    sql.push(" AND ");
                }
                first = false;
                self::node(sql, ctx, child, negations, false);
            }
            sql.push(")");
        }
        Node::Or(children) => {
            sql.push("(");
            for (i, child) in children.iter().enumerate() {
                if i > 0 {
                    sql.push(" OR ");
                }
                self::node(sql, ctx, child, negations, true);
            }
            sql.push(")");
        }
        Node::Not(child) => {
            sql.push("NOT ");
            let bare = matches!(**child, Node::Leaf(_));
            if bare {
                sql.push("(");
            }
            self::node(sql, ctx, child, negations + 1, false);
            if bare {
                sql.push(")");
            }
        }
    }
}

fn write_nodes(sql: &mut Sql<'_>, ctx: &Ctx<'_>, keyword: &str, nodes: &[&Node]) {
    if nodes.is_empty() {
        return;
    }
    sql.push(keyword);
    for (i, item) in nodes.iter().enumerate() {
        if i > 0 {
            sql.push(" AND ");
        }
        let bare = matches!(item, Node::Leaf(_));
        if bare {
            sql.push("(");
        }
        node(sql, ctx, item, 0, false);
        if bare {
            sql.push(")");
        }
    }
}

/// WHERE (and, for grouped queries, HAVING for conditions on annotations).
fn where_clause(sql: &mut Sql<'_>, ctx: &Ctx<'_>) {
    let grouped = ctx.query.is_grouped();
    let nodes: Vec<&Node> = ctx
        .query
        .filters
        .iter()
        .filter(|n| !(grouped && n.uses_annotation()))
        .collect();
    write_nodes(sql, ctx, " WHERE ", &nodes);
}

fn group_clause(sql: &mut Sql<'_>, ctx: &Ctx<'_>) {
    if !ctx.query.is_grouped() {
        return;
    }
    let Some(values) = &ctx.query.values else {
        return;
    };
    let columns: Vec<String> = values
        .fields
        .iter()
        .filter_map(|selected| match selected {
            Selected::Field(path) => Some(ctx.field_column(sql, path)),
            Selected::Annotation(_) => None,
        })
        .collect();
    if !columns.is_empty() {
        sql.push(&format!(" GROUP BY {}", columns.join(", ")));
    }
    let having: Vec<&Node> = ctx
        .query
        .filters
        .iter()
        .filter(|n| n.uses_annotation())
        .collect();
    write_nodes(sql, ctx, " HAVING ", &having);
}

fn order_clause(sql: &mut Sql<'_>, ctx: &Ctx<'_>) {
    if ctx.query.ordering.is_empty() {
        return;
    }
    sql.push(" ORDER BY ");
    for (i, order) in ctx.query.ordering.iter().enumerate() {
        if i > 0 {
            sql.push(", ");
        }
        match &order.target {
            OrderTarget::Field(path) => {
                let column = ctx.field_column(sql, path);
                sql.push(&column);
            }
            OrderTarget::Annotation(index) => write_annotation(sql, ctx, *index),
        }
        sql.push(if order.descending { " DESC" } else { " ASC" });
    }
}

fn window_clause(sql: &mut Sql<'_>, query: &Query) {
    match (query.limit, query.offset) {
        (Some(limit), Some(offset)) => {
            sql.push(" LIMIT ");
            sql.bind(int(limit));
            sql.push(" OFFSET ");
            sql.bind(int(offset));
        }
        (Some(limit), None) => {
            sql.push(" LIMIT ");
            sql.bind(int(limit));
        }
        (None, Some(offset)) => {
            let placeholder = sql.next_placeholder();
            let clause = sql.dialect.offset_only(&placeholder);
            sql.push(&clause);
            sql.params.push(int(offset));
        }
        (None, None) => {}
    }
}

fn ordering_paths(query: &Query) -> impl Iterator<Item = &[Hop]> {
    query.ordering.iter().filter_map(|o| match &o.target {
        OrderTarget::Field(path) if !path.hops.is_empty() => Some(path.hops.as_slice()),
        _ => None,
    })
}

fn grouped_paths(query: &Query) -> impl Iterator<Item = &[Hop]> {
    query
        .annotations
        .iter()
        .filter(|a| a.grouped)
        .filter_map(|a| a.aggregate.path.as_ref().map(|p| p.hops.as_slice()))
}

#[derive(Clone, Copy, PartialEq, Eq)]
enum Columns {
    /// Everything the caller reads: model fields (+ related, annotations) or the values().
    Full,
    /// The primary key only (counting and existence over a window).
    Key,
}

fn select_into(sql: &mut Sql<'_>, query: &Query, columns: Columns) {
    let root_scope = Scope {
        model: query.model.clone(),
        prefix: Some(quote_table(query)),
    };
    let mut wanted: Vec<&[Hop]> = Vec::new();
    if columns == Columns::Full {
        match &query.values {
            Some(values) => {
                for selected in &values.fields {
                    if let Selected::Field(path) = selected
                        && !path.hops.is_empty()
                    {
                        wanted.push(&path.hops);
                    }
                }
                wanted.extend(grouped_paths(query));
            }
            None => wanted.extend(query.related.iter().map(Vec::as_slice)),
        }
    }
    wanted.extend(ordering_paths(query));
    let joins = Joins::plan(sql, &root_scope, wanted);
    let ctx = Ctx::new(query, joins);
    sql.push("SELECT ");
    let mut first = true;
    let mut separator = |sql: &mut Sql<'_>| {
        if !first {
            sql.push(", ");
        }
        first = false;
    };
    match (columns, &query.values) {
        (Columns::Key, _) => {
            separator(sql);
            let pk = ctx.root.pk(sql);
            sql.push(&pk);
        }
        (Columns::Full, Some(values)) => {
            for selected in &values.fields {
                separator(sql);
                match selected {
                    Selected::Field(path) => {
                        let column = ctx.field_column(sql, path);
                        sql.push(&column);
                    }
                    Selected::Annotation(index) => write_annotation(sql, &ctx, *index),
                }
            }
        }
        (Columns::Full, None) => {
            for field in &query.model.fields {
                separator(sql);
                let column = ctx.root.col(sql, field);
                sql.push(&column);
            }
            for path in &query.related {
                let scope = ctx.joins.scope(&ctx.root, path);
                for field in &scope.model.fields {
                    separator(sql);
                    let column = scope.col(sql, field);
                    sql.push(&column);
                }
            }
            for (index, _) in query.annotations.iter().enumerate() {
                separator(sql);
                write_annotation(sql, &ctx, index);
            }
        }
    }
    sql.push(&format!(" FROM {}{}", quote_table(query), ctx.joins.text));
    where_clause(sql, &ctx);
    group_clause(sql, &ctx);
    order_clause(sql, &ctx);
    window_clause(sql, query);
}

pub fn select(dialect: &dyn Dialect, query: &Query) -> Statement {
    let mut sql = Sql::new(dialect);
    select_into(&mut sql, query, Columns::Full);
    if let Some(lock) = query.lock {
        let clause = dialect.lock_clause(&quote_table(query), lock);
        sql.push(&clause);
    }
    sql.finish()
}

/// Filters of a query compiled against the bare table (no joins): COUNT, UPDATE, DELETE.
fn plain_ctx(query: &Query) -> Ctx<'_> {
    Ctx::new(query, Joins::none())
}

pub fn count(dialect: &dyn Dialect, query: &Query) -> Statement {
    let mut sql = Sql::new(dialect);
    if query.is_grouped() || query.is_windowed() {
        sql.push("SELECT COUNT(*) FROM (");
        let columns = if query.is_grouped() {
            Columns::Full
        } else {
            Columns::Key
        };
        select_into(&mut sql, query, columns);
        sql.push(r#") AS "subquery""#);
    } else {
        let table = quote_table(query);
        sql.push(&format!("SELECT COUNT(*) FROM {table}"));
        where_clause(&mut sql, &plain_ctx(query));
    }
    sql.finish()
}

pub fn exists(dialect: &dyn Dialect, query: &Query) -> Statement {
    let mut sql = Sql::new(dialect);
    if query.is_grouped() || query.is_windowed() {
        sql.push("SELECT 1 FROM (");
        let columns = if query.is_grouped() {
            Columns::Full
        } else {
            Columns::Key
        };
        select_into(&mut sql, query, columns);
        sql.push(r#") AS "subquery" LIMIT 1"#);
    } else {
        let table = quote_table(query);
        sql.push(&format!("SELECT 1 FROM {table}"));
        where_clause(&mut sql, &plain_ctx(query));
        sql.push(" LIMIT 1");
    }
    sql.finish()
}

/// `aggregate(...)`: one row with each aggregate, over the filtered (and sliced) query.
pub fn aggregate(
    dialect: &dyn Dialect,
    query: &Query,
    aggregates: &[Aggregate],
) -> Result<Statement, OrmError> {
    if query.is_grouped() {
        return Err(OrmError(format!(
            "{}: aggregate() cannot be used on a grouped values().annotate() query",
            query.model.label
        )));
    }
    if aggregates.is_empty() {
        return Err(OrmError(format!(
            "{}: aggregate() needs at least one aggregate",
            query.model.label
        )));
    }
    for aggregate in aggregates {
        if let Some(path) = &aggregate.path
            && !path.single_valued()
        {
            return Err(OrmError(format!(
                "{}: aggregate() cannot follow many-valued relations; aggregate the related \
                 model instead (e.g. Item.objects.filter(order__...).aggregate(...))",
                query.model.label
            )));
        }
    }
    let mut sql = Sql::new(dialect);
    let root_scope = Scope {
        model: query.model.clone(),
        prefix: Some(quote_table(query)),
    };
    let wanted: Vec<&[Hop]> = aggregates
        .iter()
        .filter_map(|a| a.path.as_ref().map(|p| p.hops.as_slice()))
        .chain(if query.is_windowed() {
            ordering_paths(query).collect::<Vec<_>>()
        } else {
            Vec::new()
        })
        .collect();
    let joins = Joins::plan(&mut sql, &root_scope, wanted);
    let ctx = Ctx::new(query, joins);
    if query.is_windowed() {
        // Aggregate over the window: select the aggregated columns in a subquery first.
        sql.push("SELECT ");
        for (i, aggregate) in aggregates.iter().enumerate() {
            if i > 0 {
                sql.push(", ");
            }
            let column = aggregate
                .path
                .as_ref()
                .map(|_| format!(r#""subquery"."c{i}""#));
            write_aggregate_call(&mut sql, aggregate, column.as_deref());
            finish_aggregate(&mut sql, aggregate);
        }
        sql.push(" FROM (SELECT ");
        let pk = ctx.root.pk(&sql);
        sql.push(&pk);
        for (i, aggregate) in aggregates.iter().enumerate() {
            if let Some(path) = &aggregate.path {
                let column = ctx.field_column(&sql, path);
                sql.push(&format!(r#", {column} AS "c{i}""#));
            }
        }
        sql.push(&format!(" FROM {}{}", quote_table(query), ctx.joins.text));
        where_clause(&mut sql, &ctx);
        order_clause(&mut sql, &ctx);
        window_clause(&mut sql, query);
        sql.push(r#") AS "subquery""#);
    } else {
        sql.push("SELECT ");
        for (i, aggregate) in aggregates.iter().enumerate() {
            if i > 0 {
                sql.push(", ");
            }
            let column = aggregate.path.as_ref().map(|p| ctx.field_column(&sql, p));
            write_aggregate_call(&mut sql, aggregate, column.as_deref());
            finish_aggregate(&mut sql, aggregate);
        }
        sql.push(&format!(" FROM {}{}", quote_table(query), ctx.joins.text));
        where_clause(&mut sql, &ctx);
    }
    Ok(sql.finish())
}

/// A value written by UPDATE: a constant or an `F()` expression.
#[derive(Debug, Clone, PartialEq)]
pub enum Assignment {
    Value(Value),
    Expr(Expr),
}

fn assignments(sql: &mut Sql<'_>, scope: &Scope, values: &[(usize, Assignment)]) {
    for (i, (field, value)) in values.iter().enumerate() {
        if i > 0 {
            sql.push(", ");
        }
        let column = sql.quote(&scope.model.fields[*field].column);
        sql.push(&format!("{column} = "));
        match value {
            Assignment::Value(value) => sql.bind(value.clone()),
            Assignment::Expr(expr) => write_expr(sql, scope, expr),
        }
    }
}

fn value_assignments(sql: &mut Sql<'_>, meta: &ModelMeta, values: &[(usize, Value)]) {
    for (i, (field, value)) in values.iter().enumerate() {
        if i > 0 {
            sql.push(", ");
        }
        let column = sql.quote(&meta.fields[*field].column);
        sql.push(&format!("{column} = "));
        sql.bind(value.clone());
    }
}

fn check_writable(query: &Query, action: &str) -> Result<(), OrmError> {
    if query.is_windowed() {
        return Err(OrmError(format!(
            "{}: cannot {action} a query once a slice has been taken",
            query.model.label
        )));
    }
    if query.is_grouped() {
        return Err(OrmError(format!(
            "{}: cannot {action} a grouped values().annotate() query",
            query.model.label
        )));
    }
    Ok(())
}

pub fn update(
    dialect: &dyn Dialect,
    query: &Query,
    values: &[(usize, Assignment)],
) -> Result<Statement, OrmError> {
    check_writable(query, "update")?;
    if values.is_empty() {
        return Err(OrmError(format!(
            "{}: update() needs at least one field",
            query.model.label
        )));
    }
    let mut sql = Sql::new(dialect);
    let ctx = plain_ctx(query);
    sql.push(&format!("UPDATE {} SET ", quote_table(query)));
    assignments(&mut sql, &ctx.root, values);
    where_clause(&mut sql, &ctx);
    Ok(sql.finish())
}

pub fn delete(dialect: &dyn Dialect, query: &Query) -> Result<Statement, OrmError> {
    check_writable(query, "delete")?;
    let mut sql = Sql::new(dialect);
    sql.push(&format!("DELETE FROM {}", quote_table(query)));
    where_clause(&mut sql, &plain_ctx(query));
    Ok(sql.finish())
}

pub fn insert(
    dialect: &dyn Dialect,
    meta: &ModelMeta,
    values: &[(usize, Value)],
    returning_pk: bool,
) -> Statement {
    insert_with(dialect, meta, values, returning_pk, false)
}

/// INSERT; with `ignore_conflicts`, rows violating a unique constraint are skipped
/// (`ON CONFLICT DO NOTHING`).
pub fn insert_with(
    dialect: &dyn Dialect,
    meta: &ModelMeta,
    values: &[(usize, Value)],
    returning_pk: bool,
    ignore_conflicts: bool,
) -> Statement {
    let mut sql = Sql::new(dialect);
    let table = sql.quote(&meta.table);
    if values.is_empty() {
        sql.push(&format!("INSERT INTO {table} DEFAULT VALUES"));
    } else {
        let columns: Vec<String> = values
            .iter()
            .map(|(f, _)| sql.quote(&meta.fields[*f].column))
            .collect();
        sql.push(&format!(
            "INSERT INTO {table} ({}) VALUES (",
            columns.join(", ")
        ));
        for (i, (_, value)) in values.iter().enumerate() {
            if i > 0 {
                sql.push(", ");
            }
            sql.bind(value.clone());
        }
        sql.push(")");
    }
    if ignore_conflicts {
        sql.push(" ON CONFLICT DO NOTHING");
    }
    if returning_pk {
        let pk = sql.quote(&meta.pk_field().column);
        sql.push(&format!(" RETURNING {pk}"));
    }
    sql.finish()
}

pub fn update_row(
    dialect: &dyn Dialect,
    meta: &ModelMeta,
    values: &[(usize, Value)],
    pk: Value,
) -> Statement {
    let mut sql = Sql::new(dialect);
    let table = sql.quote(&meta.table);
    sql.push(&format!("UPDATE {table} SET "));
    value_assignments(&mut sql, meta, values);
    let pk_column = sql.quote(&meta.pk_field().column);
    sql.push(&format!(" WHERE {pk_column} = "));
    sql.bind(pk);
    sql.finish()
}

pub fn delete_row(dialect: &dyn Dialect, meta: &ModelMeta, pk: Value) -> Statement {
    let mut sql = Sql::new(dialect);
    let table = sql.quote(&meta.table);
    let pk_column = sql.quote(&meta.pk_field().column);
    sql.push(&format!("DELETE FROM {table} WHERE {pk_column} = "));
    sql.bind(pk);
    sql.finish()
}
