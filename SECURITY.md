# Security policy

## Reporting a vulnerability

Please report security issues privately through GitHub's
**"Report a vulnerability"** (Security → Advisories) on this repository rather
than opening a public issue. Include steps to reproduce and the impact you
believe it has.

You can expect an acknowledgement within 3 working days and a fix or
mitigation plan within 14 days for confirmed issues.

## Supported versions

The latest release on `main` receives security fixes.

## Scope

In scope: the application code, the container image, and the deployment
templates in `deploy/`. Out of scope: third-party services StockLens calls, and
findings that require an already-compromised host.

The design and threat model are documented in
[docs/SECURITY_ARCHITECTURE.md](docs/SECURITY_ARCHITECTURE.md).
