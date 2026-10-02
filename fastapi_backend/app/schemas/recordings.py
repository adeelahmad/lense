from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from app.schemas.common import AccessLevel, AccessPart, Ok, RequestModel, ResponseModel, Role
from app.schemas.hierarchy import CollectionStep

# Recording statuses, plus two job states: a job queued or running (processing), the latest job failed (failed).
RecordingState = Literal["new", "transcribed", "diarized", "analyzed", "error", "processing", "failed"]
MediaKind = Literal["audio", "video", "transcript", "document", "image"]
RecordingSort = Literal[
    "date",
    "-date",
    "title",
    "-title",
    "duration",
    "-duration",
    "speakers",
    "-speakers",
    "status",
    "-status",
    "importance",
    "-importance",
]


class RecordingSummary(ResponseModel):
    id: int
    title: str | None = None
    recorded_at: str | None = None
    duration_ms: int | None = None
    status: str | None = None
    error: str | None = None
    source: str | None = None
    space: int
    namespace: str | None = None
    collection: int | None = Field(None, description="the collection it lives in")
    collection_name: str | None = None
    media_kind: str = Field(description="audio, video, transcript (text without media), document or image")
    pages: int | None = Field(None, description="a document's or an image's pages")
    poster: str | None = Field(None, description="signed link to a video's first frame, or a document's or image's first page")
    emotions: dict[str, Any] = Field(default_factory=dict)
    words: int | None = None
    importance: Any = None
    sentiment: Any = None
    speakers: str = Field("", description="speaker names, comma separated")
    access: AccessLevel = Field("private", description="its own access, or its namespace's default")
    open: list[AccessPart] = Field(default_factory=list, description="the parts anyone may use when it is public")
    featured: bool = False
    tags: list[str] = Field(default_factory=list)
    language: str | None = Field(None, description="its language code, when known")
    origin: str | None = Field(
        None, description="where it came from: source:<id> (a connected source), upload, paste, iiif, folder or file"
    )
    origin_name: str | None = Field(None, description="what to call its origin: the source's name, or e.g. Uploaded")
    role: Role | None = Field(
        None, description="your role on it: through its namespace or its collection (an admin of the collection is an owner)"
    )


class OriginCount(ResponseModel):
    origin: str = Field(description="source:<id>, upload, paste, iiif, folder (the archive's own folders) or file")
    name: str
    recordings: int


class LanguageCount(ResponseModel):
    language: str | None = Field(None, description="a language code; null: not known")
    recordings: int


class ObjectCount(ResponseModel):
    object: str = Field(description="a kind of object (person, car, dog …)")
    recordings: int


class ObjectTrack(ResponseModel):
    """A kind of object seen in a recording, and where."""

    label: str = Field(description="what it is (person, car, dog …: the COCO dataset's kinds, with the default engine)")
    spans: list[list[int]] = Field(description="[from, to) where it's seen: ms, or page numbers from 0 when `paged`")
    screen_ms: int = Field(description="how long it's seen (ms), or on how many pages when `paged`")
    first_ms: int = Field(description="where it's first seen: ms, or a page number when `paged`")
    count: int = Field(description="how many times it was found (on how many sampled frames or pages, and side by side)")
    score: float = Field(description="how sure the detector was, on average (0-1)")
    frame: str | None = Field(None, description="signed link to the frame or page it's best seen on")
    box: list[float] | None = Field(None, description="where it is on that frame or page: [x, y, w, h] as fractions")
    boxes: list[list[float]] = Field(
        default_factory=list, description="each place it was found: [t (ms, or page), x, y, w, h, score] (at most 500)"
    )
    paged: bool | None = Field(None, description="true for a document's or an image's pages")
    engine: str | None = Field(None, description="the detector: yolox or ultralytics")


class Description(ResponseModel):
    """What a model that can see images said a page or a shot shows."""

    idx: int = Field(description="the page (from 0) or the shot it describes")
    t0: int = Field(description="where it starts: ms, or the page (from 0) when `paged`")
    t1: int = Field(description="where it ends (not included): ms, or the page after it when `paged`")
    text: str = Field(description="the description: a few sentences")
    model: str = Field(description="the model that wrote it (llm.vision_model)")
    frame: str | None = Field(None, description="signed link to the page's drawing or the shot's keyframe")
    paged: bool | None = Field(None, description="true for a document's or an image's pages")


class RecordingSpeaker(ResponseModel):
    id: int
    name: str
    method: str | None = None
    score: float | None = None


class EmailInfo(ResponseModel):
    """An email's own description, read when it was made into a PDF."""

    subject: str | None = None
    from_: str | None = Field(None, alias="from", description="who sent it")
    to: str | None = None
    cc: str | None = None
    date: str | None = Field(None, description="when it was sent (ISO 8601)")


class Rendition(ResponseModel):
    """How a document that isn't a PDF was made into one."""

    from_: str = Field(alias="from", description="its own type: .docx, .eml, …")
    by: Literal["libreoffice", "chromium"]


class WebPage(ResponseModel):
    """A web page captured as a document: where it was, and when."""

    url: str = Field(description="the address given")
    final: str | None = Field(None, description="the address it ended at, after redirects")
    captured_at: str | None = Field(None, description="when it was captured; none until its pipeline has run")
    how: Literal["printed", "pdf"] | None = Field(None, description="printed by Chromium, or a PDF kept as it was")


class AttachedTo(ResponseModel):
    """The email a resource was attached to (it's one of that email's files too)."""

    resource: int
    file: int | None = None
    title: str | None = None


class Suggestion(ResponseModel):
    """Something a decision model thinks fits the resource but wasn't sure enough to apply (docs/processing.md#classify)."""

    id: str
    kind: Literal["tag", "content_type", "collection"]
    value: str | int = Field(description="the tag, the content type's key, or the collection's id")
    label: str
    p: float = Field(description="how sure the model was, 0 to 1")


class Recording(ResponseModel):
    """The recording row (less its envelope) plus what the recording page needs."""

    id: int
    title: str | None = None
    status: str | None = None
    space: int
    namespace: str | None = None
    collection: int | None = Field(None, description="the collection it lives in")
    collection_path: list[CollectionStep] = Field(default_factory=list, description="its collection and the ones it's in, from the top")
    role: Role | None = Field(
        None, description="your role on it: through its namespace or its collection (an admin of the collection is an owner)"
    )
    summary: dict[str, Any] | None = None
    stats: dict[str, Any] | None = None
    report_url: str | None = Field(None, description="signed link to the built report page, if one exists")
    speakers: list[RecordingSpeaker] = Field(default_factory=list)
    jobs: list[dict[str, Any]] = Field(default_factory=list)
    access: AccessLevel = Field("private", description="its own access, or its namespace's default")
    open: list[AccessPart] = Field(default_factory=list, description="the parts anyone may use when it is public")
    featured: bool = False
    access_inherited: bool = Field(True, description="the access comes from the namespace's default")
    tags: list[str] = Field(default_factory=list)
    suggestions: list[Suggestion] = Field(default_factory=list, description="waiting for an editor to accept or dismiss; editors only")
    email: EmailInfo | None = Field(None, description="an email: its subject, sender, recipients and date")
    rendition: Rendition | None = Field(None, description="a document that isn't a PDF: how the PDF it's read from was made")
    attached_to: AttachedTo | None = Field(None, description="an email's attachment made a resource of its own: that email")
    web: WebPage | None = Field(None, description="a web page captured as a document")


class Page(ResponseModel):
    """A page of a document, or an image (a TIFF has one per frame)."""

    idx: int = Field(description="from 0")
    width: int | None = Field(None, description="pixels of its image")
    height: int | None = None
    image: str | None = Field(None, description="a signed link to it, drawn; none when it couldn't be")
    thumb: str | None = Field(None, description="a signed link to it, small")
    text: Literal["pdf", "ocr"] | None = Field(None, description="how its text was read: from the PDF, or by OCR; none without text")
    chars: int = Field(0, description="characters of text on it")
    label: str | None = Field(None, description="the PDF's own name for it (iv, A-1, …) when it isn't its number")


class Player(ResponseModel):
    """Everything the player shows: transcript, speakers, sections, entities and, for videos, shots and faces; for
    documents and images, their pages."""

    id: int
    title: str | None = None
    namespace: str | None = None
    recorded_at: str | None = None
    duration_ms: int | None = None
    audio: str | None = Field(None, description="signed audio link, or null when there is no audio")
    speakers: list[dict[str, Any]] = Field(default_factory=list)
    segments: list[dict[str, Any]] = Field(
        default_factory=list,
        description="the lines: t0, t1 (ms), s (speaker key), text, e (emotion), v (event), and w, the timed words as "
        "[c0, c1, t0, t1] (a character range of text, ms) when transcription gave them; a document's or an image's "
        "blocks of text have p, their page (from 0), and b, where they are on it ([x, y, w, h] as fractions of the "
        "page), and times that are only a reading pace",
    )
    sections: list[dict[str, Any]] = Field(default_factory=list)
    entities: list[dict[str, Any]] = Field(default_factory=list)
    keywords: list[Any] = Field(default_factory=list)
    envelope: list[Any] | None = None
    summary: dict[str, Any] | None = None
    media: dict[str, Any] | None = Field(
        None, description="kind: audio, video, document or image (audio for a transcript without media), and its size"
    )
    pages: list[Page] | None = Field(None, description="a document's or an image's pages, in order")
    objects: list[ObjectTrack] = Field(
        default_factory=list, description="videos, documents and images: the kinds of object seen in it, the most seen first"
    )
    descriptions: list[Description] = Field(
        default_factory=list, description="videos, documents and images: what each shot or page shows, by a model that can see"
    )
    faces_mode: str = Field("off", description="the namespace's face mode: off, detect or recognize")
    faces_pixelate: bool = Field(False, description="the namespace pixelates faces in the pictures visitors see")


class RecordingUpdate(RequestModel):
    """The fields to change; the others stay as they are."""

    title: str | None = Field(None, min_length=1, max_length=200, description="whitespace is collapsed")
    tags: list[str] | None = Field(None, max_length=20, description="replace its tags (at most 20, 40 characters each)")


class RecordingsRetag(RequestModel):
    recordings: list[int] = Field(min_length=1, max_length=1000)
    add: list[str] = Field(default_factory=list, max_length=20)
    remove: list[str] = Field(default_factory=list, max_length=100)


class TagsChanged(Ok):
    changed: int = Field(description="how many recordings' tags changed")


class TagCount(ResponseModel):
    tag: str
    recordings: int


class NamespaceAccess(ResponseModel):
    access: AccessLevel
    open: list[AccessPart]


class RecordingAccess(ResponseModel):
    """Who may see the recording: public, restricted or private (docs/access.md)."""

    access: AccessLevel
    open: list[AccessPart] = Field(description="the parts anyone may use when it is public: media, transcript, index")
    featured: bool
    inherited: bool = Field(description="access and open come from the namespace's default")
    default: NamespaceAccess = Field(description="the namespace's default")


class RecordingAccessUpdate(RequestModel):
    """Send the settings to change. access or open set to null follow the namespace's default again."""

    access: AccessLevel | None = None
    open: list[AccessPart] | None = None
    featured: bool | None = None


class Permission(ResponseModel):
    """Someone given permission on a recording (docs/access.md): they see all of it on the pages visitors see and in
    IIIF, whatever its access."""

    account: int
    email: str
    name: str | None = None
    by: str | None = Field(None, description="who gave it")
    at: str | None = None


class AccessRequest(ResponseModel):
    """Someone asking for permission on a recording (docs/access.md)."""

    recording: int
    title: str | None = None
    namespace: str | None = None
    account: int
    email: str
    name: str | None = None
    message: str | None = None
    at: str | None = None
    status: Literal["pending", "approved", "declined"]
    decided_by: str | None = None
    decided_at: str | None = None


class RecordingIpGroup(ResponseModel):
    """One of the namespace's IP groups, and whether it opens this recording (docs/access.md)."""

    id: int
    name: str
    ranges: list[str]
    everything: bool = Field(description="it opens every recording in the namespace")
    opens: bool = Field(description="visitors from its addresses see all of this recording")


class RecordingMove(RequestModel):
    namespace: str = Field(min_length=1, description="the namespace to move it to")
    rediarize: bool = Field(
        False, description="identify its speakers again from their voices in the new namespace (audio only); else matched by name"
    )
    revoke_shares: bool = Field(False, description="stop its share links working; otherwise they keep working")
    collection: int | None = Field(None, description="a collection of the new namespace to put it in; default: its default collection")


class RecordingMoved(Ok):
    namespace: str
    collection: int | None = Field(None, description="the collection it's in now")
    job: int = Field(description="the job that analyses it again in the new namespace")
    pinned: list[str] = Field(
        default_factory=list, description="what it had from its old namespace and keeps: access, open parts, metadata defaults"
    )
    shares_revoked: int = 0


class PermissionAdd(RequestModel):
    email: str = Field(min_length=3, max_length=320, description="the address of an account in this archive")


class ReprocessRequest(RequestModel):
    steps: list[str | dict[str, Any]] | None = Field(None, description="default: the namespace's pipeline")
    pipeline: int | None = None


class JobQueued(Ok):
    job: int


class ShareCreate(RequestModel):
    days: int = Field(30, ge=1, le=3650)


class ShareLink(ResponseModel):
    id: str = Field(description="the link's id, as GET /shares lists it")
    token: str
    embed: str = Field(description="embeddable player link carrying the share token (works without signing in)")
    short: str | None = Field(None, description='"/s/<code>": a short address for the same player; it expires and is revoked with the link')


class ShareSite(ResponseModel):
    origin: str = Field(description="a site whose pages framed the player (scheme and host, as browsers report it)")
    opens: int = Field(0, description="how many times the player was opened there")
    last_at: str | None = None


class Share(ResponseModel):
    id: str = Field(description="first characters of the link's id")
    created_by: str | None = None
    created_at: str | None = None
    expires_at: str | None = None
    active: bool = Field(description="it still works: not revoked and not expired")
    revoked: bool = False
    revoked_by: str | None = None
    revoked_at: str | None = None
    short: bool = Field(False, description="it has a short /s/ address (links made before short links have none)")
    plays: int = Field(0, description="times its player started playing, once per page load (not counting Lens itself)")
    played_at: str | None = Field(None, description="the last play")
    embedded_on: list[ShareSite] = Field(
        default_factory=list, description="sites whose pages framed its player, most recent first (up to 50)"
    )


class SegmentUpdate(RequestModel):
    """Send ``text``, ``speaker`` (a speaker id in the same namespace, or null), or both."""

    text: str | None = None
    speaker: int | None = None


class RecordingsPlace(RequestModel):
    recordings: list[int] = Field(min_length=1, max_length=1000)
    collection: int = Field(description="a collection of their namespace")


class Placed(Ok):
    moved: int = Field(description="how many weren't in it already")


class SegmentSplit(RequestModel):
    """Where to split a line: `at`, a position in its text (the split goes at the start of the word it's in)."""

    at: int = Field(ge=1, description="a character position in the line's text")
    t: int | None = Field(None, ge=0, description="when the second part starts, in ms (default: when its first word was said)")
    speaker: int | None = Field(
        None, description="the second part's speaker (an id in the namespace, or null); leave out to keep the line's"
    )


class SegmentEdit(ResponseModel):
    idx: int
    kind: Literal["split", "merge"] | None = Field(None, description="split or merge; null for a correction")
    before: dict[str, Any] | None = None
    after: dict[str, Any] | None = None
    by: str | None = None
    at: str | None = None


class Output(ResponseModel):
    key: str
    value: Any = None
    origin: Any = None
    created_at: str | None = None


class EmbedLink(ResponseModel):
    url: str = Field(description="signed link to the embeddable player")
