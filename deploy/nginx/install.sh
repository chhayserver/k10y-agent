#!/bin/sh
# Install the k10y agent next to Nginx on a Linux server (systemd). Run as root:
#   curl -fsSLO https://github.com/chhayserver/k10y-agent/releases/latest/download/install-nginx-agent.sh
#   less install-nginx-agent.sh && sudo sh install-nginx-agent.sh            [--version 2.9.0] [--port 9443]
# It installs the agent in /opt/k10y-nginx (its own Python environment), starts the service
# k10y-nginx-agent, and prints the token to add the Nginx in your k10y hub. Re-run to upgrade.
set -eu
REPO=chhayserver/k10y-agent
VERSION="" PORT=9443 SERVICE=1 LOCAL_WHEEL=""
while [ $# -gt 0 ]; do
  case "$1" in
    --version) VERSION="$2"; shift 2 ;;
    --port) PORT="$2"; shift 2 ;;
    --no-service) SERVICE=0; shift ;;          # install only (containers, tests)
    --wheel) LOCAL_WHEEL="$2"; shift 2 ;;       # a k10y_agent-*.whl file instead of the download
    *) echo "unknown option $1"; exit 2 ;;
  esac
done
[ "$(id -u)" = 0 ] || { echo "run as root (sudo)"; exit 1; }
command -v nginx >/dev/null || echo "note: nginx isn't on PATH; set K10Y_NGINX_BIN in /etc/k10y/nginx-agent.env"
PY=$(command -v python3 || true)
[ -n "$PY" ] || { echo "python3 is needed (apt install python3 python3-venv / dnf install python3)"; exit 1; }
"$PY" -c 'import sys, venv; sys.exit(sys.version_info < (3, 9))' 2>/dev/null || { echo "python3 >= 3.9 with venv is needed (apt install python3-venv)"; exit 1; }

if [ -n "$LOCAL_WHEEL" ]; then
  VERSION=$(basename "$LOCAL_WHEEL" | sed -n 's/^k10y_agent-\([0-9.]*\)-.*/\1/p')
elif [ -z "$VERSION" ]; then
  VERSION=$(curl -fsSI "https://github.com/$REPO/releases/latest" | sed -n 's#^[Ll]ocation: .*/tag/v\([0-9.]*\).*#\1#p' | tr -d '\r')
  [ -n "$VERSION" ] || { echo "can't find the latest release; pass --version X.Y.Z"; exit 1; }
fi
WHEEL="k10y_agent-$VERSION-py3-none-any.whl"
echo "Installing k10y agent $VERSION for Nginx…"
tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT
if [ -n "$LOCAL_WHEEL" ]; then cp "$LOCAL_WHEEL" "$tmp/$WHEEL"
else curl -fsSL -o "$tmp/$WHEEL" "https://github.com/$REPO/releases/download/v$VERSION/$WHEEL"; fi
[ -x /opt/k10y-nginx/bin/pip ] || "$PY" -m venv /opt/k10y-nginx
/opt/k10y-nginx/bin/pip install -q --upgrade pip >/dev/null
/opt/k10y-nginx/bin/pip install -q --upgrade "$tmp/$WHEEL[nginx]"

install -d -m 700 /etc/k10y /var/lib/k10y-nginx
[ -f /etc/k10y/nginx-agent.env ] || cat > /etc/k10y/nginx-agent.env <<ENV
# k10y agent for Nginx. After a change: systemctl restart k10y-nginx-agent
K10Y_MODE=nginx
K10Y_PORT=$PORT
K10Y_TLS=true
K10Y_TLS_DIR=/var/lib/k10y-nginx/tls
K10Y_TOKEN_FILE=/etc/k10y/nginx-token
K10Y_NGINX_CONF=/etc/nginx/nginx.conf
K10Y_NGINX_DATA_DIR=/var/lib/k10y-nginx
# K10Y_NGINX_RELOAD_CMD=systemctl reload nginx
# K10Y_NGINX_BIN=/usr/sbin/nginx
# K10Y_NGINX_STATUS_URL=http://127.0.0.1/nginx_status
ENV
chmod 600 /etc/k10y/nginx-agent.env

if [ "$SERVICE" = 1 ]; then
  cat > /etc/systemd/system/k10y-nginx-agent.service <<'UNIT'
[Unit]
Description=k10y agent for Nginx
After=network-online.target nginx.service
Wants=network-online.target

[Service]
EnvironmentFile=/etc/k10y/nginx-agent.env
ExecStart=/opt/k10y-nginx/bin/k10y-agent serve
Restart=on-failure
# Runs as root to read every config file and log and, later, to reload Nginx; the rest of the system is read-only.
NoNewPrivileges=true
PrivateTmp=true
ProtectHome=true
ProtectSystem=strict
ReadWritePaths=/var/lib/k10y-nginx /etc/k10y /etc/nginx /run /var/log/nginx
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true
RestrictSUIDSGID=true
LockPersonality=true

[Install]
WantedBy=multi-user.target
UNIT
  systemctl daemon-reload
  systemctl enable -q k10y-nginx-agent
  systemctl restart k10y-nginx-agent
  for _ in 1 2 3 4 5 6 7 8 9 10; do [ -s /etc/k10y/nginx-token ] && break; sleep 1; done
fi

echo
echo "Done. The agent listens on https://$(hostname -f 2>/dev/null || hostname):$PORT (self-signed certificate)."
[ -s /etc/k10y/nginx-token ] && echo "Token: $(cat /etc/k10y/nginx-token)"
echo "In the k10y hub: Administration -> Nginx instances -> Add Nginx, with this address and token."
echo "Allow the hub to reach port $PORT (firewall), and log in k10y's format for per-site numbers (see the docs)."
