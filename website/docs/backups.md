# Backups and restore

The hub's `backup` service dumps the whole database (users, roles, clusters, settings, audit log and alerts) to `./data/backups` **every 24 hours**, and keeps the **newest 14**.

```
data/backups/
  k10y-20261022-151448.sql.gz
  k10y-20261023-151449.sql.gz
  ...
```

Change the schedule in `.env`:

```bash
K10Y_BACKUP_EVERY_HOURS=6
K10Y_BACKUP_KEEP=28
```

Check that backups are working:

```bash
docker compose logs backup
# backup-1 | 2026-10-22T15:14:49Z backup ok: k10y-20261022-151448.sql.gz (8.0K)
```

## Back up now

From the repository:

```bash
make hub-backup
```

Or with Docker only:

```bash
docker compose exec -T postgres pg_dump -U k10y --clean --if-exists --no-owner k10y | gzip > k10y-$(date +%F).sql.gz
```

## Restore

Restoring **replaces** the current data.

```bash
make hub-restore FILE=data/backups/k10y-20261022-151448.sql.gz
```

Or with Docker only:

```bash
docker compose stop hub
gunzip -c data/backups/k10y-20261022-151448.sql.gz | docker compose exec -T postgres psql -q -v ON_ERROR_STOP=1 -U k10y -d k10y
docker compose start hub
```

## What else to keep

::: danger Keep `.env` with your backups
`K10Y_SECRET_KEY` in `.env` encrypts agent tokens, AI keys and Telegram tokens. A restored database is useless for those without the same key.
:::

- **Copy backups off the machine.** `./data/backups` is on the same disk as the database. Sync it to another server or object storage.
- **Test a restore** now and then, for example into a scratch database.
