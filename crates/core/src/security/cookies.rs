//! `Cookie` request header parsing (RFC 6265 §5.4, lenient like browsers).

/// `name=value` pairs in order; entries without `=` are skipped, quotes around values removed.
pub fn parse_cookies(header: &str) -> Vec<(String, String)> {
    header
        .split(';')
        .filter_map(|part| {
            let (name, value) = part.split_once('=')?;
            let name = name.trim();
            if name.is_empty() {
                return None;
            }
            let value = value.trim();
            let value = value
                .strip_prefix('"')
                .and_then(|v| v.strip_suffix('"'))
                .unwrap_or(value);
            Some((name.to_string(), value.to_string()))
        })
        .collect()
}
