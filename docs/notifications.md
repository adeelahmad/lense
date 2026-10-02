# Notifications

Lens can tell a chat room or another app when something happens in a namespace: a run finishes or fails, a batch
run finishes, something is added. Each namespace's owners choose where its notifications go (its **targets**) and
which events each target gets, on the namespace's page under **Notifications**.

## Targets

| Kind | Sends | For |
| --- | --- | --- |
| Matterbridge | a message to a gateway's API (`POST /api/message`) | Slack, Discord, Matrix, Telegram, IRC, Mattermost, Teams and the other chats [Matterbridge](https://github.com/42wim/matterbridge) relays to |
| Webhook | Lens's own JSON, signed | scripts, n8n, Node-RED, Home Assistant, anything that takes an HTTP POST |
| Slack-style | `{"text": …}` | Slack, Mattermost and Rocket.Chat incoming webhooks |
| Discord | `{"content": …}` | a Discord channel's webhook |

**Send test** sends a target a message at once and says how it went; **Sent** lists what a target was sent lately
(the latest 50, kept for 30 days), with each one's answer or error.

A target's address is sealed in the database once saved (Slack and Discord webhook URLs are credentials in
themselves): the page shows its scheme and host only, and changing it means giving it in full again.

### Matterbridge

Turn on Matterbridge's API and put it in the gateways that should get Lens's messages:

```toml
[api.lens]
BindAddress="0.0.0.0:4242"
Token="a-long-random-token"   # optional; give the same to Lens
Buffer=1000

[[gateway]]
name="lens"
enable=true

    [[gateway.inout]]
    account="api.lens"
    channel="api"

    [[gateway.inout]]
    account="slack.myteam"
    channel="general"
```

Then add a Matterbridge target in Lens with the API's address (`http://matterbridge:4242`; `/api/message` is added),
the gateway's `name` (`lens`), the token if you set one, and the name messages are sent under (Lens by default).

A Matterbridge next to Lens is on a private address, so an admin has to allow its network first (below).

### Webhooks

Each event is a `POST` of JSON:

```json
{
  "id": "job-41-failed-2026-10-02T12:00:05+00:00",
  "type": "job.failed",
  "timestamp": "2026-10-02T12:00:09+00:00",
  "namespace": "pods",
  "text": "“Episode 12” failed at transcribe in pods: RuntimeError: no model",
  "url": "https://lens.example.org/activity/41",
  "data": {"job": 41, "recording": 12, "title": "Episode 12", "status": "failed", "steps": ["transcribe", "analyze"], "error": "…"}
}
```

Deliveries are signed the [Standard Webhooks](https://www.standardwebhooks.com/) way. A new webhook target comes
with its secret (`whsec_…`), shown once; **New signing secret** under Edit replaces it. Each request carries
`webhook-id` (the same on every retry of a message), `webhook-timestamp` (Unix seconds) and `webhook-signature`
(`v1,` and the base64 HMAC-SHA256 of `<webhook-id>.<webhook-timestamp>.<body>`, keyed with the secret's bytes after
`whsec_`), plus `X-Lens-Event` with the event's type. To check one in Python:

```python
import base64, hashlib, hmac

def verified(secret, headers, body: bytes) -> bool:
    key = base64.b64decode(secret.removeprefix("whsec_"))
    signed = f"{headers['webhook-id']}.{headers['webhook-timestamp']}.".encode() + body
    want = base64.b64encode(hmac.new(key, signed, hashlib.sha256).digest()).decode()
    return any(hmac.compare_digest(s.split(",", 1)[1], want) for s in headers["webhook-signature"].split())
```

The Standard Webhooks libraries (`standardwebhooks` for Python, JavaScript, Go and more) verify them as they are;
check that the timestamp is recent too, to refuse replays.

## Events

| Event | When | `data` |
| --- | --- | --- |
| `job.succeeded` | a run finished | `job`, `recording`, `title`, `status`, `steps`, `finished_at`, `recording_url` |
| `job.failed` | a run failed (the step it failed at is in `text`) | the same, with `error` |
| `job.cancelled` | a run was cancelled | the same |
| `batch.finished` | a batch run finished, or its sample did | `batch`, `label`, `status`, `counts`, `sample` |
| `recording.added` | a recording, document, image or web page was added, however it came | `recording`, `title`, `source`, `created_at` |

New targets get `job.failed` and `batch.finished`. A batch run's own runs aren't sent one by one: the batch run is,
when it finishes. More than five things added to a namespace at once (a watched folder's new files, an import) are one
message (`data.recordings`, `data.count`).

`text` is the message chat targets get, and `url` links to the run, batch run or recording in the web app.

## How it's sent

A notifier runs in every process that does background work: the API server with inline workers, and each
`lens worker`. Every few seconds it reads what changed (runs that ended, recordings added) and queues it for the
targets that want it. Each event is claimed once across all of them, so several workers don't send it twice, and a
message that doesn't get through is tried again: after 30 seconds, then four times longer each time, up to six
hours, until `notifications.max_attempts`. A target that refuses a message (a 4xx other than 408, 425 and 429) isn't
tried again. Turning a target off drops what it still had waiting.

Nothing that happened before notifications were set up, or while they were off, is sent.

## Settings (admins)

Under Settings → Notifications (the `notifications` section, see [Configuration](configuration.md)):

| Setting | Default | |
| --- | --- | --- |
| `enabled` | `true` | Off: nothing is sent, and what happens meanwhile isn't sent later |
| `networks` | `[]` | Private networks targets may be in, like `192.168.1.0/24` or `172.16.0.0/12` (Docker) |
| `app_url` | `null` | Where links in messages point; `null` uses the server's `FRONTEND_URL` |
| `poll_seconds` | `5` | How often the notifier looks |
| `max_attempts` | `6` | How many times a message is tried |

Targets reach public addresses only unless their network is listed under `networks`. The notifier resolves the
target's host itself, refuses it when any of its addresses isn't allowed, and connects to the address it checked, so
a name can't be pointed at the server's own services in between; redirects aren't followed. Workers send the
messages, so a target has to be reachable from where the workers run.
