# Sensors

A **sensor** is anything that feeds Lens. There are two families, in one list (`GET /api/v1/sensors`):

| Family | Kinds | Channels | What it reports becomes |
| --- | --- | --- | --- |
| Files | S3, Dropbox, Google Drive, OneDrive, SFTP, SMB, WebDAV, a local folder (through rclone); email (IMAP); calendar feeds (iCal) | the folders it watches | resources, run through their pipelines (as sources always have) |
| Streams | MQTT devices, syslog senders, webhooks | streams: an MQTT topic, a syslog program, a webhook's stream name | readings, kept with retention, and hourly summaries of anything numeric |

File sensors are the storage sources: they are added and changed as sources (`/api/v1/sources`), and nothing about
them changes. Stream sensors are new, and everything below is about them.

Sensors are off until an admin turns them on (Settings → Sensors, or `PUT /api/v1/settings/sensors {"enabled":
true}`). Nothing calls a language model or the decision model: readings are stored as they come.

## Ways in

**The MQTT hub.** Lens runs an MQTT broker (3.1 and 3.1.1) on port 1883. Point devices at it as you would at
Mosquitto: Zigbee2MQTT, Tasmota, ESPHome, OpenWrt's collectd, Home Assistant's MQTT statestream. It is a normal local
broker too: anything can subscribe to it, with `+` and `#`, and retained messages and last wills work. Devices sign
in with a hub login (`POST /api/v1/sensor-logins {username, password, space?}`): new devices that sign in with a login
that has a namespace go into that namespace. `sensors.mqtt_anonymous` lets clients connect without a login (off by
default). Not yet: MQTT 5 (most clients fall back to 3.1.1), persistent sessions, delivery above QoS 0 to subscribers.

**Syslog.** Lens listens for syslog on UDP and TCP port 5514 (both RFC 5424 and the older BSD format, as routers,
Pi-hole, dnsmasq, Unbound, NAS boxes and firewalls send it). Only senders on `sensors.syslog_networks` are heard
(private networks by default). Each sending address is a sensor; each program (`dnsmasq`, `kernel`) a stream, and each
line keeps its severity.

**Webhooks.** `POST /api/v1/sensors {"type": "webhook", "name": "Speed test"}` answers with a token, shown once. Send
readings to `POST /api/v1/sensors/push` with `Authorization: Bearer <token>`, or to
`/api/v1/sensors/push/<token>/<stream>` for devices that can only be given a URL. Plain text is a reading per line;
JSON is one reading, or several as `{"readings": [{"stream": "down", "value": 412.5}]}`.

In Docker, the hub runs in the `worker` container, which publishes 1883 and 5514; set `LENS_MQTT_PORT` or
`LENS_SYSLOG_PORT` when something else on the machine has those ports. Run natively, it runs in `lens worker` (and in
the API when it runs background work). Several processes can run it: the first to get the port serves it, and the
others try again every minute.

## Organising itself

A device publishing to the hub, or a host sending syslog, becomes a sensor the first time it does, marked **new**. Its
device is worked out from its topics (`zigbee2mqtt/kitchen`, Tasmota's `tele/plug-1`, else the topic without its last
level); add an MQTT sensor by hand with a `prefix` to group topics differently, or a syslog sensor with an `address`,
to give it a name, namespace and handling before it reports. Each stream's kind is worked out from what it sends:
number, on/off (`ON`, `true`, `open`, `online`...), JSON (its top-level numbers are summarised one by one), text or
log.

When someone looks, each new sensor has a **suggested handling**, from rules rather than a model: log lines keep
everything for two weeks and warnings and errors for six months; numbers that arrive several times a minute keep only
changes for a week and hourly summaries for two years; and so on. It is applied only when chosen
(`POST /api/v1/sensors/{id}/suggestion`, or `POST /api/v1/sensors/review` for every new sensor), which marks the sensor
looked at.

## Handling

Each stream sensor's handling is its own choices over the `sensors` settings (a choice set to null goes back to the
setting; 0 days keeps them for good):

| Setting | Means | Default |
| --- | --- | --- |
| `store` | `all` readings; `changes` (a reading when its value differs from the last kept, and at least one an hour); `summary` (hourly summaries only); `none` (counted and dropped) | `all` |
| `raw_days` | days readings are kept (null: for good) | 30 |
| `important_days` | days log lines of warning or worse are kept, when longer than `raw_days` | 180 |
| `rollup_days` | days hourly summaries (count, min, max, average, last) are kept | 365 |
| `max_per_minute` | readings a stream may send a minute; more are counted and dropped | 600 |

A sensor can also be **paused** (nothing kept) or **ignored** (nothing kept, out of sight).

Retention runs as a routine action, `{"type": "sensors"}` (optionally `"sensors": [ids]`): the **Tidy sensor data**
routine runs it every hour, and does nothing while there are no stream sensors.

## Reading it back

- `GET /api/v1/sensors/{id}`: the sensor with its streams (kind, last value, counts).
- `GET /api/v1/sensors/{id}/readings?stream=&before=&limit=`: readings, newest first.
- `GET /api/v1/sensors/{id}/series?stream=&field=&hours=`: hourly summaries, oldest first.

## Refine later

Log patterns (the same line with different numbers grouped), triage of patterns by the decision model (opt-in), bridges
to another broker, daily digests as documents in a namespace, sensors for namespace members (admins only for now), and
the web pages.
