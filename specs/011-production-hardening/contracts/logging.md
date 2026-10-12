# Contract: Logging

## Text format (`log_format=text`, default)

Unchanged layout (timestamp, level, target, message, `key=value` fields). New fields:

- `ferro.python` records: `logger=<name>` and, during a request, `request_id=<id>`.
- Database-unavailable warnings on `ferro.handler`: `request_id=… handler=…` and the database's
  message.

## JSON format (`log_format=json`)

One JSON object per line on stderr, for every source (`ferro.access`, `ferro.server`,
`ferro.handler`, `ferro.python`):

```json
{"timestamp":"2026-10-11T08:00:00.123456Z","level":"INFO","target":"ferro.access","method":"GET","path":"/orders/7","status":200,"duration_ms":"1.84","request_id":"3f2a…","client":"127.0.0.1"}
{"timestamp":"2026-10-11T08:00:00.124001Z","level":"INFO","target":"ferro.python","message":"creating order","logger":"shop","request_id":"3f2a…"}
```

- Always present: `timestamp` (RFC 3339, UTC), `level`, `target`.
- `message` when the event has one; every other event field as a key (numbers stay numbers).
- No ANSI colours; one line per record even when the message contains newlines (escaped).
- The format is chosen by the first log initialization of the process.

## Shutdown summary (`ferro.server`, info)

```text
stopped: 12 requests in flight at stop, 12 finished, 0 cut off after shutdown_timeout
```

## Drain (Python logger `ferro.server`, info; target `ferro.python`)

```text
draining for 5.0 s: readiness reports not ready; stopping afterwards
```
