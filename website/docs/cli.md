# CLI

The `k10y` command-line tool talks to agents directly, using each cluster's API token. It's handy for scripts and for people who live in the terminal.

## Install

```bash
git clone https://github.com/zorouzumaki1111-cmd/k10y-agent.git && cd k10y-agent
python3 -m venv .venv && .venv/bin/pip install -e .
source .venv/bin/activate
```

## Add clusters

```bash
k10y cluster add lab https://192.168.64.2:30800 --token <token> --insecure   # --insecure (-k): self-signed certificate
k10y cluster list
k10y cluster use lab
k10y cluster remove lab
```

The configuration is stored in `~/.k10y/config.yaml` with file mode 600.

## Look around

```bash
k10y status --all                    # health of every cluster
k10y nodes
k10y pods --problems                 # only pods with problems
k10y deployments -n demo
k10y services
k10y events --warnings
k10y logs demo web-7847b45f49-29mvz --tail 100 --previous
```

## Take action

Add `--dry-run` to any action to validate it without changing anything. Delete and drain ask for confirmation.

```bash
k10y scale demo web 3
k10y restart demo web
k10y set-image demo web nginx:1.28
k10y history demo web
k10y rollback demo web --to-revision 2
k10y delete pod demo web-7847b45f49-29mvz
k10y cordon node-1 / uncordon node-1 / drain node-1
k10y apply -f app.yaml
k10y audit --limit 50
```

Use `-C <cluster>` to target a cluster other than the current one.

::: tip CLI vs. hub
The CLI uses the agent's token, which gives full access to that agent, so it bypasses the hub's roles. Its actions are written to the agent's own log with your local username (`cli:<user>`), not to the hub's audit log. For teams, prefer the hub, and keep agent tokens with a few administrators.
:::
