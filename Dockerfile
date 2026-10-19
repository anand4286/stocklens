# syntax=docker/dockerfile:1.7
# Multi-stage build: compilers and pip stay in the builder; the runtime image
# holds the virtualenv and the app, runs as an unprivileged user, and works
# with a read-only root filesystem (writes go to /app/var, a mounted volume).

FROM python:3.14-slim AS builder
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /build
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
COPY pyproject.toml README.md LICENSE NOTICE ./
COPY src ./src
RUN pip install ".[postgres]" \
 # The runtime never installs anything, so pip (and the copies of urllib3,
 # msgpack and setuptools it vendors) is attack surface with no use. Remove it
 # from the virtualenv that is copied into the runtime image.
 && (python -m pip uninstall --yes pip setuptools wheel || true)

FROM python:3.14-slim AS runtime
LABEL org.opencontainers.image.title="stocklens" \
      org.opencontainers.image.description="Explainable stock analytics with a guarded AI analyst agent" \
      org.opencontainers.image.licenses="Apache-2.0"
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    STOCKLENS_DATABASE_URL=sqlite:////app/var/stocklens.db
# Same for the base image's own pip and ensurepip wheels.
RUN (python -m pip uninstall --yes pip setuptools wheel || true) \
 && rm -rf /usr/local/lib/python3*/ensurepip /usr/local/bin/pip* \
 && groupadd --system --gid 10001 stocklens \
 && useradd --system --uid 10001 --gid stocklens --home-dir /app --shell /usr/sbin/nologin stocklens \
 && mkdir -p /app/var && chown stocklens:stocklens /app/var
COPY --from=builder /opt/venv /opt/venv
WORKDIR /app
USER 10001:10001
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
  CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=2).status == 200 else 1)"]
# --proxy-headers is safe only because the container is never exposed directly;
# every deploy template puts a proxy or managed load balancer in front.
# WEB_CONCURRENCY: worker processes. Keep 1 on Kubernetes (scale pods instead,
# one pod per CPU); set to the vCPU count on a single VM. Memory per worker is
# ~80 MB + 0.75 MB x STOCKLENS_CACHE_MAX_SYMBOLS.
CMD ["sh", "-c", "exec uvicorn --factory stocklens.app:app_factory --host 0.0.0.0 --port ${PORT:-8080} --workers ${WEB_CONCURRENCY:-1} --proxy-headers --forwarded-allow-ips=${FORWARDED_ALLOW_IPS:-127.0.0.1} --no-server-header"]
