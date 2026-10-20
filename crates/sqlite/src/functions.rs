//! SQL functions SQLite lacks, registered on every connection (Feature 022): `REGEXP` (SQLite
//! calls `regexp(pattern, text)` for `text REGEXP pattern`) and
//! `ferro_json_contains(document, candidate)`, PostgreSQL's `@>` for JSON documents.

use regex::Regex;
use rusqlite::Connection;
use rusqlite::functions::{Context, FunctionFlags};
use rusqlite::types::ValueRef;
use serde_json::Value as Json;

type BoxError = Box<dyn std::error::Error + Send + Sync + 'static>;

/// Text of argument `index`; numbers are read as their text. `None` for NULL.
fn text(ctx: &Context<'_>, index: usize) -> rusqlite::Result<Option<String>> {
    Ok(match ctx.get_raw(index) {
        ValueRef::Null => None,
        ValueRef::Text(bytes) | ValueRef::Blob(bytes) => {
            Some(String::from_utf8_lossy(bytes).into_owned())
        }
        ValueRef::Integer(value) => Some(value.to_string()),
        ValueRef::Real(value) => Some(value.to_string()),
    })
}

pub(crate) fn register(connection: &Connection) -> rusqlite::Result<()> {
    let flags = FunctionFlags::SQLITE_UTF8 | FunctionFlags::SQLITE_DETERMINISTIC;
    connection.create_scalar_function("regexp", 2, flags, |ctx| {
        // The compiled pattern is cached for the statement (same pattern for every row).
        let regex = ctx.get_or_create_aux(0, |pattern| -> Result<Regex, BoxError> {
            Ok(Regex::new(pattern.as_str()?)?)
        })?;
        Ok(text(ctx, 1)?.map(|value| regex.is_match(&value)))
    })?;
    connection.create_scalar_function("ferro_json_contains", 2, flags, |ctx| {
        let (Some(document), Some(candidate)) = (text(ctx, 0)?, text(ctx, 1)?) else {
            return Ok(None);
        };
        let parse = |text: &str| {
            serde_json::from_str::<Json>(text)
                .map_err(|err| rusqlite::Error::UserFunctionError(Box::new(err)))
        };
        Ok(Some(contains(
            &parse(&document)?,
            &parse(&candidate)?,
            true,
        )))
    })?;
    Ok(())
}

fn same_scalar(a: &Json, b: &Json) -> bool {
    match (a.as_f64(), b.as_f64()) {
        (Some(x), Some(y)) if a.is_number() && b.is_number() => x == y,
        _ => a == b,
    }
}

/// PostgreSQL's `jsonb @>`: objects contain the candidate's pairs (recursively), arrays contain
/// each candidate element somewhere, and a top-level array contains a matching scalar.
fn contains(document: &Json, candidate: &Json, top: bool) -> bool {
    match (document, candidate) {
        (Json::Object(document), Json::Object(candidate)) => {
            candidate.iter().all(|(key, value)| {
                document
                    .get(key)
                    .is_some_and(|inner| contains(inner, value, false))
            })
        }
        (Json::Array(document), Json::Array(candidate)) => candidate
            .iter()
            .all(|value| document.iter().any(|inner| contains(inner, value, false))),
        (Json::Array(document), scalar) if top && !scalar.is_object() => {
            document.iter().any(|inner| same_scalar(inner, scalar))
        }
        (document, candidate) => same_scalar(document, candidate),
    }
}

#[cfg(test)]
mod tests {
    use super::contains;

    fn check(document: &str, candidate: &str) -> bool {
        contains(
            &serde_json::from_str(document).unwrap(),
            &serde_json::from_str(candidate).unwrap(),
            true,
        )
    }

    #[test]
    fn containment_follows_postgresql() {
        assert!(check(
            r#"{"a": 1, "b": {"c": [1, 2]}}"#,
            r#"{"b": {"c": [2]}}"#
        ));
        assert!(!check(r#"{"a": 1}"#, r#"{"a": 2}"#));
        assert!(check("[1, 2, 3]", "[3, 1]"));
        assert!(check("[1, 2, 3]", "2"));
        assert!(
            !check(r#"{"a": [1, 2]}"#, r#"{"a": 1}"#),
            "only top-level arrays"
        );
        assert!(check(r#"{"n": 1}"#, r#"{"n": 1.0}"#));
        assert!(check(r#"{"a": 1}"#, "{}"));
    }
}
