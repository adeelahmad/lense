"""Outgoing email (password reset). Without MAIL_SERVER configured, links are logged instead of sent."""

from __future__ import annotations

import logging
import urllib.parse
from pathlib import Path

from app.config import settings

log = logging.getLogger("lens.email")


async def send_reset_password_email(email: str, name: str | None, token: str) -> None:
    link = f"{settings.FRONTEND_URL}/password-recovery/confirm?{urllib.parse.urlencode({'token': token})}"
    if not settings.mail_enabled:
        log.warning("MAIL_SERVER is not set; password reset link for %s: %s", email, link)
        return
    from fastapi_mail import ConnectionConfig, FastMail, MessageSchema, MessageType

    conf = ConnectionConfig(
        MAIL_USERNAME=settings.MAIL_USERNAME or "",
        MAIL_PASSWORD=settings.MAIL_PASSWORD or "",
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
    message = MessageSchema(
        subject="Reset your Lens password",
        recipients=[email],
        template_body={"username": name or email, "link": link},
        subtype=MessageType.html,
    )
    await FastMail(conf).send_message(message, template_name="password_reset.html")
