# Contract: Metrics

Off by default. With `metrics=true`, `GET {metrics_path}` (default `/metrics`) answers clients
whose address (after trusted-proxy resolution) matches `metrics_allow` (default `127.0.0.1`,
`::1`; IP addresses or CIDR ranges). Any other client gets the 404 error envelope, exactly as
if metrics were off. Content type: `text/plain; version=0.0.4; charset=utf-8`. Not counted in
the metrics themselves; logged on `ferro.access` at debug level only.

## Series

```text
# TYPE ferro_http_requests_total counter
ferro_http_requests_total{route="/items/{id}",method="GET",status="2xx"} 42
ferro_http_requests_total{route="<unmatched>",method="GET",status="4xx"} 3

# TYPE ferro_http_request_duration_seconds histogram
ferro_http_request_duration_seconds_bucket{route="/items/{id}",method="GET",le="0.001"} 10
...
ferro_http_request_duration_seconds_bucket{route="/items/{id}",method="GET",le="+Inf"} 42
ferro_http_request_duration_seconds_sum{route="/items/{id}",method="GET"} 0.0815
ferro_http_request_duration_seconds_count{route="/items/{id}",method="GET"} 42

# TYPE ferro_http_requests_in_flight gauge
ferro_http_requests_in_flight 1

# TYPE ferro_db_queries_total counter
ferro_db_queries_total{outcome="ok"} 40
ferro_db_queries_total{outcome="error"} 1
ferro_db_queries_total{outcome="unavailable"} 0

# TYPE ferro_db_query_duration_seconds histogram
ferro_db_query_duration_seconds_bucket{le="0.001"} ...

# TYPE ferro_process_start_time_seconds gauge
ferro_process_start_time_seconds 1791690000.123

# TYPE ferro_build_info gauge
ferro_build_info{version="0.1.0"} 1
```

- Buckets (seconds): 0.001, 0.0025, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, +Inf.
- `route`: the route's path template as registered, or `<unmatched>`; never the raw request path.
- `method`: GET, HEAD, POST, PUT, PATCH, DELETE, OPTIONS, or `other`.
- `status`: `1xx` … `5xx`.
- Series with zero counts may be omitted; label values are escaped (`\\`, `\"`, `\n`).
- Database duration is measured from submitting a query to its completion (queue wait included).
- Per process: with `--processes N`, a scrape reaches whichever worker accepts it.
