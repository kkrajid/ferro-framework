use ferro_core::passwords::{Hasher, PasswordError};

#[test]
fn hashes_are_argon2id_phc_strings_with_fresh_salts() {
    let hasher = Hasher::default();
    let first = hasher.hash("correct horse battery").unwrap();
    let second = hasher.hash("correct horse battery").unwrap();
    assert!(
        first.starts_with("$argon2id$v=19$m=19456,t=2,p=1$"),
        "{first}"
    );
    assert_ne!(first, second, "salts differ");
    assert!(!first.contains("correct horse"));
}

#[test]
fn verification_accepts_only_the_right_password() {
    let hasher = Hasher::default();
    let hash = hasher.hash("s3cret-pass").unwrap();
    assert!(hasher.verify("s3cret-pass", &hash));
    assert!(!hasher.verify("s3cret-Pass", &hash));
    assert!(!hasher.verify("", &hash));
}

#[test]
fn unknown_or_broken_hashes_never_verify() {
    let hasher = Hasher::default();
    for stored in [
        "",
        "plain text",
        "pbkdf2_sha256$600000$salt$hash",
        "$argon2id$v=19$m=19456,t=2,p=1$broken",
        "$2b$12$abcdefghijklmnopqrstuuWHnGEAFsT6sJbT0E1HbX8gZsOLLHdKu",
    ] {
        assert!(!hasher.verify("anything", stored), "{stored}");
        assert!(hasher.needs_rehash(stored), "{stored}");
    }
}

#[test]
fn rehash_is_needed_when_parameters_change() {
    let weak = Hasher::new(8192, 1, 1).unwrap();
    let hash = weak.hash("password-1").unwrap();
    assert!(weak.verify("password-1", &hash));
    assert!(!weak.needs_rehash(&hash));
    let current = Hasher::default();
    assert!(
        current.verify("password-1", &hash),
        "old parameters still verify"
    );
    assert!(current.needs_rehash(&hash));
    assert!(!current.needs_rehash(&current.hash("x").unwrap()));
}

#[test]
fn invalid_parameters_are_rejected() {
    assert!(matches!(Hasher::new(1, 1, 1), Err(PasswordError(_))));
}
