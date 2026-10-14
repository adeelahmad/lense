# Logging

Every Lens process (the API, each `lens worker`, `lens watch`) writes one structured record per log line, and sends it
to up to three places:

- **The console**, which is what `docker logs` and `docker compose logs` show: as text, or as one JSON object per line
  for a log shipper (**Format**).
- **A file per process** under `data_dir/logs` (`api-<host>.jsonl`, `worker-<host>.jsonl`, …), one JSON object per
  line, rotated at **File size** and kept **Files kept** times. **Settings → Logs** reads these, so its viewer shows the
  workers' lines as well as the API's. A file no process has written for 14 days (an old container's) is removed.
- **A syslog server**, off by default (below).

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

Not yet built: sending straight to Loki or another HTTP endpoint, and client certificates for TLS.
