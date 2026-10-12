//! TLS for the listener (Feature 014): rustls with the `ring` provider, TLS 1.2 and 1.3, the
//! certificate chain and key read once from PEM files. HTTP/2 is offered through ALPN by the
//! engine next to HTTP/1.1.

use std::path::Path;
use std::sync::Arc;

use ferro_core::{Settings, SettingsError};
use rustls::ServerConfig;
use rustls_pki_types::pem::{self, PemObject};
use rustls_pki_types::{CertificateDer, PrivateKeyDer};

/// The server TLS configuration, or `None` when TLS is off. Errors name the setting and the
/// file, never key material.
pub fn server_config(settings: &Settings) -> Result<Option<ServerConfig>, SettingsError> {
    let (cert_path, key_path) = match (&settings.tls_cert, &settings.tls_key) {
        (None, None) => return Ok(None),
        (Some(cert), Some(key)) => (cert.as_str(), key.as_str()),
        (Some(_), None) => {
            return Err(SettingsError::new(
                "tls_key",
                "must be set together with tls_cert",
            ));
        }
        (None, Some(_)) => {
            return Err(SettingsError::new(
                "tls_cert",
                "must be set together with tls_key",
            ));
        }
    };
    let certificates = read_certificates(cert_path)?;
    let key = read_key(key_path)?;
    let provider = Arc::new(rustls::crypto::ring::default_provider());
    let config = ServerConfig::builder_with_provider(provider)
        .with_safe_default_protocol_versions()
        .map_err(|err| SettingsError::new("tls_cert", format!("TLS setup failed: {err}")))?
        .with_no_client_auth()
        .with_single_cert(certificates, key)
        .map_err(|err| match err {
            rustls::Error::InconsistentKeys(_) => SettingsError::new(
                "tls_key",
                format!("the key in '{key_path}' does not match the certificate in '{cert_path}'"),
            ),
            other => SettingsError::new(
                "tls_key",
                format!("the key in '{key_path}' cannot be used: {other}"),
            ),
        })?;
    Ok(Some(config))
}

fn unreadable(setting: &'static str, path: &str, err: &pem::Error) -> SettingsError {
    let reason = match err {
        pem::Error::Io(io) => io.to_string(),
        other => format!("not valid PEM ({other})"),
    };
    SettingsError::new(setting, format!("cannot read '{path}': {reason}"))
}

fn read_certificates(path: &str) -> Result<Vec<CertificateDer<'static>>, SettingsError> {
    let certificates = CertificateDer::pem_file_iter(Path::new(path))
        .map_err(|err| unreadable("tls_cert", path, &err))?
        .collect::<Result<Vec<_>, _>>()
        .map_err(|err| unreadable("tls_cert", path, &err))?;
    if certificates.is_empty() {
        return Err(SettingsError::new(
            "tls_cert",
            format!("'{path}' contains no PEM certificate (-----BEGIN CERTIFICATE-----)"),
        ));
    }
    Ok(certificates)
}

fn read_key(path: &str) -> Result<PrivateKeyDer<'static>, SettingsError> {
    PrivateKeyDer::from_pem_file(Path::new(path)).map_err(|err| match err {
        pem::Error::NoItemsFound => SettingsError::new(
            "tls_key",
            format!("'{path}' contains no private key (PKCS#8, PKCS#1 or SEC1 PEM)"),
        ),
        other => unreadable("tls_key", path, &other),
    })
}
