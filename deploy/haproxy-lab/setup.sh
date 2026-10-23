#!/bin/sh
# Create config/ from the templates with a random Data Plane API password (once).
set -e
cd "$(dirname "$0")"
mkdir -p config
if [ ! -f config/dataplaneapi.password ]; then
  pw=$(openssl rand -hex 12)
  printf '%s\n' "$pw" > config/dataplaneapi.password
  chmod 600 config/dataplaneapi.password
  cp haproxy.cfg.template config/haproxy.cfg
  sed "s/__DPAPI_PASSWORD__/$pw/" dataplaneapi.yml.template > config/dataplaneapi.yml
  chmod 600 config/dataplaneapi.yml
  cp reload.sh config/reload.sh
  echo "created config/ (Data Plane API user: admin)"
fi
