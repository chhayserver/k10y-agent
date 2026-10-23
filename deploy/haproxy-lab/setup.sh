#!/bin/sh
# Create config/ and config2/ from the templates, each with a random Data Plane API password (once).
set -e
cd "$(dirname "$0")"
make_config() {   # $1 = directory, $2 = haproxy host name for the reload script, $3 = log tag
  dir=$1
  [ -f "$dir/dataplaneapi.password" ] && return
  mkdir -p "$dir/certs" "$dir/general"
  pw=$(openssl rand -hex 12)
  printf '%s\n' "$pw" > "$dir/dataplaneapi.password"
  chmod 600 "$dir/dataplaneapi.password"
  cp haproxy.cfg.template "$dir/haproxy.cfg"
  sed "s/__DPAPI_PASSWORD__/$pw/" dataplaneapi.yml.template > "$dir/dataplaneapi.yml"
  chmod 600 "$dir/dataplaneapi.yml"
  sed "s/nc -w 3 haproxy 9999/nc -w 3 $2 9999/; s/nc -w 5 haproxy 9999/nc -w 5 $2 9999/" reload.sh > "$dir/reload.sh"
  chmod +x "$dir/reload.sh"
  echo "created $dir/ (Data Plane API user: admin)"
}
make_config config haproxy
make_config config2 haproxy2
