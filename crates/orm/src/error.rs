use std::fmt;

/// An invalid model definition, field, lookup, ordering or value. Surfaces in Python as
/// `ferro.models.FieldError`.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct OrmError(pub String);

impl OrmError {
    pub fn new(message: impl Into<String>) -> Self {
        Self(message.into())
    }
}

impl fmt::Display for OrmError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.0)
    }
}

impl std::error::Error for OrmError {}
