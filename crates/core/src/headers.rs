//! Header rules shared by every engine adapter.

/// Baseline security headers added to every response (Feature 001 FR-027, Feature 006 FR-005).
/// Handlers may override them.
pub fn security_headers() -> &'static [(&'static str, &'static str)] {
    &[
        ("x-content-type-options", "nosniff"),
        ("referrer-policy", "strict-origin-when-cross-origin"),
        ("x-frame-options", "DENY"),
        ("cross-origin-opener-policy", "same-origin"),
    ]
}

/// RFC 9110 `token` characters.
pub fn is_valid_header_name(name: &str) -> bool {
    !name.is_empty()
        && name.bytes().all(|b| {
            b.is_ascii_alphanumeric()
                || matches!(
                    b,
                    b'!' | b'#'
                        | b'$'
                        | b'%'
                        | b'&'
                        | b'\''
                        | b'*'
                        | b'+'
                        | b'-'
                        | b'.'
                        | b'^'
                        | b'_'
                        | b'`'
                        | b'|'
                        | b'~'
                )
        })
}

/// Rejects control characters (CR, LF, NUL, DEL, ...) that enable header injection.
pub fn is_valid_header_value(value: &str) -> bool {
    value
        .bytes()
        .all(|b| b == b'\t' || (b >= 0x20 && b != 0x7f))
}
