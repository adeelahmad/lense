"""Outgoing email (password reset, access requests). Without MAIL_SERVER configured, links are logged instead."""

from __future__ import annotations

import logging
import urllib.parse
from pathlib import Path
from typing import Any

from app.config import settings

log = logging.getLogger("lens.email")


def _mailer() -> Any:
    from fastapi_mail import ConnectionConfig, FastMail
    from pydantic import SecretStr

    return FastMail(
        ConnectionConfig(
            MAIL_USERNAME=settings.MAIL_USERNAME or "",
            MAIL_PASSWORD=SecretStr(settings.MAIL_PASSWORD or ""),
            MAIL_FROM=settings.MAIL_FROM or "",
            MAIL_PORT=settings.MAIL_PORT or 25,
            MAIL_SERVER=settings.MAIL_SERVER or "",
            MAIL_FROM_NAME=settings.MAIL_FROM_NAME,
            MAIL_STARTTLS=settings.MAIL_STARTTLS,
            MAIL_SSL_TLS=settings.MAIL_SSL_TLS,
            USE_CREDENTIALS=settings.USE_CREDENTIALS,
            VALIDATE_CERTS=settings.VALIDATE_CERTS,
            TEMPLATE_FOLDER=Path(__file__).parent / "email_templates",
        )
    )


async def send_reset_password_email(email: str, name: str | None, token: str) -> None:
    link = f"{settings.FRONTEND_URL}/password-recovery/confirm?{urllib.parse.urlencode({'token': token})}"
    if not settings.mail_enabled:
        log.warning("MAIL_SERVER is not set; password reset link for %s: %s", email, link)
        return
    from fastapi_mail import MessageSchema, MessageType
    from pydantic import NameEmail

    message = MessageSchema(
        subject="Reset your Lens password",
        recipients=[NameEmail(name or email, email)],
        template_body={"username": name or email, "link": link},
        subtype=MessageType.html,
    )
    await _mailer().send_message(message, template_name="password_reset.html")


async def send_access_request_email(
    owners: list[str], who: str, rid: int, title: str | None, namespace: str | None, message: str | None
) -> None:
    """Tell a namespace's owners that someone asked for access to one of its recordings."""
    link = f"{settings.FRONTEND_URL}/resources/{rid}#access"
    if not owners:
        return
    if not settings.mail_enabled:
        log.warning("MAIL_SERVER is not set; %s asked for access to recording %s: %s", who, rid, link)
        return
    from fastapi_mail import MessageSchema, MessageType
    from pydantic import NameEmail

    message_ = MessageSchema(
        subject=f"{who} asked for access to {title or f'recording {rid}'}",
        recipients=[NameEmail(o, o) for o in owners],
        template_body={"who": who, "title": title or f"Recording {rid}", "namespace": namespace, "message": message, "link": link},
        subtype=MessageType.html,
    )
    await _mailer().send_message(message_, template_name="access_request.html")
