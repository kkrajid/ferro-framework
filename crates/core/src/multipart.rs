//! `multipart/form-data` bodies (RFC 7578; Feature 025): file uploads and the fields sent with
//! them. The body is already in memory (bounded by the route's body size limit); this splits
//! it into parts without copying, and refuses inputs that would make parsing expensive.

use std::ops::Range;

use memchr::memmem;

/// Limits that keep a hostile body cheap to reject.
#[derive(Debug, Clone, Copy)]
pub struct Limits {
    pub max_parts: usize,
    /// Bytes of one part's header block.
    pub max_header_bytes: usize,
}

impl Default for Limits {
    fn default() -> Self {
        Self {
            max_parts: 1000,
            max_header_bytes: 16 * 1024,
        }
    }
}

/// One part: its form field `name`, the `filename` for file parts, the declared content type,
/// every header, and where its data lies in the body.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Part {
    pub name: String,
    pub filename: Option<String>,
    pub content_type: Option<String>,
    pub headers: Vec<(String, String)>,
    pub data: Range<usize>,
}

/// The `boundary` parameter of a `multipart/form-data` content type (1 to 70 characters).
pub fn boundary(content_type: &str) -> Option<String> {
    let (media, params) = content_type.split_once(';')?;
    if !media.trim().eq_ignore_ascii_case("multipart/form-data") {
        return None;
    }
    for (key, value) in parameters(params) {
        if key.eq_ignore_ascii_case("boundary") {
            let valid = (1..=70).contains(&value.len())
                && value.bytes().all(|b| b.is_ascii_graphic() || b == b' ')
                && !value.ends_with(' ');
            return valid.then_some(value);
        }
    }
    None
}

/// `key=value` pairs after the first `;` of a header value; quoted values are unescaped.
fn parameters(text: &str) -> Vec<(String, String)> {
    let mut out = Vec::new();
    let bytes = text.as_bytes();
    let mut i = 0;
    while i < bytes.len() {
        while i < bytes.len() && (bytes[i] == b';' || bytes[i].is_ascii_whitespace()) {
            i += 1;
        }
        let start = i;
        while i < bytes.len() && bytes[i] != b'=' && bytes[i] != b';' {
            i += 1;
        }
        let key = text[start..i].trim().to_string();
        if i >= bytes.len() || bytes[i] == b';' {
            if !key.is_empty() {
                out.push((key, String::new()));
            }
            continue;
        }
        i += 1; // '='
        let mut value = String::new();
        if i < bytes.len() && bytes[i] == b'"' {
            i += 1;
            let mut chars = Vec::new();
            while i < bytes.len() && bytes[i] != b'"' {
                if bytes[i] == b'\\' && i + 1 < bytes.len() {
                    i += 1;
                }
                chars.push(bytes[i]);
                i += 1;
            }
            i += 1; // closing quote
            value = String::from_utf8_lossy(&chars).into_owned();
        } else {
            let begin = i;
            while i < bytes.len() && bytes[i] != b';' {
                i += 1;
            }
            value.push_str(text[begin..i].trim());
        }
        out.push((key, value));
    }
    out
}

/// RFC 5987 `filename*=UTF-8''caf%C3%A9.txt`.
fn extended_value(value: &str) -> Option<String> {
    let (charset, rest) = value.split_once('\'')?;
    let (_, encoded) = rest.split_once('\'')?;
    if !charset.eq_ignore_ascii_case("utf-8") {
        return None;
    }
    percent_encoding::percent_decode_str(encoded)
        .decode_utf8()
        .ok()
        .map(|s| s.into_owned())
}

fn parse_headers(block: &[u8]) -> Result<Vec<(String, String)>, String> {
    let text = std::str::from_utf8(block).map_err(|_| "part headers are not valid UTF-8")?;
    let mut headers = Vec::new();
    for line in text.split("\r\n").filter(|line| !line.is_empty()) {
        let (name, value) = line
            .split_once(':')
            .ok_or_else(|| format!("malformed part header {line:?}"))?;
        headers.push((name.trim().to_ascii_lowercase(), value.trim().to_string()));
    }
    Ok(headers)
}

/// Splits `body` into parts. Errors describe the problem for a 400 answer.
pub fn parse(body: &[u8], boundary: &str, limits: Limits) -> Result<Vec<Part>, String> {
    let delimiter = format!("--{boundary}");
    let finder = memmem::Finder::new(delimiter.as_bytes());
    // Data ends at CRLF + delimiter.
    let separator = format!("\r\n--{boundary}");
    let end_finder = memmem::Finder::new(separator.as_bytes());
    let header_end = memmem::Finder::new(b"\r\n\r\n");

    let mut position = finder
        .find(body)
        .ok_or("multipart body without its boundary")?
        + delimiter.len();
    let mut parts = Vec::new();
    loop {
        let rest = &body[position..];
        if rest.starts_with(b"--") {
            return Ok(parts); // closing delimiter; anything after it is ignored
        }
        // Optional whitespace after the delimiter, then CRLF.
        let line_end = memmem::find(rest, b"\r\n").ok_or("multipart body ends inside a part")?;
        if !rest[..line_end].iter().all(|b| *b == b' ' || *b == b'\t') {
            return Err("malformed multipart delimiter".into());
        }
        position += line_end + 2;
        if parts.len() == limits.max_parts {
            return Err(format!("too many parts (more than {})", limits.max_parts));
        }
        let rest = &body[position..];
        let window = &rest[..rest.len().min(limits.max_header_bytes + 4)];
        let (block, data_start) = if rest.starts_with(b"\r\n") {
            (&rest[..0], 2)
        } else {
            let end = header_end
                .find(window)
                .ok_or("part headers are too large or not terminated")?;
            (&rest[..end], end + 4)
        };
        let headers = parse_headers(block)?;
        let start = position + data_start;
        let data_len = end_finder
            .find(&body[start..])
            .ok_or("multipart body ends inside a part")?;
        let data = start..start + data_len;
        position = start + data_len + separator.len();

        let disposition = headers
            .iter()
            .find(|(n, _)| n == "content-disposition")
            .map(|(_, v)| v.as_str())
            .ok_or("part without content-disposition")?;
        let (kind, params) = disposition.split_once(';').unwrap_or((disposition, ""));
        if !kind.trim().eq_ignore_ascii_case("form-data") {
            return Err(format!("unexpected part disposition {:?}", kind.trim()));
        }
        let params = parameters(params);
        let get = |key: &str| {
            params
                .iter()
                .find(|(k, _)| k.eq_ignore_ascii_case(key))
                .map(|(_, v)| v.clone())
        };
        let name = get("name").ok_or("part without a field name")?;
        let filename = get("filename*")
            .and_then(|v| extended_value(&v))
            .or_else(|| get("filename"));
        let content_type = headers
            .iter()
            .find(|(n, _)| n == "content-type")
            .map(|(_, v)| v.clone());
        parts.push(Part {
            name,
            filename,
            content_type,
            headers,
            data,
        });
    }
}

#[cfg(test)]
mod tests {
    use super::{Limits, boundary, parse};

    fn body(parts: &[&str]) -> Vec<u8> {
        let mut out = String::from("preamble\r\n");
        for part in parts {
            out.push_str("--XyZ\r\n");
            out.push_str(part);
            out.push_str("\r\n");
        }
        out.push_str("--XyZ--\r\nepilogue");
        out.into_bytes()
    }

    #[test]
    fn boundaries_come_from_the_content_type() {
        assert_eq!(
            boundary("multipart/form-data; boundary=XyZ").as_deref(),
            Some("XyZ")
        );
        assert_eq!(
            boundary("Multipart/Form-Data; charset=utf-8; boundary=\"a b\"").as_deref(),
            Some("a b")
        );
        assert_eq!(boundary("multipart/form-data"), None);
        assert_eq!(boundary("application/json; boundary=x"), None);
        assert_eq!(
            boundary(&format!("multipart/form-data; boundary={}", "x".repeat(71))),
            None
        );
    }

    #[test]
    fn fields_and_files_are_split() {
        let data = body(&[
            "Content-Disposition: form-data; name=\"title\"\r\n\r\nHello\r\nworld",
            "Content-Disposition: form-data; name=\"file\"; filename=\"a \\\"b\\\".txt\"\r\nContent-Type: text/plain\r\n\r\nline1\r\n--Xy partial",
            "Content-Disposition: form-data; name=\"photo\"; filename=\"x.jpg\"; filename*=UTF-8''caf%C3%A9.jpg\r\n\r\n\x00\x01",
            "Content-Disposition: form-data; name=\"empty\"\r\n\r\n",
        ]);
        let parts = parse(&data, "XyZ", Limits::default()).unwrap();
        assert_eq!(parts.len(), 4);
        assert_eq!(parts[0].name, "title");
        assert_eq!(&data[parts[0].data.clone()], b"Hello\r\nworld");
        assert_eq!(parts[0].filename, None);
        assert_eq!(parts[1].filename.as_deref(), Some("a \"b\".txt"));
        assert_eq!(parts[1].content_type.as_deref(), Some("text/plain"));
        assert_eq!(&data[parts[1].data.clone()], b"line1\r\n--Xy partial");
        assert_eq!(parts[2].filename.as_deref(), Some("caf\u{e9}.jpg"));
        assert_eq!(&data[parts[2].data.clone()], b"\x00\x01");
        assert_eq!(parts[3].data.len(), 0);
    }

    #[test]
    fn malformed_and_hostile_bodies_are_refused() {
        let limits = Limits {
            max_parts: 2,
            max_header_bytes: 64,
        };
        let three = body(&[
            "Content-Disposition: form-data; name=\"a\"\r\n\r\n1",
            "Content-Disposition: form-data; name=\"b\"\r\n\r\n2",
            "Content-Disposition: form-data; name=\"c\"\r\n\r\n3",
        ]);
        assert!(
            parse(&three, "XyZ", limits)
                .unwrap_err()
                .contains("too many parts")
        );
        let long = body(&[&format!(
            "Content-Disposition: form-data; name=\"a\"\r\nX-Pad: {}\r\n\r\n1",
            "p".repeat(100)
        )]);
        assert!(
            parse(&long, "XyZ", limits)
                .unwrap_err()
                .contains("too large")
        );
        assert!(parse(b"no boundary here", "XyZ", limits).is_err());
        assert!(
            parse(
                b"--XyZ\r\nContent-Disposition: form-data; name=\"a\"\r\n\r\nunterminated",
                "XyZ",
                limits
            )
            .is_err()
        );
        let nameless = body(&["Content-Disposition: form-data\r\n\r\n1"]);
        assert!(
            parse(&nameless, "XyZ", limits)
                .unwrap_err()
                .contains("field name")
        );
        let attachment = body(&["Content-Disposition: attachment; name=\"a\"\r\n\r\n1"]);
        assert!(parse(&attachment, "XyZ", limits).is_err());
    }
}
