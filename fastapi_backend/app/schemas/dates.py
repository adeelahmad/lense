from __future__ import annotations

from pydantic import Field

from app.schemas.common import RequestModel, ResponseModel


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
    proposable: bool = Field(
        False,
        description="whether you can propose a date as an event (POST /dates/propose): an admin, with a calendar connection that may add events",
    )


class EventProposal(RequestModel):
    """A date said, to propose as an event: each field left out is prefilled from the line."""

    recording_id: int
    segment: int = Field(description="the line's id, as GET /dates gives it")
    index: int = Field(0, ge=0, le=50, description="which of the line's dates, the earliest first, when it gives more than one")
    title: str | None = Field(None, max_length=300, description="else the line itself, shortened")
    start: str | None = Field(
        None,
        max_length=40,
        description="ISO 8601: a date alone is all day; a time without an offset is in the calendar's time zone. Else the day said, at the time said in the line",
    )
    end: str | None = Field(None, max_length=40, description="else an hour after start, or a day for a day")
    where: str | None = Field(None, max_length=500)
    description: str | None = Field(None, max_length=8000, description="else where it was said, with a link to that moment")
    attendees: list[str] | None = Field(
        None, max_length=50, description="addresses; else those in the line and, for an email, its sender and recipients ([] for nobody)"
    )
    invite: bool = Field(False, description="email them an invitation when it's added")
    connection: str | None = Field(None, max_length=200, description="the calendar connection, by id or name (else the only one)")
    calendar: str | None = Field(None, max_length=300, description="one of its calendars, by id or name (else its first)")


class EventProposed(ResponseModel):
    approval: int = Field(description="the approval waiting for your yes: nothing is added until you approve it")
    status: str = Field(description="waiting")
    summary: str | None = None
    preview: str | None = Field(None, description="the event as the approval shows it")
    again: bool = Field(False, description="this date was proposed before and still waits: the same approval")
    url: str | None = Field(None, description="where you approve it in the web app")
