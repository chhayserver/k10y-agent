---
title: Changelog
---

# Changelog

Every release is a multi-arch image on Docker Hub (`k10y/k10y-agent`, amd64 and arm64) with `install.yaml`, `install-readonly.yaml` and `docker-compose.yml` attached to the [GitHub release](https://github.com/zorouzumaki1111-cmd/k10y-agent/releases).

## 2.1.0 <Badge type="tip" text="latest" />

**New**
- **Pod shell:** an interactive terminal in any running container, in the browser. Needs the new `pods.exec` permission; every session is audited.
- **Edit YAML** in the details panel, like `kubectl edit`: a line-by-line diff, dry run, and a conflict check if someone else changed the object. New permission `resources.edit`.
- **Sessions:** see your signed-in devices and sign them out. Admins can sign a user out everywhere, and disable or enable accounts.
- **Settings follow you:** sidebar pages, theme, refresh, start page and chart range are saved to your account.
- **Automatic backups** of the hub database (daily, newest 14 kept), plus `make hub-backup` and `make hub-restore`.
- New sign-in page with the k10y-agent logo, a show/hide button on every password field, and a password strength meter.

**Security**
- Agents are **API-only**: the old built-in dashboard and `K10Y_MODE=dashboard` were removed, and cross-origin browser calls are off by default.

**Upgrade notes:** apply the new `install.yaml` (it adds `pods/exec` to the control role) and use the new `docker-compose.yml` (it adds the `backup` service). `pods.exec` isn't in the Operator role by default.

## 2.0.0

- **The hub:** a central server with PostgreSQL, login, fully custom roles (per cluster or global), encrypted secrets and a permanent audit log. The browser only talks to the hub, and agents are reached over HTTPS.
- **HTTPS everywhere**, with self-signed certificates by default. No Ingress needed.
- **New dashboard** in Vue 3 + TypeScript.
- **Alerts:** problem, CPU/memory threshold and unreachable-cluster rules, with Telegram notifications and history.
- **Live log streaming** for a pod or every pod of a workload.
- New pages: **CronJobs** (run now, suspend, resume), **Autoscalers**, **Ingresses** and **Storage**.

**Upgrade notes:** agent URLs are now `https://`. Add your clusters in the hub; the old browser-storage dashboard is replaced.

## 1.5.0
- **Namespaces** page: status, pods, problems, deployments, services, CPU and memory per namespace.
- **Settings:** hide sidebar pages, choose theme, refresh interval and start page.

## 1.4.0
- **StatefulSets** (scale, restart), **DaemonSets** (restart) and **Jobs** (delete with their pods) pages.
- Under-replicated StatefulSets and DaemonSets, and failed Jobs, appear under Problems.

## 1.3.0
- **Details panel** for every resource: overview, related objects with drill-down, live YAML and events.

## 1.2.1
- Keep keys for **both** AI providers and switch between them with one click.

## 1.2.0
- **AI assistant** with Google Gemini or any OpenAI-compatible API. It reads with read-only tools, explains root causes and proposes fixes. Nothing changes until a person approves.

## 1.1.0
- **One-command install:** `kubectl apply -f install.yaml`, and the agent creates its own API token.

## 1.0.0
- First release: monitoring, problems, logs and events; control actions with dry run, protected namespaces and audit; the map; multi-cluster dashboard; live CPU and memory charts; multi-arch image.
