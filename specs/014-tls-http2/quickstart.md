# Quickstart: TLS and HTTP/2

```bash
cargo test -p ferro-http --test tls
.venv/Scripts/python -m pytest tests/integration/test_tls.py -q
```

Local HTTPS by hand (a self-signed certificate; browsers will warn):

```bash
openssl req -x509 -newkey rsa:2048 -nodes -days 30 -subj "/CN=localhost" -addext "subjectAltName=DNS:localhost" -keyout key.pem -out cert.pem
ferro run --tls-cert cert.pem --tls-key key.pem
curl --http2 -k https://localhost:8000/healthz -v
```

Expected: the banner shows `https://127.0.0.1:8000`; curl reports `ALPN: server accepted h2`
and `{"status":"ok"}`.
