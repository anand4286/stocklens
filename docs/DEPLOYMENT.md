# Deployment

Every target gives the same guarantees: TLS terminated at an edge you control,
an edge rate limit, the app reachable only through that edge, non-root and
read-only at runtime, secrets from a secret store, and probes wired to
`/healthz` and `/readyz`.

## Configuration

All settings are environment variables prefixed `STOCKLENS_` — see
[`.env.example`](../.env.example). With none set, StockLens runs in demo mode with
an SQLite file and the rules-based analyst.

| Variable | Production guidance |
|---|---|
| `STOCKLENS_DATABASE_URL` | PostgreSQL: `postgresql+psycopg://user:pass@host:5432/stocklens` |
| `STOCKLENS_API_KEY` | Set for private deployments; leave unset for a public read-only showcase |
| `STOCKLENS_TRUST_PROXY_HEADERS` | `true` only behind a proxy that overwrites `X-Forwarded-For` |
| `STOCKLENS_AGENT_PUBLIC` | `false` on public sites unless you accept the spend |
| `STOCKLENS_AGENT_DAILY_LIMIT` | Your cost ceiling in model runs per UTC day |
| `FORWARDED_ALLOW_IPS` | Proxy IPs uvicorn trusts for `--proxy-headers` (container only) |

## 1. Local

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install --require-hashes -r requirements-dev.lock -r requirements-build.lock
pip install --no-deps --no-build-isolation -e .
uvicorn --factory stocklens.app:app_factory --reload --port 8080
```

## 2. Docker

```bash
docker build -t stocklens .
docker run --rm -p 8080:8080 --read-only --tmpfs /tmp --tmpfs /app/var \
  --cap-drop ALL --security-opt no-new-privileges stocklens
```

## 3. Docker Compose (app + PostgreSQL + Caddy)

```bash
cp .env.example .env
echo "POSTGRES_PASSWORD=$(openssl rand -hex 24)" >> .env
SITE_ADDRESS=analytics.example.com docker compose up -d --build
```

Caddy obtains certificates automatically for a public DNS name (use
`localhost` for a self-signed local cert). Only Caddy publishes ports; the
database sits on an `internal` network with no route out.

## 4. Single VM (Ubuntu 24.04)

```bash
sudo DOMAIN=analytics.example.com EMAIL=you@example.com SSH_FROM=203.0.113.10/32 \
  bash deploy/vm/install.sh
```

Installs the app in a venv under a system user, the hardened systemd unit,
nginx with TLS from Let's Encrypt and per-route `limit_req`, unattended
security upgrades, fail2ban, and the ufw policy (default deny both
directions). Edit `/etc/stocklens/stocklens.env` (mode 0600) and
`systemctl restart stocklens`. Check the sandbox with
`systemd-analyze security stocklens`.

## 5. Kubernetes

```bash
kubectl apply -f deploy/k8s/namespace.yaml
kubectl -n stocklens create secret generic stocklens-secrets \
  --from-literal=STOCKLENS_DATABASE_URL='postgresql+psycopg://…' \
  --from-literal=STOCKLENS_API_KEY="$(openssl rand -hex 32)" \
  --from-literal=STOCKLENS_ANTHROPIC_API_KEY='…'
kubectl apply -k deploy/k8s
```

Assumes ingress-nginx and cert-manager with a `letsencrypt` ClusterIssuer.
Includes: PSA `restricted`, non-root read-only pods with no service-account
token, default-deny NetworkPolicy, HPA (2–6 on CPU), PDB, topology spread,
startup/readiness/liveness probes. Use a managed PostgreSQL or an operator
(CloudNativePG); with more than one replica a shared database is required for
the agent budget and cache to be global.

## 6. Google Cloud Run

```bash
export PROJECT_ID=your-project REGION=us-central1
bash deploy/cloudrun/deploy.sh
```

Builds with Cloud Build, pushes to Artifact Registry, deploys with a dedicated
service account and secrets from Secret Manager. For production add Cloud SQL
and put an HTTPS load balancer with Cloud Armor in front
(`deploy/cloudrun/cloud-armor.sh`), then set ingress to
`internal-and-cloud-load-balancing`.

## 7. AWS

App Runner for simplicity, or ECS Fargate behind an ALB for control — see
[`deploy/aws/README.md`](../deploy/aws/README.md) for commands, the task
definition (read-only, non-root, capabilities dropped) and the security-group
matrix. Attach AWS WAF with managed rule groups and a rate-based rule.

## 8. Azure

Container Apps with Key Vault references and Front Door WAF — see
[`deploy/azure/README.md`](../deploy/azure/README.md).

## Post-deploy checks

```bash
URL=https://analytics.example.com
curl -fsS $URL/healthz && curl -fsS $URL/readyz
curl -sI $URL/ | grep -iE 'content-security-policy|strict-transport|x-frame-options'
curl -s $URL/api/v1/meta | jq '{version, sources, agent}'
for i in $(seq 1 80); do curl -s -o /dev/null -w '%{http_code}\n' $URL/api/v1/meta; done | sort | uniq -c   # expect some 429
```

## Upgrades and rollback

The schema is created on start-up (`create_all`) and both tables are
append-only, so rolling deploys are safe. Roll back by redeploying the previous
image tag or digest. For schema changes beyond additive columns, introduce
Alembic migrations run as a pre-deploy job.
