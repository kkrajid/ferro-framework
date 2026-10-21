//! TLS connector (research R6): rustls with the `ring` provider; public roots plus `sslrootcert`.
//! Certificates are always verified when TLS is used.

use std::sync::Arc;

use ferro_database::DbError;
use rustls_pki_types::CertificateDer;
use rustls_pki_types::pem::PemObject;
use tokio_postgres_rustls::MakeRustlsConnect;

use crate::config::PgConfig;

pub(crate) fn connector(config: &PgConfig) -> Result<MakeRustlsConnect, DbError> {
    let fail = |message: String| DbError::Other { message };
    let mut roots = rustls::RootCertStore::empty();
    roots.extend(webpki_roots::TLS_SERVER_ROOTS.iter().cloned());
    if let Some(path) = &config.root_cert {
        let certificates = CertificateDer::pem_file_iter(path).map_err(|err| {
            fail(format!(
                "cannot read sslrootcert '{}': {err}",
                path.display()
            ))
        })?;
        for certificate in certificates {
            let certificate = certificate.map_err(|err| {
                fail(format!(
                    "invalid certificate in '{}': {err}",
                    path.display()
                ))
            })?;
            roots.add(certificate).map_err(|err| {
                fail(format!(
                    "invalid certificate in '{}': {err}",
                    path.display()
                ))
            })?;
        }
    }
    let provider = Arc::new(rustls::crypto::ring::default_provider());
    let tls = rustls::ClientConfig::builder_with_provider(provider)
        .with_safe_default_protocol_versions()
        .map_err(|err| fail(format!("TLS setup failed: {err}")))?
        .with_root_certificates(roots)
        .with_no_client_auth();
    Ok(MakeRustlsConnect::new(tls))
}

#[cfg(test)]
mod tests {
    /// RUSTSEC-2026-0381: eMudhra intermediate CA keys under "emSign ECC Root CA - G3" are
    /// compromised; webpki-roots 1.0.10 removed that root and it must stay out.
    #[test]
    fn the_compromised_emsign_root_is_not_trusted() {
        let compromised: &[u8] = b"emSign ECC Root CA - G3";
        let trusted = webpki_roots::TLS_SERVER_ROOTS.iter().any(|anchor| {
            anchor
                .subject
                .as_ref()
                .windows(compromised.len())
                .any(|window| window == compromised)
        });
        assert!(!trusted, "upgrade webpki-roots to 1.0.10 or later");
    }
}
