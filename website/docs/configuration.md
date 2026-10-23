# Configuration reference

All settings are environment variables starting with `K10Y_`.

## Hub

Set these in `.env`, or in the `hub` service of `docker-compose.yml`.

| Variable | Default | Purpose |
|---|---|---|
| `K10Y_MODE` | `agent` | `hub` for the hub (set in the compose file) |
| `K10Y_DATABASE_URL` | — | `postgresql://user:password@host:5432/db` (set in the compose file from `POSTGRES_PASSWORD`) |
| `K10Y_SECRET_KEY` | — | Encrypts stored tokens and keys. **Keep it stable and backed up.** |
| `K10Y_ADMIN_USER` | `admin` | First admin's username, used only when there are no users |
| `K10Y_ADMIN_PASSWORD` | random | First admin's password. If empty, a random one is printed in the hub log. |
| `K10Y_SESSION_HOURS` | `12` | Idle time before a session expires |
| `K10Y_PUBLIC_URL` | — | The hub's address, for links in alert messages |
| `K10Y_ALERTS_ENABLED` | `true` | Run the alert engine |
| `K10Y_SYSLOG_PORT` | `5514` in compose (`0` = off) | UDP port where the hub receives HAProxy logs |
| `K10Y_TLS` | `true` in compose | Serve HTTPS |
| `K10Y_TLS_CERT`, `K10Y_TLS_KEY` | — | Your own certificate and key. Without them, a self-signed certificate is created. |
| `K10Y_TLS_DIR` | `/data/tls` in compose | Where the self-signed certificate is kept |

Compose-only settings (in `.env`):

| Variable | Default | Purpose |
|---|---|---|
| `POSTGRES_PASSWORD` | — | Database password |
| `K10Y_HUB_PORT` | `8443` | Port published on the host |
| `K10Y_DATA_DIR` | `./data` | Folder for the database, certificate and backups |
| `K10Y_BACKUP_EVERY_HOURS` | `24` | Backup interval |
| `K10Y_BACKUP_KEEP` | `14` | Backups to keep |
| `K10Y_IMAGE` | the release image | Override the hub image |
| `K10Y_SYSLOG_PUBLISH` | `5514` | Host UDP port published for HAProxy logs |

## Agent

Set in the agent's Deployment (`install.yaml`).

| Variable | Default | Purpose |
|---|---|---|
| `K10Y_TOKEN_SECRET` | `k10y-agent` in the installer | Read the API token from this Secret, creating it if missing |
| `K10Y_API_TOKEN` | — | Use this token instead of a Secret |
| `K10Y_CLUSTER_NAME` | `kubernetes` | Display name (the installer reads it from ConfigMap `k10y-settings`) |
| `K10Y_CONTROL_ENABLED` | `false` (`true` in `install.yaml`) | Allow actions. Also needs the control RBAC role. |
| `K10Y_PROTECTED_NAMESPACES` | `kube-system` (`kube-system,k10y` in the installer) | Namespaces where actions, edits and shells are refused |
| `K10Y_TLS` | `true` in the installer | Serve HTTPS |
| `K10Y_CORS_ORIGINS` | empty | Browser origins allowed to call the agent directly. Leave empty: browsers go through the hub. |
| `K10Y_METRICS_POLL_S` | `5` | How often metrics-server is checked |
| `K10Y_METRICS_RETENTION_S` | `3600` | CPU and memory history kept in memory |
| `K10Y_AUDIT_PATH` | `/data/audit.jsonl` | The agent's local action log |
| `K10Y_LOG_LEVEL` | `info` | Log level |
