# Audit log

Everything that changes something is recorded in the hub's PostgreSQL database, with the person who did it. Open **Activity** to see it. You need the `audit.view` permission.

<Shot src="/shots/activity.png" alt="Activity page: the audit log" url="k10y / activity" />

## What's recorded

| Kind | Examples |
|---|---|
| Actions | scale, restart, set image, rollback, delete, cordon, drain, apply, CronJob run/suspend/resume |
| Edits | `edit` with the object's kind, namespace and name |
| Shells | `exec` when a shell opens and closes, with its duration |
| Sign-in | successful and failed logins, password changes, signed-out sessions |
| Administration | users, roles, clusters, AI settings, alert channels and rules |
| AI | every investigation, with the model and the number of issues found |

Each entry has the time, user, cluster, action, target, parameters, result (success, dry run or failed), a message and the client IP. **Dry runs, refused actions and attempts against unreachable clusters are recorded too.**

Read-only views (opening pages, reading logs) are not recorded.

## Filters

Search by user, action, target or message. Show **All**, only **Changes**, or only **Failed** entries, for all clusters or the current one.

## Download

**Download** on the Activity page saves the last 90 days you can see (all clusters, or the current one) as CSV. The API also gives JSON: `GET /api/hub/audit/export?format=json&days=30`.

## Export to a SIEM or log system

**System settings → Activity log export** (needs `settings.manage`) copies every entry as it's written, including shells and port-forwards, to any of:

- **Webhook:** JSON batches (`{"entries": [...]}`) POSTed every few seconds, for Splunk HEC, Elastic, Datadog or your own service. With a signing secret, `X-K10y-Signature: sha256=<hex>` is the HMAC-SHA256 of the body.
- **Syslog:** RFC 5424 lines with the entry as JSON, over UDP, TCP or TLS (rsyslog, syslog-ng, most SIEMs).
- **Files:** one JSON line per entry in `/data/audit/audit-YYYY-MM-DD.jsonl` inside the hub (in `./data/hub/audit` with Docker Compose), with old files removed after the days you choose.

**Save & send test** sends one test entry to every destination you turned on and shows any error. Each entry has `time`, `user`, `cluster`, `action`, `target`, `ok`, `dry_run`, `message`, `params`, `client_ip` and `source: "k10y"`.

## Retention

**Keep in k10y** sets how long entries stay in the database: forever (the default), 90 or 180 days, 1 or 2 years. Older entries are removed once an hour; exported copies aren't affected. Entries are included in the [backups](./backups). Each agent also keeps a small local log of its own actions.
