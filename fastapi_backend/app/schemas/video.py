"""Video: text on screen and people on screen (faces, with per-namespace consent)."""

from __future__ import annotations

from typing import Any

from pydantic import Field

from app.schemas.common import Ok, RequestModel, ResponseModel


class OcrFix(RequestModel):
    text: str = ""


class NamespaceFaces(ResponseModel):
    mode: str = Field(description="off, detect or recognize")
    pixelate: bool = Field(False, description="faces are pixelated in the pictures visitors see")
    purpose: str | None = None
    set_by: str | None = None
    set_at: str | None = None
    faces: list[dict[str, Any]]
    merges: list[dict[str, Any]]


class FacesMode(RequestModel):
    mode: str | None = Field(None, description="off, detect or recognize; left out, the mode stays as it is")
    pixelate: bool | None = Field(
        None,
        description="pixelate the faces found in the pictures visitors see (public pages, embeds, share links, IIIF); "
        "members see them as they are. Needs faces detected",
    )
    purpose: str | None = Field(None, description="why faces are recognised (required for recognize)")
    reprocess: bool = Field(False, description="queue face detection for this namespace's videos, documents and images")


class FacesModeSet(Ok):
    jobs: list[int] = []


class FaceRename(RequestModel):
    name: str = ""


class FaceName(RequestModel):
    name: str = Field(description="who it is; a name another face of the namespace has joins it to that person")


class FaceNamed(Ok):
    name: str
    into: int | None = Field(None, description="the face of the same name it joined, if any")
    merge: int | None = Field(None, description="that merge, to undo with POST /faces/merges/{merge}/undo")


class FaceThumb(ResponseModel):
    recording: int
    cover_url: str = Field(description="a signed link to the crop")


class FaceAsset(ResponseModel):
    id: int
    title: str | None = None


class UnnamedFace(ResponseModel):
    id: int
    label: str = Field(description="what it's called until it's named, e.g. Face 3")
    guess: str | None = Field(None, description="a name to offer: the voice it's linked to, else one suggested for it")
    speaker: int | None = Field(None, description="the voice it's linked to")
    appearances: int
    screen_ms: int = Field(description="time on screen in videos")
    pages: int = Field(description="pages of documents and images it's on")
    thumbnails: list[FaceThumb]
    recordings: list[FaceAsset] = Field(description="the assets it's in, where it's seen longest first")


class UnnamedFaces(ResponseModel):
    mode: str = Field(description="off, detect or recognize; faces are grouped across the namespace only in recognize")
    total: int
    groups: list[UnnamedFace] = Field(description="the largest group first")


class FaceMerge(RequestModel):
    into: int = 0


class FaceMerged(Ok):
    merge: int


class FaceSpeaker(RequestModel):
    speaker: int | None = None


class FaceDismiss(RequestModel):
    kind: str = "face"
    id: int = 0
