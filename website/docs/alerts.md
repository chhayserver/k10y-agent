# Alerts and notifications

The hub checks your alert rules against every cluster **every 30 seconds**. When a condition has held for the rule's hold time, the alert **fires** and a message is sent. When the condition clears, it **resolves**, and you can get a message for that too. All alerts are kept in PostgreSQL and shown on the **Alerts** page.

## 1. Add a channel

You need the `settings.manage` permission. Open **Administration → System settings → Alert channels → Add channel** and pick a type. **Save & send test** sends a test message right away. Tokens, Slack webhook URLs and passwords are stored encrypted. You can add several channels, for example one per team.

**Telegram**
1. In Telegram, message **@BotFather**, send `/newbot`, and copy the bot token.
2. Send any message to your new bot. For a group, add the bot to the group and post a message there.
3. Paste the token in k10y and click **Find chats**. Pick your chat, or paste a chat ID.

**Slack**: in Slack, open **Apps → Incoming Webhooks → Add to Slack**, pick a channel and paste the webhook URL (`https://hooks.slack.com/…`).

**Email**: your SMTP server (STARTTLS on 587, SSL/TLS on 465, or none), an optional username and password, the From address and one or more To addresses.

**Webhook**: k10y POSTs JSON to your URL for every message: `status` (`firing`, `resolved`, `reminder`, `acknowledged` or `test`), `rule`, `severity`, `title`, `message`, `cluster`, `resource`, `first_seen`, `acked_by`, `url` and `text`. With a **signing secret**, each request has the header `X-K10y-Signature: sha256=<hex>`, the HMAC-SHA256 of the body with your secret, so the receiver can check it came from k10y:

```python
import hashlib, hmac
expected = "sha256=" + hmac.new(SECRET.encode(), request_body, hashlib.sha256).hexdigest()
assert hmac.compare_digest(expected, request.headers["X-K10y-Signature"])
```

## 2. Create rules

Open **Alerts → New rule** (permission `alerts.manage`). Empty pages offer three presets: *Broken workloads*, *Node memory ≥ 90%* and *Cluster unreachable*.

<Shot src="/shots/alert-rule.png" alt="Editing an alert rule" url="k10y / alerts / rules" />

| Rule type | Fires when |
|---|---|
| **Cluster problems** | Anything on the Overview's problem list: crash loops, failing image pulls, OOM kills, pods pending too long, unavailable replicas, failed jobs and CronJobs, stuck rollouts, PVCs pending or lost, TLS certificates expiring within 14 days, NotReady nodes, node pressure. Filter by kind, and include or skip namespaces. |
| **CPU / memory threshold** | CPU or memory reaches a percentage on **any node** or for the **whole cluster**. |
| **Cluster unreachable** | The hub can't reach the cluster's agent. |

Options for every rule:
- **Clusters:** all clusters, or one.
- **Severity:** critical, warning or info.
- **Hold time:** how long the condition must last before it fires. A short blip never sends a message.
- **Remind every:** repeat the message while the alert is still firing (0 = only once).
- **Notify:** the channels to message. With none, alerts are only recorded.
- **Send a message when it resolves.**
- **Add an AI diagnosis:** the [AI assistant](./ai) investigates the broken object and its answer is added to the message.

## 3. Watch alerts

**Alerts** lists open alerts by default, with tabs for firing, resolved and all. Click one for its timeline (started, fired, resolved, notified), the object, any error sending the message, and the AI diagnosis.

- **Acknowledge** ("I'm on it"): the rule's channels are told who took it, and no more reminders are sent for it. Anyone who can see the alert can acknowledge it. When it resolves, the resolved message is still sent.
- **Silence…** (needs `alerts.manage`): mute **this alert**, also if it comes back, or **every alert of its rule**, for 30 minutes up to a week, with an optional reason. Silenced alerts are still recorded; only messages stop. Active silences are listed under the alerts, with **End now**.

<Shot src="/shots/alerts.png" alt="Alerts page with history" url="k10y / alerts" />

## Tips

- Set `K10Y_PUBLIC_URL` on the hub so messages include a link back to k10y.
- Skip noisy namespaces (for example `kube-system`) in your problem rules.
- If a message fails to send (wrong chat ID, bot removed from the group), the alert shows the error. It's only sent again if the rule has **Remind every** set, so use **Send test** on the channel after fixing it.
