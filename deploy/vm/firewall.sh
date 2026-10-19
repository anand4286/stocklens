#!/usr/bin/env bash
# Host firewall for a single-VM deployment (Ubuntu/Debian, ufw).
# Default deny in; only SSH (rate-limited) and HTTPS/HTTP reach the host.
# Outbound is restricted to DNS, NTP and HTTPS — what the app and the OS need.
set -euo pipefail

SSH_FROM="${SSH_FROM:-}"   # e.g. 203.0.113.10/32 — your admin IP. Strongly recommended.

ufw --force reset
ufw default deny incoming
ufw default deny outgoing
ufw default deny routed

if [[ -n "$SSH_FROM" ]]; then
  ufw allow from "$SSH_FROM" to any port 22 proto tcp comment 'ssh (admin only)'
else
  ufw limit 22/tcp comment 'ssh (rate limited)'
fi
ufw allow 80/tcp comment 'http -> https redirect, ACME'
ufw allow 443/tcp comment 'https'

ufw allow out 53 comment 'dns'
ufw allow out 123/udp comment 'ntp'
ufw allow out 443/tcp comment 'https: market data, SEC, Anthropic, package mirrors'
ufw allow out 80/tcp comment 'apt mirrors (remove after provisioning)'

ufw logging medium
ufw --force enable
ufw status verbose

# The app port must never be public: it listens on 127.0.0.1 only.
if ss -ltn | grep -qE '0\.0\.0\.0:8080|\[::\]:8080'; then
  echo "WARNING: something listens on 8080 on all interfaces" >&2
  exit 1
fi
