"""Signing media links in API responses.

Audio, video, frames and word clouds are fetched by <audio>, <video> and <img> tags, which can't send the
Authorization header. The domain layer builds plain links (``/api/v1/recordings/12/audio``); before a response leaves,
``sign_urls`` turns every such link into a signed one that works on its own until it expires. Only responses already
limited to what the caller may read pass through here, so a signed link never grants more than the caller had.
"""

from __future__ import annotations

import re
import urllib.parse
from typing import Any, cast

from app.core.security import sign_path
from app.domain.store import API

MEDIA_RX = re.compile(rf"^{re.escape(API)}/recordings/\d+/(audio|media|wordcloud\.svg|frames/[\w.-]+)$")
NS_MEDIA_RX = re.compile(rf"^{re.escape(API)}/namespaces/[a-z0-9][a-z0-9_-]*/wordcloud\.svg$")
REPORT_RX = re.compile(r"^/(reports|embed)/")


def sign_url(url: str | None) -> str | None:
    if not url:
        return url
    path, _, query = url.partition("?")
    if not (MEDIA_RX.match(path) or NS_MEDIA_RX.match(path) or REPORT_RX.match(path)):
        return url
    params = dict(urllib.parse.parse_qsl(query))
    params.pop("exp", None)
    params.pop("sig", None)
    return sign_path(path, **cast(dict[str, Any], params))


def sign_urls[T](obj: T) -> T:
    """Sign every media link found in a JSON-like structure (in place for dicts and lists; returns it)."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(v, str):
                obj[k] = sign_url(v)
            elif isinstance(v, (dict, list)):
                sign_urls(v)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            if isinstance(v, str):
                obj[i] = sign_url(v)
            elif isinstance(v, (dict, list)):
                sign_urls(v)
    return obj


def sign_html_links(html: str) -> str:
    """Sign audio links inside a stored report page as it is served."""
    return re.sub(rf'{re.escape(API)}/recordings/\d+/audio(?=["\'])', lambda m: sign_path(m.group(0)), html)
