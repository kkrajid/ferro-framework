use ferro_core::Method;
use ferro_router::{Match, RouteError, Router, parse_path};

fn router(routes: &[(&[Method], &str)]) -> Router {
    let mut builder = Router::builder();
    for (id, (methods, path)) in routes.iter().enumerate() {
        builder.add(methods, path, id).expect("valid route");
    }
    builder.build()
}

fn found(m: Match) -> (usize, Vec<(String, String)>) {
    match m {
        Match::Found { route_id, params } => (route_id, params),
        other => panic!("expected Found, got {other:?}"),
    }
}

fn p(name: &str, value: &str) -> (String, String) {
    (name.to_string(), value.to_string())
}

#[test]
fn parses_valid_paths() {
    assert!(parse_path("/").unwrap().param_names().is_empty());
    assert_eq!(
        parse_path("/products/{id}").unwrap().param_names(),
        vec!["id"]
    );
    assert_eq!(
        parse_path("/a/{x}/b/{y_2}/").unwrap().param_names(),
        vec!["x", "y_2"]
    );
}

#[test]
fn rejects_invalid_paths() {
    let bad = [
        ("products", "must start with '/'"),
        ("/a/{id", "invalid parameter"),
        ("/a/id}", "invalid parameter"),
        ("/a/{}", "invalid parameter"),
        ("/a/{1x}", "invalid parameter"),
        ("/a/{id}/{id}", "duplicate parameter"),
        ("/a//b", "empty segment"),
        ("/a/x{id}", "whole segment"),
    ];
    for (path, expected) in bad {
        let err = parse_path(path).expect_err(path);
        let message = err.to_string();
        assert!(message.contains(expected), "{path}: {message}");
        assert!(message.contains(path), "{path}: {message}");
    }
}

#[test]
fn static_beats_param_regardless_of_order() {
    for routes in [
        [
            (&[Method::Get][..], "/users/me"),
            (&[Method::Get][..], "/users/{id}"),
        ],
        [
            (&[Method::Get][..], "/users/{id}"),
            (&[Method::Get][..], "/users/me"),
        ],
    ] {
        let r = router(&routes);
        let static_id = routes
            .iter()
            .position(|(_, path)| *path == "/users/me")
            .unwrap();
        let (id, params) = found(r.lookup(Method::Get, "/users/me"));
        assert_eq!(id, static_id);
        assert!(params.is_empty());
        let (id, params) = found(r.lookup(Method::Get, "/users/42"));
        assert_ne!(id, static_id);
        assert_eq!(params, vec![p("id", "42")]);
    }
}

#[test]
fn backtracks_from_static_to_param() {
    let r = router(&[
        (&[Method::Get], "/users/me"),
        (&[Method::Get], "/users/{id}/posts"),
    ]);
    let (id, params) = found(r.lookup(Method::Get, "/users/me/posts"));
    assert_eq!(id, 1);
    assert_eq!(params, vec![p("id", "me")]);
}

#[test]
fn method_falls_through_to_less_specific_candidate() {
    let r = router(&[
        (&[Method::Get], "/users/me"),
        (&[Method::Delete], "/users/{id}"),
    ]);
    let (id, params) = found(r.lookup(Method::Delete, "/users/me"));
    assert_eq!(id, 1);
    assert_eq!(params, vec![p("id", "me")]);
}

#[test]
fn head_uses_get_route() {
    let r = router(&[(&[Method::Get], "/")]);
    assert_eq!(found(r.lookup(Method::Head, "/")).0, 0);
}

#[test]
fn method_not_allowed_lists_canonical_methods() {
    let r = router(&[
        (&[Method::Post, Method::Get], "/items"),
        (&[Method::Delete], "/items"),
    ]);
    match r.lookup(Method::Put, "/items") {
        Match::MethodNotAllowed { allowed } => assert_eq!(
            allowed,
            vec![Method::Get, Method::Head, Method::Post, Method::Delete]
        ),
        other => panic!("{other:?}"),
    }
    match r.lookup(Method::Other, "/items") {
        Match::MethodNotAllowed { .. } => {}
        other => panic!("{other:?}"),
    }
}

#[test]
fn trailing_slash_redirects_to_registered_form() {
    let r = router(&[(&[Method::Get], "/products"), (&[Method::Get], "/items/")]);
    assert_eq!(
        r.lookup(Method::Get, "/products/"),
        Match::Redirect {
            location: "/products".into()
        }
    );
    assert_eq!(
        r.lookup(Method::Post, "/items"),
        Match::Redirect {
            location: "/items/".into()
        }
    );
    assert_eq!(found(r.lookup(Method::Get, "/items/")).0, 1);
}

#[test]
fn both_slash_forms_registered_means_no_redirect() {
    let r = router(&[(&[Method::Get], "/a"), (&[Method::Get], "/a/")]);
    assert_eq!(found(r.lookup(Method::Get, "/a")).0, 0);
    assert_eq!(found(r.lookup(Method::Get, "/a/")).0, 1);
}

#[test]
fn not_found() {
    let r = router(&[(&[Method::Get], "/")]);
    assert_eq!(r.lookup(Method::Get, "/nothing/here"), Match::NotFound);
    let empty = Router::builder().build();
    assert_eq!(empty.lookup(Method::Get, "/"), Match::NotFound);
}

#[test]
fn params_are_percent_decoded_after_splitting() {
    let r = router(&[(&[Method::Get], "/files/{name}"), (&[Method::Get], "/a b")]);
    assert_eq!(
        found(r.lookup(Method::Get, "/files/a%20b")).1,
        vec![p("name", "a b")]
    );
    assert_eq!(
        found(r.lookup(Method::Get, "/files/a%2Fb")).1,
        vec![p("name", "a/b")]
    );
    assert_eq!(
        found(r.lookup(Method::Get, "/files/%E0%B4%AE")).1,
        vec![p("name", "\u{0d2e}")]
    );
    assert_eq!(found(r.lookup(Method::Get, "/a%20b")).0, 1);
}

#[test]
fn params_do_not_match_empty_segments() {
    let r = router(&[(&[Method::Get], "/files/{name}")]);
    assert_eq!(
        r.lookup(Method::Get, "/files/"),
        Match::NotFound,
        "an empty segment is not a parameter value"
    );
}

#[test]
fn duplicate_routes_are_rejected() {
    let mut builder = Router::builder();
    builder.add(&[Method::Get], "/a/{x}", 0).unwrap();
    builder.add(&[Method::Post], "/a/{y}", 1).unwrap();
    let err = builder.add(&[Method::Get], "/a/{y}", 2).unwrap_err();
    assert_eq!(
        err,
        RouteError::Duplicate {
            method: Method::Get,
            path: "/a/{y}".into(),
            existing_route_id: 0
        }
    );
}

#[test]
fn head_and_other_cannot_be_registered() {
    let mut builder = Router::builder();
    assert!(matches!(
        builder.add(&[Method::Head], "/", 0),
        Err(RouteError::UnsupportedMethod { .. })
    ));
    assert!(matches!(
        builder.add(&[], "/", 0),
        Err(RouteError::UnsupportedMethod { .. })
    ));
}

#[test]
fn routes_listing() {
    let r = router(&[(&[Method::Get], "/a/{x}"), (&[Method::Post], "/b")]);
    assert_eq!(r.param_names(0), &["x".to_string()]);
    assert!(r.param_names(1).is_empty());
}

#[test]
fn tail_segments_take_the_rest_of_the_path() {
    let r = router(&[
        (&[Method::Get], "/static/{path:path}"),
        (&[Method::Get], "/static/special"),
        (&[Method::Get], "/files/{owner}/{rest:path}"),
    ]);
    assert_eq!(
        found(r.lookup(Method::Get, "/static/css/site.css")),
        (0, vec![p("path", "css/site.css")])
    );
    assert_eq!(found(r.lookup(Method::Get, "/static/special")), (1, vec![]));
    assert_eq!(
        found(r.lookup(Method::Get, "/static/a%20b/c%2Fd.txt")),
        (0, vec![p("path", "a b/c/d.txt")])
    );
    assert_eq!(
        found(r.lookup(Method::Get, "/files/ann/x/y")),
        (2, vec![p("owner", "ann"), p("rest", "x/y")])
    );
    assert_eq!(r.lookup(Method::Get, "/static"), Match::NotFound);
    assert_eq!(
        parse_path("/a/{rest:path}").unwrap().param_names(),
        vec!["rest"]
    );
    assert!(matches!(
        parse_path("/a/{rest:path}/b"),
        Err(RouteError::InvalidParam { .. })
    ));
    assert!(matches!(
        parse_path("/a/{rest:int}"),
        Err(RouteError::InvalidParam { .. })
    ));
}
