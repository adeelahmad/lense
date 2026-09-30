"""Video: text on screen and people on screen (faces, with per-namespace consent)."""

from __future__ import annotations

from typing import Any

from pydantic import Field

from app.schemas.common import Ok, RequestModel, ResponseModel


class OcrFix(RequestModel):
    text: str = ""


class NamespaceFaces(ResponseModel):
    mode: str = Field(description="off, detect or recognize")
    purpose: str | None = None
    set_by: str | None = None
    set_at: str | None = None
    faces: list[dict[str, Any]]
    merges: list[dict[str, Any]]


class FacesMode(RequestModel):
    mode: str | None = Field(None, description="off, detect or recognize")
    purpose: str | None = Field(None, description="why faces are recognised (required for recognize)")
    reprocess: bool = Field(False, description="queue face detection for this namespace's videos")


class FacesModeSet(Ok):
    jobs: list[int] = []


class FaceRename(RequestModel):
    name: str = ""


class FaceMerge(RequestModel):
    into: int = 0


class FaceMerged(Ok):
    merge: int


class FaceSpeaker(RequestModel):
    speaker: int | None = None


class FaceDismiss(RequestModel):
    kind: str = "face"
    id: int = 0
