//! Ferro's own ORM core (constitution III): model metadata, relations, the query AST, lookups,
//! the SQL compiler and DDL. No Python and no database driver live here, so generated SQL text
//! and parameters are tested directly.
//!
//! Pipeline: [`ModelMeta`] (in a [`Registry`]) → [`Query`] (built from `field__lookup` keys,
//! following relations) → [`compile`] with a [`compile::Dialect`] →
//! [`ferro_database::Statement`] → a backend.

#![forbid(unsafe_code)]

pub mod compile;
pub mod ddl;
mod error;
mod field;
mod lookup;
mod meta;
mod path;
mod query;
mod registry;

pub use error::OrmError;
pub use field::{FieldKind, FieldMeta, FieldSpec, ForeignKeyRef, OnDelete, Relation, RelationSpec};
pub use lookup::{Lookup, parse_key};
pub use meta::{CompositeIndex, ModelMeta, RESERVED_NAMES, Resolver, is_identifier, snake_case};
pub use path::{FieldPath, Hop, resolve_field, resolve_lookup, resolve_related};
pub use query::{
    AggFunc, Aggregate, Annotation, ArithOp, Condition, Expr, Lock, Node, Operand, Order,
    OrderTarget, Query, Selected, Shape, Target, Values,
};
pub use registry::{ManyToMany, Registry, Reverse};
