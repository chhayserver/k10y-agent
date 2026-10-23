# What is k10y?

k10y-agent is a self-hosted, open-source control room that brings your DevOps tools into one place. Today it manages **Kubernetes clusters** and **HAProxy load balancers**; more systems are on the [roadmap](/roadmap). Your team monitors, troubleshoots and safely changes all of them from one dashboard, with one login, one set of roles, one audit log, one alert pipeline and an optional AI assistant.

<Shot src="/shots/overview.png" alt="k10y overview: cluster health, live CPU and memory charts, problems and recent warnings" url="k10y / lab / overview" />

## What you can do with it

- **See everything:** health, problems, live CPU and memory, and every common resource type, from Deployments to PVCs, across all your clusters.
- **Troubleshoot fast:** live logs for a whole workload, a shell in any container, events, and a relationship map.
- **Change things safely:** scale, restart, roll back, edit YAML, drain nodes and more. Every action has a dry run, and destructive ones need confirmation.
- **Control who does what:** custom roles per cluster, with every change recorded against the person who made it.
- **Know when something breaks:** alert rules with Telegram notifications.
- **Ask for help:** an AI assistant that investigates with read-only tools and proposes fixes for you to approve.

## How it fits together

k10y has two parts:

| Part | Runs | Job |
|---|---|---|
| **Hub** | Docker Compose on any server, with PostgreSQL | The website people sign in to. Users, roles, cluster connections, audit, alerts and AI live here. |
| **Agent** | One small pod in each cluster | Reads the Kubernetes API and metrics-server, and carries out actions. API-only. |

People only ever talk to the hub. The hub checks their permissions and forwards each request to the right agent over HTTPS. [Read the architecture →](./architecture)

## Requirements

- **Hub:** a machine with Docker and Docker Compose (Linux, macOS or Windows), reachable by your users on one port (8443 by default).
- **Each cluster:** a recent Kubernetes (developed and tested on 1.36 with RKE2 and k3s; anything with `batch/v1` CronJobs and `autoscaling/v2` HPAs, 1.23+, should work), `kubectl` with admin rights for the install, and metrics-server for CPU and memory charts. RKE2, k3s, EKS, GKE and AKS include it. The hub must be able to reach port 30800 on a node.
- **Images:** `docker.io/k10y/k10y-agent` for amd64 and arm64.

## One place for every system

The sidebar has a **system switcher**: pick Kubernetes or HAProxy, then a cluster or HAProxy instance. Everything that matters across systems is shared: **Home** (health of everything), **Alerts**, **Activity** (audit), **users and roles**, and **My settings**. New systems plug into the same hub, so they get all of that from day one.

## Who makes k10y

k10y-agent is created and maintained by **Kang Kimchhay**.

## Next steps

1. [Quick start](./quick-start): running in about five minutes.
2. [Dashboard tour](./dashboard): what each page does.
3. [Users, roles and permissions](./users-roles): invite your team.
4. [HAProxy](./haproxy): connect your load balancers.
