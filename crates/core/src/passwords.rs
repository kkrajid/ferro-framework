//! Password hashing (Feature 007 research R1): Argon2id with OWASP parameters, PHC strings,
//! constant-time verification. Callers run these on a worker pool, never on the event loop.

use std::fmt;

use argon2::password_hash::SaltString;
use argon2::{Algorithm, Argon2, Params, PasswordHash, PasswordHasher, PasswordVerifier, Version};

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PasswordError(pub String);

impl fmt::Display for PasswordError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.0)
    }
}

impl std::error::Error for PasswordError {}

/// Argon2id with fixed cost parameters.
#[derive(Debug, Clone)]
pub struct Hasher {
    params: Params,
}

impl Default for Hasher {
    /// 19 MiB memory, 2 iterations, 1 lane (OWASP 2023 recommendation).
    fn default() -> Self {
        Self::new(19_456, 2, 1).expect("valid default parameters")
    }
}

impl Hasher {
    /// `memory_kib` KiB of memory, `iterations` passes, `lanes` degree of parallelism.
    pub fn new(memory_kib: u32, iterations: u32, lanes: u32) -> Result<Self, PasswordError> {
        let params = Params::new(memory_kib, iterations, lanes, None)
            .map_err(|e| PasswordError(format!("invalid Argon2 parameters: {e}")))?;
        Ok(Self { params })
    }

    fn argon2(&self) -> Argon2<'static> {
        Argon2::new(Algorithm::Argon2id, Version::V0x13, self.params.clone())
    }

    /// A PHC string (`$argon2id$v=19$m=...,t=...,p=...$salt$hash`) with a fresh random salt.
    pub fn hash(&self, password: &str) -> Result<String, PasswordError> {
        let salt = SaltString::encode_b64(uuid::Uuid::new_v4().as_bytes())
            .map_err(|e| PasswordError(e.to_string()))?;
        self.argon2()
            .hash_password(password.as_bytes(), &salt)
            .map(|hash| hash.to_string())
            .map_err(|e| PasswordError(e.to_string()))
    }

    /// Whether `password` matches `stored` (an Argon2id PHC string, any parameters). Anything
    /// else — other algorithms, malformed strings — never matches.
    pub fn verify(&self, password: &str, stored: &str) -> bool {
        let Ok(parsed) = PasswordHash::new(stored) else {
            return false;
        };
        if parsed.algorithm != Algorithm::Argon2id.ident() {
            return false;
        }
        let Ok(params) = Params::try_from(&parsed) else {
            return false;
        };
        Argon2::new(Algorithm::Argon2id, Version::V0x13, params)
            .verify_password(password.as_bytes(), &parsed)
            .is_ok()
    }

    /// Whether `stored` should be replaced by a hash with the current parameters.
    pub fn needs_rehash(&self, stored: &str) -> bool {
        let Ok(parsed) = PasswordHash::new(stored) else {
            return true;
        };
        if parsed.algorithm != Algorithm::Argon2id.ident()
            || parsed.version != Some(0x13)
            || parsed.hash.is_none()
        {
            return true;
        }
        match Params::try_from(&parsed) {
            Ok(params) => {
                params.m_cost() != self.params.m_cost()
                    || params.t_cost() != self.params.t_cost()
                    || params.p_cost() != self.params.p_cost()
            }
            Err(_) => true,
        }
    }
}
