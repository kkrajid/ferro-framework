//! Request ids (FR-026): propagate a well-formed incoming `x-request-id`, otherwise generate one.

pub fn generate() -> String {
    uuid::Uuid::new_v4().simple().to_string()
}

pub fn is_valid(id: &str) -> bool {
    (1..=128).contains(&id.len())
        && id
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || matches!(b, b'.' | b'_' | b'-'))
}

pub fn accept_or_generate(incoming: Option<&str>) -> String {
    match incoming {
        Some(id) if is_valid(id) => id.to_string(),
        _ => generate(),
    }
}
