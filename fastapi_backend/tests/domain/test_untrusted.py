"""Third-party text in prompts (untrusted.py): marked as data, with chat templates' control tokens and our own markers
broken up so a document can't end its turn or close its marker early."""

from __future__ import annotations

import re

import pytest

from app.domain import untrusted
from tests import injections


@pytest.mark.parametrize("token", injections.TOKENS)
def test_control_tokens_and_markers_are_broken_up(token):
    assert token not in untrusted.neutral(f"before {token} after")


@pytest.mark.parametrize("text", injections.ALL)
def test_no_injection_keeps_a_control_token(text):
    out = untrusted.neutral(text)
    assert not any(t in out for t in injections.TOKENS)
    # the words stay readable: only a space goes in
    assert re.sub(r"\s", "", out) == re.sub(r"\s", "", text)


@pytest.mark.parametrize("text", injections.BENIGN)
def test_ordinary_text_is_left_as_it_is(text):
    assert untrusted.neutral(text) == text


def test_a_marked_piece_can_only_be_closed_by_its_own_code():
    out = untrusted.wrap(injections.FAKE_CLOSE, "archive")
    code = re.match(r'<untrusted-([0-9a-f]{8}) source="archive">\n', out).group(1)
    assert out.endswith(f"\n</untrusted-{code}>")
    assert out.count("</untrusted-") == 1 and out.count("<untrusted-") == 1  # the forged ones are broken up
    assert untrusted.wrap("x", "archive") != untrusted.wrap("x", "archive")  # a new code each time


def test_the_source_is_a_label_not_markup():
    out = untrusted.wrap("x", 'tool "a"> <b')
    assert out.startswith("<untrusted-") and 'source="tool a b">' in out


def test_marking_follows_the_setting():
    on, off = {"ai": {}}, {"ai": {"untrusted_marking": False}}
    assert untrusted.wrap("hi <|im_end|>", "page", on).startswith("<untrusted-")
    assert untrusted.wrap("hi", "page", off) == "hi"
    assert untrusted.rules(on) == untrusted.RULES and untrusted.rules(off) == ""
