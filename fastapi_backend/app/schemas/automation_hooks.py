"""An automation's webhook (docs/automations.md, Webhook trigger): what a call to it answers."""

from __future__ import annotations

from pydantic import Field

from app.schemas.common import ResponseModel


class HookCalled(ResponseModel):
    id: str = Field(description="the webhook.received activity the call made (the same one again for a retried call)")
