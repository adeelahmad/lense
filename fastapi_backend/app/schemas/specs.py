"""Specs from transcripts (app/domain/specs.py)."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from app.schemas.common import RequestModel, ResponseModel


class SpecWrite(RequestModel):
    template: int | None = Field(None, description="another prompt template to write it with (default: Spec from transcript)")
    version: int | None = Field(None, ge=1, description="that template's version (default: its current one)")


class Spec(ResponseModel):
    status: Literal["none", "writing", "ready", "failed"] = Field(
        description="ready: written (note); writing: a job is on it (job); failed: the last job to write it failed (error)"
    )
    note: int | None = Field(None, description="the note it was written to (/notes/{id})")
    title: str | None = None
    job: int | None = Field(None, description="the latest job that was to write it")
    error: str | None = None
    written_at: str | None = None
    template: int = Field(description="the prompt template it was written with, else the one it would be")
    version: int | None = None
    model: str | None = None
    decisions: int | None = None
    rejected: int | None = Field(None, description="each no, and each 'not that, this'")
    constraints: int | None = None
    gaps: int | None = Field(None, description="what the speaker left to decide later")
    not_found: int | None = Field(None, description="quotes that aren't in the transcript word for word (marked so in the note)")
    cut: bool | None = Field(None, description="the transcript was longer than the model is given (llm.max_chars)")
