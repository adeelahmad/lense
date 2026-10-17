"""Finding resources by any attribute (domain/find.py)."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from app.schemas.common import RequestModel, ResponseModel
from app.schemas.recordings import MediaKind, RecordingState, RecordingSummary

Named = list[int | str]


class FindQuery(RequestModel):
    """Filters combine with AND, the values of one filter with OR. Faces, speakers, entities and topics are ids or names
    (ignoring case; an entity's other names count, and any of a topic's labels)."""

    namespace: str | None = Field(None, description="one namespace (default: every namespace you can read)")
    text: str | None = Field(
        None,
        max_length=300,
        description='words said or written in them, as search takes them (words, "phrases", OR); by meaning too when that is set up',
    )
    title: str | None = Field(None, max_length=200, description="words that must all appear in the title, the namespace or a person's name")
    faces: Named | None = Field(None, max_length=20, description="people seen in them: face identities, by id or name")
    speakers: Named | None = Field(None, max_length=20, description="people heard in them: speakers, by id or name")
    entities: Named | None = Field(None, max_length=20, description="entities they mention, by id or name")
    topics: Named | None = Field(
        None, max_length=20, description="topics they were accepted as about, by id or label; a topic's narrower topics count too"
    )
    types: list[MediaKind] | None = Field(None, max_length=5, description="their kind: audio, video, transcript, document or image")
    content_types: list[str] | None = Field(
        None, max_length=20, description="content types (podcast, interview, scan, photo, email …, GET /content-types), by key or label"
    )
    date_from: str | None = Field(None, description="made on or after this day, month or year (2026-09-30, 2026-09, 2026)")
    date_to: str | None = Field(None, description="made on or before this day, month or year")
    min_duration: float | None = Field(None, ge=0, description="at least this many seconds long")
    max_duration: float | None = Field(None, ge=0, description="shorter than this many seconds")
    tags: list[str] | None = Field(None, max_length=20, description="tags (ignoring case)")
    languages: list[str] | None = Field(None, max_length=20, description="language codes; none for those whose language isn't known")
    objects: list[str] | None = Field(None, max_length=20, description="kinds of object seen in them (person, car, dog …)")
    emotions: list[str] | None = Field(None, max_length=20, description="emotions heard in them (ignoring case)")
    status: list[RecordingState] | None = Field(None, max_length=10, description="statuses, or processing and failed")
    origins: list[str] | None = Field(
        None, max_length=20, description="where they came from: source:<id>, upload, paste, iiif, folder or file"
    )
    collection: int | None = Field(None, description="a collection: what's in it and in the collections inside it")
    sensitive: Literal["include", "only", "exclude"] | None = Field(
        None, description="where NSFW classification is on: only those flagged, exclude them, or include them"
    )
    sort: Literal["relevance", "-date", "date", "-title", "title", "-duration", "duration"] | None = Field(
        None, description="relevance (the default with text: best matching words first), else newest first"
    )
    limit: int = Field(20, ge=1, le=100)
    offset: int = Field(0, ge=0, le=10000)


class FindMoment(ResponseModel):
    t0: int | None = Field(None, description="ms from the start, on a timeline")
    t1: int | None = Field(None, description="ms, where it ends")
    page: int | None = Field(None, description="in a document or an image: the page (from 0)")
    last_page: int | None = Field(None, description="a face over several pages: the last of them")
    text: str | None = Field(None, description="the words there, when it has any")
    speaker: str | None = None
    source: str | None = Field(None, description="words: said, screen, file, page, object or described (as search has it)")


class FindReason(ResponseModel):
    by: Literal["words", "face", "voice", "entity", "topic", "content_type", "object", "emotion"]
    name: str | None = Field(None, description="what matched: the words, the person's or the entity's name, the topic's label …")
    moments: list[FindMoment] = Field(default_factory=list, description="up to 3 moments that show it, to cite")
    count: int | None = Field(None, description="emotion: how many lines have it")


class FindItem(RecordingSummary):
    why: list[FindReason] = Field(default_factory=list, description="why it matched")


class FindNamed(ResponseModel):
    id: int | None = None
    key: str | None = None
    name: str | None = None
    namespace: str | None = None


class FindText(ResponseModel):
    mode: str = Field(description="how the words were matched: keyword, semantic or hybrid")
    meaning: str | None = Field(None, description="why not by meaning, when it was asked and couldn't be")
    moments: int = Field(description="moments the words were found in")
    capped: bool = Field(description="more moments matched than were looked through, so some assets may be missing")


class FindResults(ResponseModel):
    total: int = Field(description="how many assets match, on all pages")
    items: list[FindItem]
    resolved: dict[str, list[FindNamed] | int] = Field(
        default_factory=dict, description="what the names given matched, by filter (and how many narrower topics came with them)"
    )
    text: FindText | None = None
