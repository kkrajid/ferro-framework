use bytes::Bytes;

/// Engine-independent response produced by a dispatcher or by Ferro itself.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct FerroResponse {
    pub status: u16,
    pub headers: Vec<(String, String)>,
    pub body: Bytes,
}

impl FerroResponse {
    pub fn new(status: u16) -> Self {
        Self {
            status,
            headers: Vec::new(),
            body: Bytes::new(),
        }
    }

    pub fn json(status: u16, body: impl Into<Bytes>) -> Self {
        Self::new(status)
            .with_header("content-type", "application/json")
            .with_body(body)
    }

    pub fn with_header(mut self, name: impl Into<String>, value: impl Into<String>) -> Self {
        self.headers.push((name.into(), value.into()));
        self
    }

    pub fn with_body(mut self, body: impl Into<Bytes>) -> Self {
        self.body = body.into();
        self
    }

    /// First value of a header, case-insensitively.
    pub fn header(&self, name: &str) -> Option<&str> {
        self.headers
            .iter()
            .find(|(n, _)| n.eq_ignore_ascii_case(name))
            .map(|(_, v)| v.as_str())
    }

    pub fn has_header(&self, name: &str) -> bool {
        self.header(name).is_some()
    }

    /// Replaces every value of `name` with a single value.
    pub fn set_header(&mut self, name: &str, value: impl Into<String>) {
        self.headers.retain(|(n, _)| !n.eq_ignore_ascii_case(name));
        self.headers.push((name.to_string(), value.into()));
    }
}
