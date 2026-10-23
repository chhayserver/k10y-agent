---
title: Security
---

# Security

k10y sits between people and your clusters, so it's built to be the safest way in, not another way around your controls. This page explains how.

## The short version

- People sign in to the **hub**. They never hold kubeconfigs or agent tokens.
- Every request to a cluster is **checked against the user's roles** in the hub, then forwarded to that cluster's agent over **HTTPS**.
- Every change, every login and every shell session is **audited** with the user who did it.
- Secrets at rest (agent tokens, AI keys, Telegram tokens) are **encrypted**. Secret *values* from your clusters are never sent to the browser.

## Identity and sessions

| Control | How |
|---|---|
| Password storage | argon2id hashes |
| Password policy | At least 8 characters, letters plus numbers or symbols; first login forces a change |
| Brute force | 5 failed attempts per 5 minutes per username and IP, then a pause |
| Sessions | Random tokens stored hashed on the server; `HttpOnly`, `Secure`, `SameSite=Strict` cookie; 12-hour sliding expiry |
| CSRF | Every change needs a custom header that other sites can't send |
| Device control | Users see and sign out their devices; admins sign a user out everywhere or disable the account instantly |

## Authorization

Roles are fully custom, built from [21 permissions](/docs/users-roles). A role can apply to **all clusters** or to **one cluster**. Clusters a user has no role on are invisible to them. The hub refuses any write request that doesn't map to a known action, so new agent endpoints are denied until they're added to the permission map.

## Data protection

- **In transit:** the hub and every agent serve HTTPS. They generate a self-signed EC certificate by default, or you can bring your own. Verifying the agent's certificate can be switched on per cluster.
- **At rest:** agent tokens, AI keys and Telegram bot tokens are encrypted with a key derived from `K10Y_SECRET_KEY`. PostgreSQL isn't published outside the Docker network.
- **Secrets in clusters:** the agent only returns Secret names, types and key names. Values are never returned, never shown and never given to the AI assistant. Secrets can't be edited from the dashboard.
- **AI:** optional. When you turn it on, the cluster data the assistant reads (events, specs, logs) is sent to the provider you chose.

## In each cluster

- The agent runs as **non-root** with a **read-only root filesystem**, all Linux capabilities dropped, and the RuntimeDefault seccomp profile.
- **Least-privilege RBAC:** read access by default. Write access is opt-in (`install.yaml` versus `install-readonly.yaml`) and includes **no RBAC or CRD permissions**, so even Apply YAML can't be used to grant more rights.
- **Protected namespaces** (`kube-system` and `k10y` by default) are enforced by the agent itself: no actions, no edits, no shells there.
- **API-only:** agents serve no web page and allow no cross-origin browser calls by default.

## Pod shells

Opening a shell needs the `pods.exec` permission, control enabled on that agent, a running pod and a namespace that isn't protected. The WebSocket only accepts pages served by the hub itself, which blocks cross-site WebSocket hijacking. Opening and closing a shell, with its duration, is written to the audit log.

## Audit and recovery

Every control attempt is stored in PostgreSQL: dry runs, refusals, failures and unreachable agents included. So are logins, admin changes, YAML edits and shell sessions, each with user, cluster, target, parameters, result and client IP. The database is [backed up automatically](/docs/backups).

## Reporting a vulnerability

Please **don't open a public issue**. Use GitHub's [private vulnerability reporting](https://github.com/zorouzumaki1111-cmd/k10y-agent/security/advisories/new) instead, with steps to reproduce. We'll confirm receipt, fix the issue, and credit you if you'd like.
