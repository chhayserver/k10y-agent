# Troubleshooting & FAQ

## The browser warns about the certificate

The hub uses a self-signed certificate by default. Choose *Advanced → Proceed* once, or [use your own certificate](./install-hub#https-certificate).

## "Too many failed attempts"

After 5 failed sign-ins in 5 minutes for a username from one IP address, sign-in pauses. Wait a few minutes and try again.

## I forgot the admin password

If another admin exists, they can set a new password on **Administration → Users**. If you're locked out completely, set one from the hub container:

```bash
docker compose exec -T hub python - <<'EOT'
from sqlalchemy import select
from k10y_agent.config import Settings
from k10y_agent.hub.db import Database, User
from k10y_agent.hub.security import hash_password
with Database(Settings().database_url).session() as s:
    u = s.execute(select(User).where(User.username == "admin")).scalar_one()
    u.password_hash = hash_password("Temporary-pass-1"); u.must_change_password = True; u.is_active = True
    s.commit()
print("password reset; change it at next sign-in")
EOT
```

## "Cannot reach agent" when adding a cluster

- Use `https://`, not `http://` (agents serve HTTPS since 2.0).
- Check the hub can reach the node: `curl -k https://<node-ip>:30800/healthz` should return `{"status":"ok"}`.
- Check firewalls and security groups for port 30800.
- Leave **Verify TLS** off unless the agent has a certificate the hub trusts.
- If the hub runs in Docker on the same machine as a local cluster, `localhost` means the container. Use the machine's IP address instead.

## The charts are empty

The charts need **metrics-server**. Check `kubectl top nodes`. Points arrive about every 15 seconds, and history starts again when the agent restarts.

## A button is missing

Buttons only appear when the action is allowed:

| Missing | Check |
|---|---|
| Any action | Your role's permissions on **this** cluster; the agent installed with `install.yaml` (control enabled) |
| Shell | `pods.exec`, pod is Running, namespace not protected |
| Edit (YAML) | `resources.edit`, not a Secret, namespace not protected |
| Admin pages | The role must be assigned on **all clusters** |

## Telegram messages don't arrive

- Send a message to the bot first (bots can't start a chat with you), or add it to the group.
- Use **Find chats** to get the right chat ID. Group IDs start with `-`.
- Press **Send test** on the channel. The error from Telegram is shown if it fails.
- Check the rule has the channel ticked under **Notify**.

## Where is my data?

In `./data` next to `docker-compose.yml`. See [Install the hub](./install-hub#where-data-is-stored) and [Backups](./backups).

## Does k10y send data anywhere?

No, unless you turn on the AI assistant (data goes to the provider you chose) or Telegram alerts (alert messages go to Telegram). The hub and agents don't phone home.

## Is k10y free?

Yes. It's free and open source, and you host it yourself.
