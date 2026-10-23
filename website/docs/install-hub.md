# Install the hub

The hub runs with Docker Compose: the `hub` service, `postgres` (PostgreSQL 17) and `backup`.

## Requirements

- Docker Engine 24+ with the Compose plugin, on Linux, macOS or Windows.
- 1 CPU and 1 GB of memory are plenty for a small team.
- An open port for your users (8443 by default).
- Network access from the hub to each agent on port 30800.

## Install

```bash
mkdir k10y && cd k10y
curl -LO https://github.com/zorouzumaki1111-cmd/k10y-agent/releases/latest/download/docker-compose.yml
```

Create `.env` next to it:

```bash
cat > .env <<EOT
POSTGRES_PASSWORD=$(openssl rand -hex 24)
K10Y_SECRET_KEY=$(openssl rand -base64 48 | tr -d '\n')
K10Y_ADMIN_USER=admin
K10Y_ADMIN_PASSWORD=$(openssl rand -base64 12 | tr -d '/+=\n')
EOT
chmod 600 .env
docker compose up -d
```

Open `https://<server>:8443` and sign in with `K10Y_ADMIN_USER` / `K10Y_ADMIN_PASSWORD`. The first admin is created only when the database has no users, and must choose a new password at first sign-in. If you leave `K10Y_ADMIN_PASSWORD` empty, a random password is printed in `docker compose logs hub`.

## Where data is stored

Everything lives in folders next to `docker-compose.yml`, not in Docker volumes:

| Folder | Contents |
|---|---|
| `./data/postgres` | The database: users, roles, clusters, settings, audit log, alerts |
| `./data/hub` | The hub's TLS certificate (kept so the browser exception stays valid) |
| `./data/backups` | Automatic backups ([details](./backups)) |

To use another location, set `K10Y_DATA_DIR=/srv/k10y` in `.env`.

## HTTPS certificate

By default the hub creates a self-signed certificate on first start. To use your own (for example from your company CA), mount it and set:

```yaml
# docker-compose.yml, hub service
environment:
  K10Y_TLS_CERT: /certs/tls.crt
  K10Y_TLS_KEY: /certs/tls.key
volumes:
  - ./certs:/certs:ro
```

## Change the port

Set `K10Y_HUB_PORT=443` in `.env` and run `docker compose up -d`.

## Links in alert messages

Set `K10Y_PUBLIC_URL=https://k10y.example.com:8443` so Telegram messages link back to your hub.

## Useful commands

```bash
docker compose ps                 # status
docker compose logs -f hub        # hub logs
docker compose stop / start       # stop or start, keeping data
docker compose down               # remove containers; data stays in ./data
```
