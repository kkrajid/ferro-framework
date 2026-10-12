# Production image for a Ferro project (copy to the project root, next to app.py).
#   docker build -t shop .
#   docker run -p 8000:8000 -e FERRO_DATABASE_URL=postgresql://... shop
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    FERRO_HOST=0.0.0.0 \
    FERRO_PORT=8000 \
    FERRO_PROCESSES=2 \
    FERRO_SHUTDOWN_DRAIN=5 \
    FERRO_LOG_FORMAT=json

WORKDIR /app

# Dependencies first (`dependencies` of pyproject.toml), so code changes do not reinstall them.
COPY pyproject.toml ./
RUN python -c "import tomllib; print('\n'.join(tomllib.load(open('pyproject.toml', 'rb'))['project']['dependencies']))" > /tmp/requirements.txt && \
    pip install -r /tmp/requirements.txt && \
    useradd --system --uid 10001 --home /app ferro

COPY --chown=ferro:ferro . .

USER ferro
EXPOSE 8000

HEALTHCHECK --interval=10s --timeout=3s --start-period=10s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=2)"

# SIGTERM (docker stop) drains for FERRO_SHUTDOWN_DRAIN seconds, then stops gracefully.
CMD ["ferro", "run"]
