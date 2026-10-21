//! Ferro's router: path pattern parsing and a segment trie with deterministic precedence.
//!
//! - Static segments beat parameter segments, independent of registration order, with
//!   backtracking (`/users/me/posts` still reaches `/users/{id}/posts`).
//! - `HEAD` is answered by the `GET` route.
//! - A path that matches only with its trailing slash toggled yields [`Match::Redirect`].
//! - Request segments are split on `/` first and percent-decoded afterwards, so `%2F` never
//!   splits a segment.

#![forbid(unsafe_code)]

use std::collections::HashMap;
use std::fmt;

use ferro_core::Method;
use percent_encoding::percent_decode_str;

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Segment {
    /// Decoded literal text. The empty string marks a trailing slash (or the root path).
    Static(String),
    Param(String),
    /// `{name:path}`, the last segment: the rest of the path, slashes included (Feature 025).
    Tail(String),
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PathPattern {
    segments: Vec<Segment>,
}

impl PathPattern {
    pub fn segments(&self) -> &[Segment] {
        &self.segments
    }

    pub fn param_names(&self) -> Vec<&str> {
        self.segments
            .iter()
            .filter_map(|s| match s {
                Segment::Param(name) | Segment::Tail(name) => Some(name.as_str()),
                Segment::Static(_) => None,
            })
            .collect()
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum RouteError {
    NoLeadingSlash {
        path: String,
    },
    EmptySegment {
        path: String,
    },
    InvalidParam {
        path: String,
        segment: String,
    },
    PartialParam {
        path: String,
        segment: String,
    },
    DuplicateParam {
        path: String,
        name: String,
    },
    UnsupportedMethod {
        method: String,
    },
    Duplicate {
        method: Method,
        path: String,
        existing_route_id: usize,
    },
}

impl fmt::Display for RouteError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::NoLeadingSlash { path } => {
                write!(f, "invalid route path '{path}': must start with '/'")
            }
            Self::EmptySegment { path } => {
                write!(
                    f,
                    "invalid route path '{path}': empty segment ('//') is not allowed"
                )
            }
            Self::InvalidParam { path, segment } => write!(
                f,
                "invalid route path '{path}': invalid parameter segment '{segment}' \
                 (use '{{name}}' where name is a Python identifier, or '{{name:path}}' as the \
                 last segment for the rest of the path)"
            ),
            Self::PartialParam { path, segment } => write!(
                f,
                "invalid route path '{path}': a parameter must be a whole segment, got '{segment}'"
            ),
            Self::DuplicateParam { path, name } => {
                write!(
                    f,
                    "invalid route path '{path}': duplicate parameter '{name}'"
                )
            }
            Self::UnsupportedMethod { method } => write!(
                f,
                "unsupported method '{method}': use GET, POST, PUT, PATCH, DELETE or OPTIONS \
                 (HEAD is answered by GET routes)"
            ),
            Self::Duplicate { method, path, .. } => {
                write!(f, "duplicate route: {method} {path} is already registered")
            }
        }
    }
}

impl std::error::Error for RouteError {}

fn is_identifier(name: &str) -> bool {
    let mut chars = name.chars();
    matches!(chars.next(), Some(c) if c == '_' || c.is_ascii_alphabetic())
        && chars.all(|c| c == '_' || c.is_ascii_alphanumeric())
}

fn decode(segment: &str) -> String {
    percent_decode_str(segment).decode_utf8_lossy().into_owned()
}

/// Parses and validates a route path such as `/products/{id}`.
pub fn parse_path(path: &str) -> Result<PathPattern, RouteError> {
    let Some(rest) = path.strip_prefix('/') else {
        return Err(RouteError::NoLeadingSlash {
            path: path.to_string(),
        });
    };
    let parts: Vec<&str> = rest.split('/').collect();
    let last = parts.len() - 1;
    let mut segments = Vec::with_capacity(parts.len());
    let mut names: Vec<&str> = Vec::new();
    for (index, part) in parts.iter().enumerate() {
        if part.is_empty() {
            if index == last {
                segments.push(Segment::Static(String::new()));
                continue;
            }
            return Err(RouteError::EmptySegment {
                path: path.to_string(),
            });
        }
        let has_brace = part.contains('{') || part.contains('}');
        if part.len() >= 2 && part.starts_with('{') && part.ends_with('}') {
            let inner = &part[1..part.len() - 1];
            let (name, tail) = match inner.split_once(':') {
                Some((name, "path")) if index == last => (name, true),
                Some(_) => {
                    return Err(RouteError::InvalidParam {
                        path: path.to_string(),
                        segment: part.to_string(),
                    });
                }
                None => (inner, false),
            };
            if !is_identifier(name) {
                return Err(RouteError::InvalidParam {
                    path: path.to_string(),
                    segment: part.to_string(),
                });
            }
            if names.contains(&name) {
                return Err(RouteError::DuplicateParam {
                    path: path.to_string(),
                    name: name.to_string(),
                });
            }
            names.push(name);
            segments.push(if tail {
                Segment::Tail(name.to_string())
            } else {
                Segment::Param(name.to_string())
            });
        } else if has_brace {
            let error = if part.contains('{') && !part.starts_with('{') {
                RouteError::PartialParam {
                    path: path.to_string(),
                    segment: part.to_string(),
                }
            } else {
                RouteError::InvalidParam {
                    path: path.to_string(),
                    segment: part.to_string(),
                }
            };
            return Err(error);
        } else {
            segments.push(Segment::Static(decode(part)));
        }
    }
    Ok(PathPattern { segments })
}

/// Result of routing one request.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Match {
    Found {
        route_id: usize,
        params: Vec<(String, String)>,
    },
    MethodNotAllowed {
        allowed: Vec<Method>,
    },
    Redirect {
        location: String,
    },
    NotFound,
}

#[derive(Debug, Default)]
struct Node {
    statics: HashMap<String, Node>,
    param: Option<Box<Node>>,
    /// A `{name:path}` child: it takes every remaining segment.
    tail: Option<Box<Node>>,
    methods: Vec<(Method, usize)>,
}

impl Node {
    fn route_for(&self, method: Method) -> Option<usize> {
        self.methods
            .iter()
            .find(|(m, _)| *m == method)
            .map(|(_, id)| *id)
    }
}

#[derive(Debug, Default)]
pub struct RouterBuilder {
    root: Node,
    params: HashMap<usize, Vec<String>>,
    templates: HashMap<usize, String>,
}

impl RouterBuilder {
    pub fn add(
        &mut self,
        methods: &[Method],
        path: &str,
        route_id: usize,
    ) -> Result<(), RouteError> {
        if methods.is_empty() {
            return Err(RouteError::UnsupportedMethod {
                method: "(none)".to_string(),
            });
        }
        if let Some(bad) = methods
            .iter()
            .find(|m| matches!(m, Method::Head | Method::Other))
        {
            return Err(RouteError::UnsupportedMethod {
                method: bad.to_string(),
            });
        }
        let pattern = parse_path(path)?;
        let mut node = &mut self.root;
        for segment in pattern.segments() {
            node = match segment {
                Segment::Static(text) => node.statics.entry(text.clone()).or_default(),
                Segment::Param(_) => node.param.get_or_insert_with(Box::default),
                Segment::Tail(_) => node.tail.get_or_insert_with(Box::default),
            };
        }
        for method in methods {
            if let Some(existing_route_id) = node.route_for(*method) {
                return Err(RouteError::Duplicate {
                    method: *method,
                    path: path.to_string(),
                    existing_route_id,
                });
            }
        }
        for method in methods {
            if node.route_for(*method).is_none() {
                node.methods.push((*method, route_id));
            }
        }
        self.templates.insert(route_id, path.to_string());
        self.params.insert(
            route_id,
            pattern
                .param_names()
                .into_iter()
                .map(str::to_string)
                .collect(),
        );
        Ok(())
    }

    pub fn build(self) -> Router {
        Router {
            root: self.root,
            params: self.params,
            templates: self.templates,
        }
    }
}

#[derive(Debug, Default)]
pub struct Router {
    root: Node,
    params: HashMap<usize, Vec<String>>,
    templates: HashMap<usize, String>,
}

impl Router {
    pub fn builder() -> RouterBuilder {
        RouterBuilder::default()
    }

    /// The path pattern a route was registered with (`/items/{id}`), for metric labels.
    pub fn template(&self, route_id: usize) -> Option<&str> {
        self.templates.get(&route_id).map(String::as_str)
    }

    /// One more than the highest registered route id.
    pub fn route_slots(&self) -> usize {
        self.templates.keys().max().map_or(0, |max| max + 1)
    }

    /// Parameter names of a registered route, in pattern order.
    pub fn param_names(&self, route_id: usize) -> &[String] {
        self.params.get(&route_id).map_or(&[], Vec::as_slice)
    }

    pub fn lookup(&self, method: Method, raw_path: &str) -> Match {
        let candidates = self.candidates(raw_path);
        if !candidates.is_empty() {
            let wanted = [
                method,
                if method == Method::Head {
                    Method::Get
                } else {
                    method
                },
            ];
            for want in wanted {
                for (node, values) in &candidates {
                    if let Some(route_id) = node.route_for(want) {
                        let params = self
                            .param_names(route_id)
                            .iter()
                            .cloned()
                            .zip(values.iter().cloned())
                            .collect();
                        return Match::Found { route_id, params };
                    }
                }
            }
            let mut allowed: Vec<Method> = candidates
                .iter()
                .flat_map(|(node, _)| node.methods.iter().map(|(m, _)| *m))
                .collect();
            if allowed.contains(&Method::Get) {
                allowed.push(Method::Head);
            }
            allowed.sort();
            allowed.dedup();
            return Match::MethodNotAllowed { allowed };
        }
        if let Some(alternative) = toggle_trailing_slash(raw_path)
            && !self.candidates(&alternative).is_empty()
        {
            return Match::Redirect {
                location: alternative,
            };
        }
        Match::NotFound
    }

    /// Terminal nodes matching the path, most specific first, with their parameter values.
    fn candidates(&self, raw_path: &str) -> Vec<(&Node, Vec<String>)> {
        let mut out = Vec::new();
        let Some(rest) = raw_path.strip_prefix('/') else {
            return out;
        };
        let segments: Vec<String> = rest.split('/').map(decode).collect();
        let mut values = Vec::new();
        collect(&self.root, &segments, &mut values, &mut out);
        out
    }
}

fn collect<'a>(
    node: &'a Node,
    segments: &[String],
    values: &mut Vec<String>,
    out: &mut Vec<(&'a Node, Vec<String>)>,
) {
    let Some((segment, remaining)) = segments.split_first() else {
        if !node.methods.is_empty() {
            out.push((node, values.clone()));
        }
        return;
    };
    if let Some(child) = node.statics.get(segment) {
        collect(child, remaining, values, out);
    }
    if !segment.is_empty()
        && let Some(child) = &node.param
    {
        values.push(segment.clone());
        collect(child, remaining, values, out);
        values.pop();
    }
    // Lowest precedence: the rest of the path (at least one non-empty segment).
    if let Some(child) = &node.tail
        && !child.methods.is_empty()
        && segments.iter().any(|s| !s.is_empty())
    {
        values.push(segments.join("/"));
        out.push((child, values.clone()));
        values.pop();
    }
}

fn toggle_trailing_slash(raw_path: &str) -> Option<String> {
    if raw_path == "/" || !raw_path.starts_with('/') {
        None
    } else if let Some(stripped) = raw_path.strip_suffix('/') {
        Some(stripped.to_string())
    } else {
        Some(format!("{raw_path}/"))
    }
}
