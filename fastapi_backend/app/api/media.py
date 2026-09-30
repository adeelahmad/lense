"""Signing media links in API responses and in the pages the API serves.

Audio, video, frames and word clouds are fetched by <audio>, <video> and <img> tags, which can't send the
Authorization header. The domain layer builds plain links (``/api/v1/recordings/12/audio``); before a response leaves,
``sign_urls`` turns every such link into a signed one that works on its own until it expires. Only responses already
limited to what the caller may read pass through here, so a signed link never grants more than the caller had.

Only links the server wrote are signed: the fields that hold links in API responses (LINK_KEYS), and in a page, links
to the recordings the page is about. Titles, transcript lines, metadata and chat messages are written by people (or
models) and can be made to look like a link to any recording; signing them would open recordings their author can't
read.
"""

from __future__ import annotations

import re
import urllib.parse
from collections.abc import Container
from typing import Any, cast

from app.core.security import sign_path
from app.domain.store import API

MEDIA_RX = re.compile(rf"^{re.escape(API)}/recordings/\d+/(audio|media|wordcloud\.svg|frames/[\w.-]+)$")
NS_MEDIA_RX = re.compile(rf"^{re.escape(API)}/namespaces/[a-z0-9][a-z0-9_-]*/wordcloud\.svg$")
REPORT_RX = re.compile(r"^/(reports|embed)/")
# the response fields the server fills with links (player audio, frames, face covers, posters, reports, word clouds)
LINK_KEYS = frozenset({"audio", "cover", "cover_url", "frame", "poster", "report_url", "wordcloud"})
# a media link in a page, in an attribute or inside embedded JSON (followed by a quote, or a backslash in JSON)
MEDIA_IN_HTML = re.compile(rf"{re.escape(API)}/recordings/(\d+)/(?:audio|media|wordcloud\.svg|frames/[\w.-]+)(?=[\"'\\])")


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
    """Sign the media links in a JSON-like structure (in place for dicts and lists; returns it): the values of the
    LINK_KEYS fields, at any depth. Other strings are left alone, however much they look like a link."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(v, str):
                if k in LINK_KEYS:
                    obj[k] = sign_url(v)
            elif isinstance(v, (dict, list)):
                sign_urls(v)
    elif isinstance(obj, list):
        for v in obj:
            if isinstance(v, (dict, list)):
                sign_urls(v)
    return obj


def sign_page_links(page: str, recordings: Container[int]) -> str:
    """Sign the media links (audio, video, frames, word clouds) of these recordings in a page, including inside its
    embedded JSON. Links to any other recording stay unsigned: the page's text may contain them."""
    return MEDIA_IN_HTML.sub(lambda m: sign_path(m.group(0)) if int(m.group(1)) in recordings else m.group(0), page)
