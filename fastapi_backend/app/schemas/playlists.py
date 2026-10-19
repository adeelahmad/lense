"""Playlists: clips (moments of resources, with a title and a note) in order, to play back to back. Private unless
shared; a reader gets only the clips of resources they can read."""

from __future__ import annotations

from pydantic import Field

from app.schemas.common import RequestModel, ResponseModel


class PlaylistCreate(RequestModel):
    name: str = Field(min_length=1, max_length=120, description="unique among your playlists")
    description: str | None = Field(None, max_length=2000)
    shared: bool = Field(False, description="everyone signed in reads it, each seeing only the clips of resources they can read")


class PlaylistUpdate(RequestModel):
    name: str | None = Field(None, min_length=1, max_length=120)
    description: str | None = Field(None, max_length=2000, description="empty clears it")
    shared: bool | None = None


class Playlist(ResponseModel):
    id: int
    name: str
    description: str | None = None
    shared: bool = False
    mine: bool = Field(description="you made it: only you change it")
    created_by: str | None = Field(None, description="who made it: their email")
    created_by_name: str | None = None
    count: int = Field(description="its clips of resources you can read")
    duration_ms: int = Field(description="how long those clips play, back to back")
    created_at: str | None = None
    updated_at: str | None = None


class ClipCreate(RequestModel):
    recording: int | None = Field(None, description="the resource the clip is of (or give highlight)")
    highlight: int | None = Field(None, description="clip a highlight: its resource, passage and label, unless given here")
    t0: int | None = Field(None, ge=0, description="where the clip starts, in ms from the start (required without highlight)")
    t1: int | None = Field(None, ge=0, description="where it ends, in ms (default: 30 seconds on, or the end of the resource)")
    title: str | None = Field(None, max_length=200, description="what it is, in a few words")
    note: str | None = Field(None, max_length=2000)
    quote: str | None = Field(None, max_length=1000, description="the words picked in the text")
    position: int | None = Field(None, ge=0, description="where it goes, from 0 (default: the end)")


class ClipUpdate(RequestModel):
    title: str | None = Field(None, max_length=200, description="empty clears it")
    note: str | None = Field(None, max_length=2000, description="empty clears it")
    t0: int | None = Field(None, ge=0)
    t1: int | None = Field(None, ge=0)


class PlaylistOrder(RequestModel):
    items: list[int] = Field(max_length=500, description="clip ids in their new order; clips not named follow, as they were")


class Clip(ResponseModel):
    id: int
    playlist: int
    position: int = Field(description="its place in the playlist, from 0")
    recording: int
    t0: int = Field(description="where it starts, in ms from the start")
    t1: int = Field(description="where it ends, in ms")
    title: str | None = None
    note: str | None = None
    quote: str | None = Field(None, description="the words picked in the text")
    highlight: int | None = Field(None, description="the highlight it was clipped from")
    recording_title: str | None = None
    namespace: str | None = None
    media: str = Field(description="the resource's kind: audio, video, document, image or transcript")
    audio: str | None = Field(None, description="a signed link to the resource's audio or video, or null when it has none")
    link: str = Field(description="the resource's page in the web app at the clip's start")
    created_at: str | None = None


class PlaylistDetail(Playlist):
    items: list[Clip] = Field(description="its clips of resources you can read, in order, each with what a player needs")
    hidden: int = Field(0, description="clips of resources you can't read, left out")
