# Research: TLS and HTTP/2

## R1 — TLS library and crypto provider

**Decision**: rustls 0.23 with the `ring` provider, through the engine's `rustls-0_23` feature;
the provider is passed explicitly (`ServerConfig::builder_with_provider`).

**Rationale**: rustls and ring are already in the dependency tree (PostgreSQL TLS, Feature 003),
so no C toolchain (aws-lc, OpenSSL) is needed on any platform. The engine's rustls integration
pulls `tokio-rustls` without default features, so aws-lc is not built. Passing the provider
explicitly avoids rustls's process-default provider panicking if another crate ever enables a
second provider.

**Alternatives**: OpenSSL (system library on Linux, painful on Windows); aws-lc-rs (needs CMake
and NASM on Windows).

## R2 — Protocol versions and ALPN

**Decision**: `with_safe_default_protocol_versions()` (TLS 1.2 and 1.3). The engine prepends ALPN
`h2` and `http/1.1`; the client chooses. No setting to turn HTTP/2 off.

**Rationale**: HTTP/2 over TLS is negotiated, so offering it costs nothing to HTTP/1.1 clients.
h2c (cleartext HTTP/2) is not offered: browsers never use it and proxies speak HTTP/1.1 to
backends.

## R3 — Where certificates are read and errors reported

**Decision**: `ferro_http::tls::server_config(settings)` reads the PEM files and builds the
config; errors are `SettingsError` naming `tls_cert`/`tls_key` and the path. It runs in the
native `Server` constructor (so `ferro run` fails before binding, exit 2 via
`ConfigurationError`), in `start_on` (the value actually used), and from the multi-process
supervisor through `_ferro.check_tls` before the shared socket is bound. Key-mismatch detection
comes from rustls (`InconsistentKeys`). Messages contain paths and reasons only, never PEM text
(tested).

## R4 — Scheme and host

**Decision**: `State.tls` makes the CSRF origin check use `https` directly (forwarded headers are
for proxies, which TLS-terminating Ferro does not need). HTTP/2 requests carry `:authority`
instead of `Host`; the origin check and the headers given to Python use the URI authority when
`Host` is absent.

**Rationale**: Without this, same-origin POSTs over HTTP/2 would be refused (no host to compare
with) and handlers reading `request.headers["host"]` would break on HTTP/2.

## R5 — Handshake bound

**Decision**: `tls_handshake_timeout = header_timeout` (default 5 s).

**Rationale**: A client that opens a connection and never completes the handshake must not hold a
connection slot longer than one that never sends its headers.

## R6 — Testing

**Decision**: Rust tests generate certificates with `rcgen` (dev-dependency, ring) and talk TLS
with a blocking rustls client (HTTP/1.1, ALPN check, CSRF scheme, misconfiguration). Python
integration tests use `trustme` (throwaway CA) and `httpx` with `h2` to exercise real HTTP/2:
host from authority, JSON bodies, 413, 404, probes, 50 multiplexed requests, and both
`--processes 1` and `2`. `trustme` and `h2` join the dev dependencies (CI installs them).
