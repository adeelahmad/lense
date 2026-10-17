"""Classifying an asset or a text (domain/classify.py)."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from app.schemas.common import RequestModel, ResponseModel


class ClassifyRequest(RequestModel):
    """Give a `recording` or a `text`, and `labels` or a `vocabulary`."""

    recording: int | None = Field(None, description="the asset to classify")
    text: str | None = Field(None, max_length=20000, description="text to classify instead")
    labels: dict[str, str] | list[str] | None = Field(
        None, description="the labels to choose from (2 to 60): names, or {name: what it means}"
    )
    vocabulary: Literal["content_type", "topic"] | None = Field(
        None, description="choose from Lens's content types (of the asset's kind), or the namespace's topics"
    )
    namespace: str | None = Field(None, description="whose topics, when classifying a text by topic")
    question: str | None = Field(None, max_length=500, description="what to ask (default: which label fits best)")
    apply: bool = Field(
        False,
        description="act on a sure answer: set the content type, suggest the topic, or add the label as a tag (editors)",
    )


class RankedLabel(ResponseModel):
    label: str
    p: float = Field(description="how likely, from 0 to 1")
    key: str | None = Field(None, description="content_type: the content type's key")
    id: int | None = Field(None, description="topic: the topic's id")


class ClassifyResult(ResponseModel):
    choice: str = Field(description="the label that fits best")
    confidence: float = Field(description="how sure, from 0 to 1")
    sure: bool = Field(description="sure enough to act on without asking (decisions.act_above)")
    ranked: list[RankedLabel] = Field(description="every label, the likeliest first")
    by: str = Field(description="what answered: jev, laya, llm, or only option")
    vocabulary: str | None = None
    question: str
    applied: Literal["content_type", "topic_suggested", "tag"] | None = Field(
        None, description="what was done with a sure answer, when apply was asked"
    )
    key: str | None = Field(None, description="content_type: the chosen content type's key")
    id: int | None = Field(None, description="topic: the chosen topic's id")
