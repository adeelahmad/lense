from __future__ import annotations

from pydantic import Field

from app.schemas.common import ResponseModel


class DateSaid(ResponseModel):
    """A date an asset's line gives, as days on the calendar."""

    day: str = Field(description="the first day it means, YYYY-MM-DD")
    until: str = Field(description="the last day it means: the same day for a day, the Sunday for a week, the 31st for a month")
    period: str = Field(description="day, weekend, week, month or year")
    said: str = Field(description='the words that give it, like "next Tuesday" or "3 March 2024"')
    relative: bool = Field(description="worked out from the asset's own date (recorded_at): a day said relative to it, or without its year")
    recording_id: int
    title: str | None = None
    namespace: str | None = None
    recorded_at: str | None = None
    segment: int = Field(description="the line's id")
    idx: int = Field(description="the line's place in the asset's text")
    t0: int | None = Field(None, description="ms")
    time: str | None = None
    speaker: str | None = None
    text: str | None = Field(None, description="the line")


class DateList(ResponseModel):
    total: int
    items: list[DateSaid]
    reading: bool = Field(description="whether analysis reads dates (Settings → Analysis → Read dates); off, nothing new is read")
