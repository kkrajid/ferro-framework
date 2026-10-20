# Deploying Ferro in production

Ferro is its own application server: `ferro run` serves HTTP (and HTTPS/HTTP/2 when given a
certificate) from its Rust engine. There is no Gunicorn, Uvicorn or Daphne in front of it; at
most a reverse proxy or load balancer. This guide goes from a checklist to complete setups for a
single server, Docker, Kubernetes and platforms as a service. Ready-to-edit files are in
[`deploy/`](../deploy/).

## 1. Checklist

| Item | How |
|---|---|
| Install | `pip install ferro-framework` (wheels for Linux, macOS, Windows; Python 3.12+) in a virtual environment or image |
| Database | PostgreSQL in production: `FERRO_DATABASE_URL=postgresql://user:pass@host:5432/db?sslmode=require` |
| Migrations | `ferro migrate` once per release, before new code serves (a separate step or job) |
| Admin account | `FERRO_SUPERUSER_PASSWORD=... ferro createsuperuser --username admin --no-input` |
| Processes | `--processes N` (≈ CPU cores for Python-heavy handlers); `--workers` I/O threads per process |
| Address | `--host 0.0.0.0` inside containers; `127.0.0.1` behind a local proxy |
| HTTPS | a proxy/load balancer terminating TLS, or `--tls-cert/--tls-key` directly |
| Proxies | `FERRO_TRUSTED_PROXIES=10.0.0.0/8` (the proxy's addresses) so client IPs and `https` are taken from `X-Forwarded-For` / `X-Forwarded-Proto` |
| Origins | `FERRO_TRUSTED_ORIGINS=https://www.example.com` if forms post from another origin |
| HSTS | `FERRO_HSTS=1` once HTTPS works everywhere |
| Probes | liveness `GET /healthz`, readiness `GET /readyz` |
| Zero-downtime stops | `--shutdown-drain 5` (readiness fails, traffic moves away, then a graceful stop) |
| Logs | stderr; `--log-format json` for log collectors; request ids in every line |
| Metrics | `FERRO_METRICS=1`, scrape `/metrics` from `FERRO_METRICS_ALLOW` addresses |
| Rate limits | `FERRO_RATE_LIMIT=100/minute` (shared by the processes of one machine) |
| Cache | `App(cache="database")` for a cache shared by every process and server |
| Real-time | WebSockets/event streams through the proxy (upgrade headers, no buffering: see the files in `deploy/`); `FERRO_BROADCAST=database` (PostgreSQL) when sockets live in several processes or servers |
| Debug | never `--debug` in production (it shows tracebacks) |

Every setting is a command-line option, an environment variable `FERRO_<NAME>` or an `App(...)`
argument (in that order of precedence); `.env` in the working directory is read too.

## 2. One server (VPS) with systemd

### Step by step: an Ubuntu server from zero

This walkthrough puts a Ferro project from a Git repository on a fresh Ubuntu 24.04 server
(any provider: Hetzner, DigitalOcean, AWS Lightsail/EC2, …) with PostgreSQL, HTTPS from Caddy
and systemd. 2 vCPUs and 2–4 GB of memory are plenty to start. Replace `example.com`,
`shop` and the repository URL with yours.

**1. DNS.** Point an `A` record (and `AAAA` for IPv6) of `example.com` to the server's address.

**2. Base system and firewall** (as root, or with `sudo`):

```bash
apt update && apt upgrade -y
apt install -y python3-venv git postgresql
ufw allow OpenSSH && ufw allow 80 && ufw allow 443 && ufw enable
```

Install Caddy from its official package repository
([instructions](https://caddyserver.com/docs/install#debian-ubuntu-raspbian)).

**3. Database:**

```bash
sudo -u postgres createuser --pwprompt shop        # choose a strong password
sudo -u postgres createdb --owner shop shop
```

**4. A system user and the code:**

```bash
useradd --system --no-create-home --home-dir /srv/shop --shell /usr/sbin/nologin ferro
mkdir /srv/shop && chown ferro:ferro /srv/shop
sudo -u ferro git clone https://github.com/you/shop.git /srv/shop
sudo -u ferro python3 -m venv /srv/shop/.venv
sudo -u ferro /srv/shop/.venv/bin/pip install ferro-framework   # plus your project's other dependencies
```

(Private repository: use a read-only deploy key.)

**5. Settings** — `/srv/shop/.env`, readable only by the service user:

```bash
FERRO_DATABASE_URL=postgresql://shop:THE_PASSWORD@localhost:5432/shop
FERRO_TRUSTED_PROXIES=127.0.0.1
FERRO_HSTS=1
FERRO_METRICS=1
```

```bash
chown ferro:ferro /srv/shop/.env && chmod 600 /srv/shop/.env
```

**6. Create the schema and an admin user** (run in the project directory so `.env` and `app.py`
are found):

```bash
cd /srv/shop
sudo -u ferro .venv/bin/ferro migrate
sudo -u ferro env FERRO_SUPERUSER_PASSWORD='a-long-secret' .venv/bin/ferro createsuperuser --username admin --no-input
```

**7. The service:** save [`deploy/ferro.service`](../deploy/ferro.service) from Ferro's
repository as `/etc/systemd/system/shop.service` — its paths already match this layout; set
`--processes` to the number of CPUs (see [scaling](guide/12-performance-scaling.md)) — and start
it:

```bash
systemctl daemon-reload
systemctl enable --now shop
systemctl status shop
curl -s http://127.0.0.1:8000/readyz        # {"status":"ready"}
```

**8. HTTPS with Caddy** — `/etc/caddy/Caddyfile`:

```text
example.com {
	encode zstd gzip
	reverse_proxy 127.0.0.1:8000 {
		flush_interval -1    # stream responses and server-sent events at once
	}
}
```

```bash
systemctl reload caddy
curl -s https://example.com/healthz        # {"status":"ok"} — the certificate is automatic
```

**9. Logs and monitoring:**

```bash
journalctl -u shop -f
```

Scrape `http://127.0.0.1:8000/metrics` from a local Prometheus agent, or check `/readyz` from
an uptime monitor.

**10. Backups** — a daily dump, kept for 14 days (copy it off the server too):

```bash
mkdir -p /var/backups/shop && chown postgres /var/backups/shop
echo '30 3 * * * postgres pg_dump -Fc shop > /var/backups/shop/shop-$(date +\%F).dump && find /var/backups/shop -mtime +14 -delete' > /etc/cron.d/shop-backup
```

Restore with `pg_restore -d shop shop-YYYY-MM-DD.dump`.

**11. Deploying a new version** — save as `/srv/shop/deploy.sh` and run it after every merge:

```bash
#!/bin/sh
set -e
cd /srv/shop
sudo -u ferro git pull --ff-only
sudo -u ferro .venv/bin/pip install --upgrade ferro-framework   # and your dependencies
sudo -u ferro .venv/bin/ferro migrate
systemctl restart shop                                    # drains, then finishes requests in flight
```

### What the unit file does

[`deploy/ferro.service`](../deploy/ferro.service) runs `ferro run --processes 4 --shutdown-drain 5`
as an unprivileged user with systemd's sandboxing, restarts on failure and gives the drain plus
`shutdown_timeout` time to finish on stop. Put HTTPS in front with Caddy
([`deploy/Caddyfile`](../deploy/Caddyfile): automatic Let's Encrypt certificates) or nginx
([`deploy/nginx.conf`](../deploy/nginx.conf)), and set `FERRO_TRUSTED_PROXIES=127.0.0.1`.

Without a proxy, Ferro can serve HTTPS itself:

```bash
ferro run --host 0.0.0.0 --port 443 --tls-cert /etc/letsencrypt/live/example.com/fullchain.pem \
          --tls-key /etc/letsencrypt/live/example.com/privkey.pem --processes 4
```

Binding port 443 needs `AmbientCapabilities=CAP_NET_BIND_SERVICE` in the unit. Certificates are
read at start: reload them with `systemctl restart shop` after renewal (a `deploy-hook` of
certbot). A proxy remains simpler for redirecting port 80, several sites on one machine or
static files.

**Deploying a new version:** install it, run `ferro migrate`, then `systemctl restart shop`. The
restart drains for `--shutdown-drain` seconds and lets requests in flight finish.

## 3. Docker and Docker Compose

[`deploy/Dockerfile`](../deploy/Dockerfile) builds a small image running as a non-root user with a
health check; [`deploy/docker-compose.yml`](../deploy/docker-compose.yml) adds PostgreSQL and
Caddy:

```bash
cp deploy/Dockerfile deploy/docker-compose.yml deploy/Caddyfile .
docker compose up -d db
docker compose run --rm web ferro migrate
docker compose up -d
docker compose logs -f web
```

Inside the container Ferro listens on `0.0.0.0:8000`; only Caddy publishes ports 80/443. Set
`FERRO_PROCESSES` to the CPUs you give the container. Stopping a container sends SIGTERM: the
drain and graceful stop happen within Docker's stop timeout (`stop_grace_period` in the compose
file is set above `shutdown_drain + shutdown_timeout`).

## 4. Kubernetes

[`deploy/kubernetes.yaml`](../deploy/kubernetes.yaml) has a migration `Job`, a `Deployment` and a
`Service`:

- `readinessProbe` → `/readyz` (fails while the database is unreachable or the pod drains),
  `livenessProbe` → `/healthz` (process alive; does not depend on the database, so a database
  outage does not restart pods).
- `--shutdown-drain 5` with `terminationGracePeriodSeconds` larger than drain + shutdown timeout:
  on termination the pod reports not-ready, endpoints are removed, then requests finish.
- Database URL from a `Secret`; `FERRO_TRUSTED_PROXIES` set to the ingress controller's pod
  range.
- One process per pod (`FERRO_PROCESSES=1`) and more replicas is the usual choice; the
  horizontal autoscaler then works on CPU.

Run the migration job for each release before rolling out the deployment (CI: `kubectl apply -f
job.yaml && kubectl wait --for=condition=complete job/shop-migrate`).

## 5. Platforms as a service

Any platform that runs a command and routes HTTP to a port works. Common settings:

| Platform | Start command | Notes |
|---|---|---|
| Render | `ferro run --host 0.0.0.0 --port $PORT --processes 2 --shutdown-drain 5` | pre-deploy command `ferro migrate`; health check path `/readyz` |
| Fly.io | same, port 8000 in `fly.toml` | `release_command = "ferro migrate"`; `[[http_service.checks]]` on `/readyz` |
| Railway | same with `$PORT` | add a PostgreSQL plugin; `FERRO_DATABASE_URL=${{Postgres.DATABASE_URL}}` |
| Google Cloud Run | the Docker image, `--port 8080` | concurrency up to `max_connections`; run `ferro migrate` as a Cloud Run job |
| Heroku-style | `web: ferro run --host 0.0.0.0 --port $PORT` (Procfile) | `release: ferro migrate` |

These platforms terminate TLS and add `X-Forwarded-*` headers: set `FERRO_TRUSTED_PROXIES` to
their documented ranges (or `0.0.0.0/0` only if nothing can reach the app except the platform's
router).

## 6. Operating

- **Scaling:** more processes per machine (`--processes`), more machines behind a load balancer.
  Rate limits are shared per machine; caches with `cache="database"` and sessions are shared by
  every machine through the database. Sizing, database connections and load testing:
  [performance and scaling](guide/12-performance-scaling.md).
- **Database pool:** each process opens up to `pool_size` connections (URL parameter
  `?pool_size=`); keep `processes × machines × pool_size` below the server's `max_connections`
  (or use PgBouncer 1.21+ with `max_prepared_statements` in transaction mode).
- **WebSockets and event streams:** connections stay open, so proxies and load balancers need
  idle timeouts above 60 s (Ferro pings sockets every 20 s and event streams every 15 s). On stop,
  sockets close with code 1001 and streams are cut off; clients reconnect to another instance. The
  `database` broadcast backend keeps one extra connection per process for `LISTEN`; it must reach
  PostgreSQL directly or through PgBouncer in *session* mode (transaction mode drops
  notifications). See [real-time](guide/16-realtime.md).
- **Backups:** PostgreSQL `pg_dump`/managed snapshots; for SQLite stop the app or use
  `sqlite3 db.sqlite3 ".backup backup.sqlite3"`.
- **Monitoring:** alert on `/readyz` failures, the 5xx share of `ferro_http_requests_total`,
  `ferro_db_queries_total{outcome="unavailable"}` and latency histograms.
- **Upgrades:** read the [changelog](../CHANGELOG.md) and the [stability policy](stability.md);
  run `ferro makemigrations --check` in CI so model changes always come with migrations.
