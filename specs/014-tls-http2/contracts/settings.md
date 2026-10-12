# Contract: TLS settings and behaviour

| Setting | CLI | Environment | App | Default |
|---|---|---|---|---|
| `tls_cert` | `--tls-cert PATH` | `FERRO_TLS_CERT` | `App(tls_cert=...)` | none |
| `tls_key` | `--tls-key PATH` | `FERRO_TLS_KEY` | `App(tls_key=...)` | none |

- Both or neither. One alone → `ConfigurationError`: `setting 'tls_cert' (from cli) needs
  'tls_key' too: ...` (exit 2).
- `tls_cert`: PEM, one or more `CERTIFICATE` blocks, leaf first.
- `tls_key`: PEM private key (`PRIVATE KEY`, `RSA PRIVATE KEY`, `EC PRIVATE KEY`), unencrypted.
- File errors (exit 2, before listening), as ``invalid setting `<name>`: <message>``:
  - ``cannot read '<path>': <OS reason>`` / ``cannot read '<path>': not valid PEM (...)``
  - ``'<path>' contains no PEM certificate (-----BEGIN CERTIFICATE-----)``
  - ``'<path>' contains no private key (PKCS#8, PKCS#1 or SEC1 PEM)``
  - ``the key in '<key>' does not match the certificate in '<cert>'``
- With TLS: TLS 1.2/1.3, ALPN `h2`, `http/1.1`; handshake bounded by `header_timeout`; startup
  banner shows `https://`; origin checks use scheme `https`.
- HTTP/2 requests: `host` header given to handlers = request authority when absent.
- Native: `_ferro.check_tls(cert, key)` raises `ValueError` with the messages above.
