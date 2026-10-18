# Logging

Every Lens process (the API, each `lens worker`, `lens watch`) writes one structured record per log line, and sends it
to up to four places:

- **The console**, which is what `docker logs` and `docker compose logs` show: as text, or as one JSON object per line
  for a log shipper (**Format**).
- **A file per process** under `data_dir/logs` (`api-<host>.jsonl`, `worker-<host>.jsonl`, …), one JSON object per
  line, rotated at **File size** and kept **Files kept** times. **Settings → Logs** reads these, so its viewer shows the
  workers' lines as well as the API's. A file no process has written for 14 days (an old container's) is removed.
- **A syslog server**, off by default (below).
- **An HTTP endpoint**: Grafana Loki's push API, or JSON for Vector, Fluent Bit, Logstash and the like; off by default
  (below).

Change any of it in **Settings → Logs**; the API and every worker follow within a few seconds, without a restart.
`LENS_LOG_LEVEL` and `LENS_LOG_FORMAT` in .env win over Settings, which then show those fields locked.

## What a record holds

```json
{"ts": "2026-10-10T20:02:19.809Z", "level": "warning", "logger": "lens.email", "msg": "…",
 "process": "worker", "pid": 42, "host": "lens-worker-1", "exc": "Traceback …", "fields": {"job": 7}}
```

`exc` is there when the line carries an error's traceback, and `fields` when the code logged extra values.

**No secrets.** Before a line goes anywhere it is redacted: bearer and basic credentials, `key=value` pairs whose key
names a key, token, secret, password, cookie or authorization, the password in a URL (`https://user:***@host`), and
well-known key shapes (`sk-…`, `ghp_…`, `xox…-`, JSON web tokens) become `***`. Extra fields whose name says key, token,
secret or password are dropped to `***` too.

## Levels

**Level** (info by default) is what is kept on the console and in the files. **Logger levels** gives single loggers
their own, one per line, like `httpx warning` to quiet every outgoing request, or `lens.bridge debug` to follow the
chat-room bridge closely. Lens's own loggers start with `lens`; the web server's access log is `uvicorn.access`.

## Sending to a syslog server

Turn on **Send to syslog** and set the server's **Host** and **Port**. Lens speaks
[RFC 5424](https://www.rfc-editor.org/rfc/rfc5424) over:

| Transport | Standard | Framing |
| --- | --- | --- |
| UDP (default, port 514) | RFC 5426 | one line per datagram |
| TCP | RFC 6587 | octet counting (`<length> <message>`), or one line per newline for servers that want that |
| TLS (usually port 6514) | RFC 5425 | as TCP; the server's certificate is checked unless **Verify the certificate** is off |

Each message is `<PRI>1 TIMESTAMP HOST APP-NAME PROCESS.PID LOGGER - MESSAGE`, where PRI combines **Facility**
(local0 by default) with the line's level, APP-NAME is **App name** (`lens`), and MESSAGE is the line (and its
traceback) or, with **Message format** JSON, the whole record above. Only lines at **Syslog level** or above are sent.
Messages longer than 8 KB are cut.

Lines are queued and sent from a background thread, so a slow or unreachable server never holds up Lens. While the
server can't be reached Lens retries, waiting up to 30 seconds between tries, and once 10,000 lines are waiting new
ones are dropped and counted. **Send a test line** sends one message to the saved server, even while sending is off,
and Settings → Logs shows how many lines the API has sent and dropped and the last error.

rsyslog, syslog-ng, Graylog, Grafana Loki (through promtail or Alloy's syslog receiver), Splunk, Papertrail and most
log tools take RFC 5424 syslog.

### Client certificates and a private CA

A log server that wants to know who is sending (mutual TLS) asks for a client certificate. Paste this machine's
certificate and its unencrypted private key, as PEM, into **Client certificate**: Lens shows it to syslog over TLS and
to an https endpoint. It's stored encrypted and never shown again; line breaks lost in pasting are put back, and a
certificate without its key, or one ssl can't load, isn't saved. When a private CA signed the log server's own
certificate, paste that CA's certificate into **Server's CA certificate**, and Lens checks the server against it
instead of the system's CAs (with **Verify the certificate** on).

## Sending to Loki or another HTTP endpoint

Turn on **Send to an HTTP endpoint** and set its **Address**. Lines are POSTed in batches (up to 500 lines, or what
arrived in 2 seconds), in one of three **Formats**:

| Format | Request | Takes it |
| --- | --- | --- |
| Loki push API (default) | `POST /loki/api/v1/push`, `{"streams": [...]}` | Grafana Loki, Grafana Cloud Logs, Alloy's `loki.source.api` |
| JSON array | one array of records | Fluent Bit's `http` input, Logstash's `http` input, Vector's `http_server` |
| One JSON object per line | `application/x-ndjson` | Vector, OpenObserve and other NDJSON receivers |

For Loki the server's address is enough (`http://loki:3100`); `/loki/api/v1/push` is added when the address has no
path. Each Loki line is the whole record above as JSON (`| json` in LogQL reads its fields), in a stream labelled
`service_name="lens"`, `host`, `process` and `level`. The other formats send the records as they are.

**Headers** are for a server that wants a token: `key=value` pairs separated by commas, values URL-encoded, like
`Authorization=Bearer%20abc,X-Scope-OrgID=home` (Grafana Cloud takes `Authorization=Basic%20<base64 of user:token>`).
They're stored encrypted and never shown again, and they're dropped when the address moves to another server, so a
token never goes somewhere it wasn't meant for. The address can't carry a user name or password, and can't be a
link-local address (169.254.0.0/16, fe80::/10, where cloud metadata lives); Lens follows no redirects and uses no proxy
from the environment for it. **Verify the certificate** (on) checks an https server's certificate.

Only lines at **HTTP level** or above are sent. As with syslog, lines wait in a queue of up to 10,000 sent from a
background thread, so a slow or unreachable endpoint never holds up Lens; a batch that fails is tried again, waiting up
to 30 seconds between tries. **Send a test line** sends one line to the saved endpoint even while sending is off, and
Settings → Logs shows how many lines the API has sent and dropped and the last error. Agents do the same with
`PUT /api/v1/settings/logs`, `POST /api/v1/settings/logs/test?to=http` and `GET /api/v1/settings/logs/status`.
