#!/usr/bin/env bash
# Provision StockLens on a fresh Ubuntu 24.04 VM: user, venv, systemd, nginx, TLS, firewall.
#   sudo REPO=https://github.com/<owner>/stocklens.git DOMAIN=example.com EMAIL=you@example.com SSH_FROM=203.0.113.10/32 ./install.sh
set -euo pipefail
: "${DOMAIN:?set DOMAIN}"; : "${EMAIL:?set EMAIL}"
: "${REPO:?set REPO to the git URL of your copy}"

apt-get update
apt-get install -y python3.12-venv git nginx certbot python3-certbot-nginx ufw fail2ban unattended-upgrades
dpkg-reconfigure -f noninteractive unattended-upgrades

id stocklens >/dev/null 2>&1 || useradd --system --home /opt/stocklens --shell /usr/sbin/nologin stocklens
git clone --depth 1 "$REPO" /opt/stocklens 2>/dev/null || git -C /opt/stocklens pull --ff-only
python3 -m venv /opt/stocklens/.venv
# Hash-locked: pip refuses any package whose SHA-256 is not in the lockfile.
/opt/stocklens/.venv/bin/pip install --require-hashes \
  -r /opt/stocklens/requirements.lock -r /opt/stocklens/requirements-build.lock
/opt/stocklens/.venv/bin/pip install --no-deps --no-build-isolation /opt/stocklens
mkdir -p /opt/stocklens/var /etc/stocklens
chown -R stocklens:stocklens /opt/stocklens/var

if [[ ! -f /etc/stocklens/stocklens.env ]]; then
  install -m 0600 -o root -g root /opt/stocklens/.env.example /etc/stocklens/stocklens.env
  sed -i "s|^STOCKLENS_DATABASE_URL=.*|STOCKLENS_DATABASE_URL=sqlite:////opt/stocklens/var/stocklens.db|" /etc/stocklens/stocklens.env
  echo "STOCKLENS_TRUST_PROXY_HEADERS=true" >> /etc/stocklens/stocklens.env
fi

install -m 0644 /opt/stocklens/deploy/systemd/stocklens.service /etc/systemd/system/stocklens.service
systemctl daemon-reload
systemctl enable --now stocklens

sed "s/example.com/${DOMAIN}/g" /opt/stocklens/deploy/nginx/stocklens.conf > /etc/nginx/sites-available/stocklens.conf
ln -sf /etc/nginx/sites-available/stocklens.conf /etc/nginx/sites-enabled/stocklens.conf
rm -f /etc/nginx/sites-enabled/default
certbot certonly --nginx -d "$DOMAIN" -m "$EMAIL" --agree-tos --non-interactive
nginx -t && systemctl reload nginx

SSH_FROM="${SSH_FROM:-}" bash /opt/stocklens/deploy/vm/firewall.sh
echo "StockLens is live at https://${DOMAIN}"
