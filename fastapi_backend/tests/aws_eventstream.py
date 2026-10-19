"""AWS's event stream encoding (application/vnd.amazon.eventstream), which SageMaker's InvokeEndpointWithResponseStream
and Bedrock's ConverseStream answer in, for fakes to answer in it: each message is a prelude (its length and its
headers' length, with their CRC32), string headers, the payload and the message's CRC32. Lens reads them with
botocore's own decoder, so a message made wrong here fails there."""

from __future__ import annotations

import json
import struct
import zlib


def message(headers, payload=b""):
    hs = b""
    for name, value in headers.items():
        n, v = name.encode(), value.encode()
        hs += struct.pack("!B", len(n)) + n + struct.pack("!BH", 7, len(v)) + v  # type 7: a string
    total = 12 + len(hs) + len(payload) + 4
    prelude = struct.pack("!II", total, len(hs))
    head = prelude + struct.pack("!I", zlib.crc32(prelude)) + hs + payload
    return head + struct.pack("!I", zlib.crc32(head))


def event(kind, payload, content_type="application/json"):
    body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
    return message({":message-type": "event", ":event-type": kind, ":content-type": content_type}, body)


def exception(kind, payload):
    return message(
        {":message-type": "exception", ":exception-type": kind, ":content-type": "application/json"}, json.dumps(payload).encode()
    )
