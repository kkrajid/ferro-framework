# Contract: PostgreSQL database URLs

```text
postgresql://[user[:password]@]host[:port]/database[?param=value&...]
postgres://...                                         (same)
```

| Parameter | Values | Default | Meaning |
|---|---|---|---|
| `sslmode` | `disable`, `prefer`, `require`, `verify-full` | `prefer` | TLS use; certificates are always verified when TLS is used |
| `sslrootcert` | path to PEM file | — | extra trusted CA certificates |
| `pool_size` | 1–1000 | 10 | maximum connections (`db.connect(url, pool_size=N)` overrides) |
| `connect_timeout` | seconds | driver default | TCP/handshake timeout |
| `options` | e.g. `-csearch_path=tenant1` | — | session settings sent at connect |

Unknown parameters → `ConfigurationError("unsupported PostgreSQL URL parameter 'x'; supported: ...")`.
Connection failures → `OperationalError("could not connect to PostgreSQL at host:port/database as user: <reason>")`
— never containing the password.

Column types created by `create-tables`: see research R3/R4.
