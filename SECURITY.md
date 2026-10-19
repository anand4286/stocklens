# Security policy

StockLens is designed to be safe to run on the public internet with no
credentials in the repository, no account data, and no code path that can move
money. This document says how to report a problem, what is protected, and what
a deployer must do.

## Reporting a vulnerability

Report privately through GitHub's **Report a vulnerability** button
(Security → Advisories → *New draft advisory*) on
[anand4286/stocklens](https://github.com/anand4286/stocklens/security/advisories/new).
Please do not open a public issue for a security problem.

Include the affected version or commit, steps to reproduce, and the impact you
believe it has. You can expect:

| Step | Target |
|---|---|
| Acknowledgement | within 3 working days |
| Triage and severity (CVSS 3.1) | within 7 days |
| Fix or mitigation for confirmed High/Critical | within 14 days |
| Coordinated disclosure and credit | when a fix is released, unless you prefer otherwise |

Good-faith research that respects this policy, avoids privacy violations and
service degradation, and does not touch other people's deployments is welcome.

## Supported versions

The latest commit on `main` receives security fixes.

## What is protected, and how

| Asset | Control |
|---|---|
| **Secrets** (API keys for Alpaca, Anthropic; the optional StockLens API key; database URL) | Read only from the environment or a secret manager; typed as `SecretStr` so they never appear in `repr`, logs or errors; `.env` is git-ignored; gitleaks runs in pre-commit and on every push (full history) |
| **Upstream accounts** | Market-data scope only; no trading endpoint exists in the code. Tickers are validated against an allowlist pattern before they can reach any upstream URL; upstream error bodies are never echoed |
| **Model spend** | Agent endpoint off for anonymous callers by default; per-client rate limit; daily budget counted in the database; per-symbol result cache; step ceiling |
| **Compute** | Per-client token bucket on every API route, plus a tighter one on history-rescoring endpoints (evidence, portfolio, forecasts, track record) |
| **Storage** | Ledgers are append-only, resolved write-once, and capped by `STOCKLENS_LEDGER_DAILY_LIMIT` so GET traffic cannot grow the database without bound |
| **Browser** | Strict CSP (`script-src 'self'`, no inline script or style, `connect-src 'self'`), HSTS, frame-deny, nosniff, Permissions-Policy; every DOM node built with `textContent`; third-party links restricted to http(s) |
| **AI agent** | Read-only tools bound server-side to one symbol; no tool accepts a ticker, URL or query; headlines delivered as labelled untrusted data; every cited figure verified |
| **Runtime** | Multi-stage build; non-root UID 10001 with no login shell; read-only root filesystem; all Linux capabilities dropped; no-new-privileges |
| **Network** | Default-deny Kubernetes NetworkPolicy; host firewall deny-in-and-out templates; egress only to DNS, the database and the named HTTPS providers |
| **Supply chain** | Every GitHub Action pinned to a full commit SHA; Dependabot for pip, Docker and Actions; pip-audit and a CycloneDX SBOM in CI; Trivy image scan; CodeQL (security-extended); OpenSSF Scorecard |

Threat model (STRIDE), trust boundaries and firewall design:
[docs/SECURITY_ARCHITECTURE.md](docs/SECURITY_ARCHITECTURE.md).

## Deployer checklist

Before exposing an instance publicly:

- [ ] Secrets come from a secret manager or the platform's encrypted
      environment — never a committed file, image layer or build argument.
- [ ] `STOCKLENS_API_KEY` is set if the API should not be public, and
      `STOCKLENS_AGENT_PUBLIC=false` unless you intend to pay for anonymous runs.
- [ ] TLS terminates at an edge you control; `STOCKLENS_TRUST_PROXY_HEADERS=true`
      **only** behind that proxy.
- [ ] `STOCKLENS_CORS_ORIGINS` lists exact origins (never `*`).
- [ ] PostgreSQL is on a private network with a strong, rotated password.
- [ ] Rate limits and `STOCKLENS_LEDGER_DAILY_LIMIT` are sized for your traffic.
- [ ] Container runs with the provided read-only, non-root, no-capability flags.
- [ ] Logs are shipped somewhere you can query `request_id`; they contain no
      query strings, headers or secrets by design.

## Repository settings (maintainers)

- Branch protection on `main`: pull request required, CI and CodeQL required,
  force-push and deletion disabled.
- Secret scanning and push protection enabled; private vulnerability reporting
  enabled; Dependabot alerts and security updates enabled.

## Scope

In scope: the application code, the container image, the deployment templates
in `deploy/`, and the generated API console in `site/`. Out of scope:
third-party services StockLens calls, and findings that require an
already-compromised host or a deployer ignoring the checklist above.
