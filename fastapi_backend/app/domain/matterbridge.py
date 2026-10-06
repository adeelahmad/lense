"""Matterbridge run by Lens: the optional `matterbridge` service in Docker Compose (packaging/matterbridge), configured
from Settings → Chat rooms instead of a matterbridge.toml written by hand.

With matterbridge.run on, Lens writes the config into the volume the two share (LENS_MATTERBRIDGE_DIR, /matterbridge
in Compose): one account per chat network from its token, a gateway per room named "<network>-<room>" (so a room can
be given to a namespace in bridge.rooms, like "slack-general = pods"), and an API account in each gateway that the
bridge (bridge.py) reads and answers through, with a token derived from the server's secret key. The service restarts
Matterbridge whenever the config changes, and keeps what it printed in matterbridge.log, where the WhatsApp QR code
to pair with is read from (whatsapp_qr).

Refine later: several accounts per network, Mattermost, IRC, XMPP and the other networks Matterbridge speaks.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re

from . import settings

API_PORT = 4242
# network: ({its account's config key: the matterbridge setting holding it}, the setting listing its rooms)
NETWORKS = {
    "slack": ({"Token": "slack_token"}, "slack_channels"),
    "discord": ({"Token": "discord_token", "Server": "discord_server"}, "discord_channels"),
    "telegram": ({"Token": "telegram_token"}, "telegram_chats"),
    "matrix": ({"Server": "matrix_server", "Login": "matrix_login", "Password": "matrix_password"}, "matrix_rooms"),
    "whatsapp": ({"Number": "whatsapp_number"}, "whatsapp_groups"),
}
QR_CHARS = set("█▀▄ ")


def conf_dir():
    """Where the config goes: the volume shared with the matterbridge service; None outside Compose."""
    return os.environ.get("LENS_MATTERBRIDGE_DIR") or None


def running(cfg):
    return bool((cfg.get("matterbridge") or {}).get("run"))


def api_url():
    return os.environ.get("LENS_MATTERBRIDGE_URL") or f"http://matterbridge:{API_PORT}"


def api_token(cfg):
    return hmac.new(settings.secret_key(cfg), b"lens/matterbridge/api", "sha256").hexdigest()[:40]


def gateway_name(network, room):
    slug = re.sub(r"[^a-z0-9]+", "-", room.lower().lstrip("#@!")).strip("-")[:40]
    return f"{network}-{slug or hashlib.sha1(room.encode()).hexdigest()[:8]}"


def networks(cfg):
    """{network: [rooms]} for the networks with what they need set and at least one room."""
    m = cfg.get("matterbridge") or {}
    out = {}
    for net, (fields, rooms) in NETWORKS.items():
        if all(m.get(k) for k in fields.values()) and m.get(rooms):
            out[net] = list(m[rooms])
    return out


def gateways(cfg):
    """[(gateway, network, room)] Lens's config has."""
    return [(gateway_name(net, room), net, room) for net, rooms in networks(cfg).items() for room in rooms]


def _s(v):
    return json.dumps(str(v))  # a TOML basic string: JSON's escapes are TOML's


def config_text(cfg):
    """matterbridge.toml for these settings."""
    m = cfg["matterbridge"]
    lines = [
        "# Written by Lens from Settings → Chat rooms; changes made here are overwritten.",
        "[general]",
        'RemoteNickFormat="{NICK}: "',
        "",
        "[api.lens]",
        f'BindAddress="0.0.0.0:{API_PORT}"',
        f"Token={_s(api_token(cfg))}",
        "Buffer=1000",
        'RemoteNickFormat="{NICK}"',
    ]
    used = networks(cfg)
    for net in used:
        lines += ["", f"[{net}.lens]"]
        lines += [f"{key}={_s(str(m[name]).strip())}" for key, name in NETWORKS[net][0].items()]
        if net == "whatsapp":  # paired once, kept in the volume
            lines.append(f"SessionFile={_s(os.path.join(conf_dir() or '/matterbridge', 'whatsapp'))}")
    for gw, net, room in gateways(cfg):
        lines += [
            "",
            "[[gateway]]",
            f"name={_s(gw)}",
            "enable=true",
            "[[gateway.inout]]",
            f'account="{net}.lens"',
            f"channel={_s(room)}",
            "[[gateway.inout]]",
            'account="api.lens"',
            'channel="api"',
        ]
    return "\n".join(lines) + "\n"


def write(cfg):
    """Write the config when it changed (atomically, readable by Matterbridge only). Returns what went wrong or None."""
    where = conf_dir()
    if not where:
        return "Lens isn't running in Docker Compose, so it can't run Matterbridge"
    path = os.path.join(where, "matterbridge.toml")
    if not gateways(cfg):  # Matterbridge won't start without a room: the service waits for one
        try:
            os.remove(path)
        except OSError:
            pass
        return "add a chat network and at least one of its rooms"
    text = config_text(cfg)
    try:
        with open(path, encoding="utf-8") as f:
            if f.read() == text:
                return None
    except OSError:
        pass
    try:
        tmp = path + ".tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    except OSError as e:
        return f"couldn't write Matterbridge's config ({e.strerror}): is the matterbridge service running?"
    return None


def _log():
    where = conf_dir()
    if not where:
        return []
    try:
        with open(os.path.join(where, "matterbridge.log"), encoding="utf-8", errors="replace") as f:
            return f.read()[-200_000:].splitlines()
    except OSError:
        return []


def whatsapp_qr():
    """The QR code WhatsApp is waiting to be scanned with (Linked devices › Link a device), as the lines Matterbridge
    drew it in; None when it isn't waiting."""
    lines, qr, block = _log(), None, []
    for line in lines:
        if line.strip() and set(line) <= QR_CHARS:
            block.append(line)
            continue
        if block:
            qr, block = block, []
        if "WhatsApp connection successful" in line or "QR channel result" in line:
            qr = None
    if block:
        qr = block
    return "\n".join(qr) if qr and len(qr) >= 10 else None


def status(cfg):
    """For Settings → Chat rooms: whether Lens runs Matterbridge, its rooms, and the WhatsApp QR code when pairing."""
    if not running(cfg):
        return {"run": False}
    return {
        "run": True,
        "gateways": [{"gateway": gw, "network": net, "room": room} for gw, net, room in gateways(cfg)],
        "whatsapp_qr": whatsapp_qr() if "whatsapp" in networks(cfg) else None,
        "error": write(cfg),
    }
