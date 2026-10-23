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

## Retention

Entries are kept until you delete them from the database, and they're included in the [backups](./backups). Each agent also keeps a small local log of its own actions.
