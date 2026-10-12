# Feature Specification: TLS and HTTP/2

**Feature Branch**: `014-tls-http2`

**Created**: 2026-10-11

**Status**: Draft

**Input**: User description: "TLS/HTTP2 illa (reverse proxy venam) — complete it": serve HTTPS
and HTTP/2 directly from Ferro's engine, without requiring a reverse proxy.

## Clarifications

### Session 2026-10-11

The user asked to complete these items without interruptions; recommended options were taken.

- Q: Where do certificates come from? → A: PEM files named by two settings, `tls_cert` (the
  certificate chain, leaf first) and `tls_key` (the private key: PKCS#8, PKCS#1 or SEC1). Both or
  neither; automatic certificates (ACME) are out of scope (use a proxy or a certificate tool).
- Q: Which protocols? → A: TLS 1.2 and 1.3 only, with the TLS library's safe defaults. HTTP/2 is
  offered through ALPN next to HTTP/1.1 whenever TLS is on; the client chooses. Cleartext HTTP/2
  (h2c) is not offered.
- Q: Does a running server pick up renewed certificates? → A: No; certificates are read at start.
  Restart (or roll the worker processes) after renewal — documented. Hot reload is out of scope.
- Q: What does the framework treat as the request scheme? → A: `https` for requests on a TLS
  listener (affects origin checks), regardless of forwarded headers.
- Q: How are startup problems reported? → A: Missing/unreadable files, invalid PEM, a key that does
  not match the certificate, or only one of the two settings → a configuration error before the
  server accepts connections, naming the setting and file, never printing key material.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Serve HTTPS directly (Priority: P1)

An operator runs a Ferro application on a server without a reverse proxy and wants browsers and
API clients to connect over HTTPS.

**Why this priority**: Without TLS, a proxy is mandatory for any internet-facing deployment.

**Independent Test**: Start `ferro run --tls-cert cert.pem --tls-key key.pem`, call it with an
HTTPS client trusting the test certificate authority, get the handler's response.

**Acceptance Scenarios**:

1. **Given** valid certificate and key files, **When** the server starts, **Then** it accepts
   HTTPS on the configured port and the startup message shows an `https://` address.
2. **Given** a running TLS server, **When** a client sends plain HTTP to that port, **Then** the
   connection fails for that client only and the server keeps serving HTTPS clients.
3. **Given** missing files, invalid PEM, a key that does not match, or only one setting, **Then**
   the server refuses to start with a message naming the setting and the path.
4. **Given** TLS, **Then** probes, metrics, rate limits, request ids, timeouts, graceful shutdown
   and multiple processes (`--processes N`) work as without TLS.
5. **Given** TLS and a same-origin form POST from a browser (`Origin: https://host:port`),
   **Then** the CSRF origin check allows it.

---

### User Story 2 - HTTP/2 (Priority: P2)

Clients that support HTTP/2 (browsers, gRPC-free API clients, load testers) use one multiplexed
connection.

**Why this priority**: Fewer connections and header compression for browsers and API clients;
expected of a modern server.

**Independent Test**: An HTTP/2-capable client connects with ALPN, negotiates `h2`, sends several
concurrent requests on one connection and gets correct responses.

**Acceptance Scenarios**:

1. **Given** TLS, **When** a client offers `h2`, **Then** HTTP/2 is negotiated; a client offering
   only `http/1.1` gets HTTP/1.1.
2. **Given** HTTP/2, **Then** path/query parameters, headers, cookies, JSON and form bodies,
   streaming bodies, error envelopes, 404/405, body size limit (413), and the request timeout
   behave as over HTTP/1.1.
3. **Given** HTTP/2 requests (which carry no `Host` header), **Then** the origin check uses the
   request's authority and Python sees a `host` header.
4. **Given** 50 concurrent requests on one HTTP/2 connection, **Then** all succeed.

### Edge Cases

- Certificate files that change while the server runs are not re-read (documented).
- A TLS handshake that never completes is dropped after the handshake timeout and does not hold a
  worker.
- `ferro dev` accepts the same settings (local HTTPS testing).

## Requirements *(mandatory)*

- **FR-001**: Settings `tls_cert` and `tls_key` (paths) through the usual precedence (CLI
  `--tls-cert/--tls-key`, environment `FERRO_TLS_CERT/FERRO_TLS_KEY`, `App(...)`); both or none.
- **FR-002**: With TLS, the listener accepts TLS 1.2/1.3 only and advertises `h2` and `http/1.1`
  via ALPN.
- **FR-003**: Startup validation per the clarification; errors never include key contents.
- **FR-004**: Requests on a TLS listener have scheme `https` for origin checks; HTTP/2 requests
  get their host from the authority when there is no `Host` header (also exposed to Python as
  `host`).
- **FR-005**: Every existing engine behaviour (limits, timeouts, probes, metrics, shutdown,
  multi-process) works over TLS and HTTP/2.
- **FR-006**: TLS handshakes are bounded in time.
- **FR-007**: Documentation covers HTTPS without a proxy, certificate renewal (restart) and when a
  proxy is still preferable.

## Success Criteria *(mandatory)*

- **SC-001**: An HTTPS client completes requests over HTTP/1.1 and HTTP/2 against `ferro run` with
  TLS, on Windows and Linux.
- **SC-002**: 50 concurrent requests multiplexed on one HTTP/2 connection all succeed.
- **SC-003**: Every misconfiguration in scenario 1.3 is reported before the port accepts
  connections, with the setting name in the message.

## Assumptions

- Certificate files are readable by the server process; private keys are not encrypted.
- ACME/Let's Encrypt automation, mutual TLS (client certificates), OCSP stapling, hot reload and
  h2c are out of scope.
