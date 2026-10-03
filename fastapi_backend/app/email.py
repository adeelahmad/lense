"""Outgoing email (password reset, sign-in links, access requests), sent through the SMTP server in Settings → Email (or MAIL_* in
.env, which win and show locked). Without one, links are logged instead."""

from __future__ import annotations

import logging
import os
import urllib.parse
from pathlib import Path
from typing import Any

from app.config import settings

log = logging.getLogger("lens.email")


def mail_enabled(cfg: dict[str, Any]) -> bool:
    m = cfg.get("mail") or {}
    return bool(m.get("server") and m.get("from_address"))


def app_url(cfg: dict[str, Any]) -> str:
    """Where people open the web app, for links in emails: notifications.app_url when set, else FRONTEND_URL."""
    return str((cfg.get("notifications") or {}).get("app_url") or settings.FRONTEND_URL).rstrip("/")


def _mailer(cfg: dict[str, Any]) -> Any:
    from fastapi_mail import ConnectionConfig, FastMail
    from pydantic import SecretStr

    m = cfg["mail"]
    security = m.get("security") or "starttls"
    if os.environ.get("MAIL_SERVER"):  # set up in .env: its MAIL_STARTTLS and MAIL_SSL_TLS say how to connect
        security = "ssl" if settings.MAIL_SSL_TLS else "starttls" if settings.MAIL_STARTTLS else "none"
    return FastMail(
        ConnectionConfig(
            MAIL_USERNAME=m.get("username") or "",
            MAIL_PASSWORD=SecretStr(m.get("password") or ""),
            MAIL_FROM=m["from_address"],
            MAIL_PORT=int(m.get("port") or 587),
            MAIL_SERVER=m["server"],
            MAIL_FROM_NAME=m.get("from_name") or "Lens",
            MAIL_STARTTLS=security == "starttls",
            MAIL_SSL_TLS=security == "ssl",
            USE_CREDENTIALS=bool(m.get("username")),
            VALIDATE_CERTS=settings.VALIDATE_CERTS,
            TEMPLATE_FOLDER=Path(__file__).parent / "email_templates",
        )
    )


async def send_test_email(cfg: dict[str, Any], to: str) -> None:
    """A short message to check the settings; raises with the server's answer when it can't be sent."""
    from fastapi_mail import MessageSchema, MessageType
    from pydantic import NameEmail

    message = MessageSchema(
        subject="Lens can send email",
        recipients=[NameEmail(to, to)],
        body=f"<p>Email from Lens works. Links in emails open {app_url(cfg)}.</p>",
        subtype=MessageType.html,
    )
    await _mailer(cfg).send_message(message)


async def send_reset_password_email(cfg: dict[str, Any], email: str, name: str | None, token: str) -> None:
    link = f"{app_url(cfg)}/password-recovery/confirm?{urllib.parse.urlencode({'token': token})}"
    if not mail_enabled(cfg):
        log.warning("no email server is set up (Settings → Email); password reset link for %s: %s", email, link)
        return
    from fastapi_mail import MessageSchema, MessageType
    from pydantic import NameEmail

    message = MessageSchema(
        subject="Reset your Lens password",
        recipients=[NameEmail(name or email, email)],
        template_body={"username": name or email, "link": link},
        subtype=MessageType.html,
    )
    await _mailer(cfg).send_message(message, template_name="password_reset.html")


async def send_signin_link_email(cfg: dict[str, Any], email: str, name: str | None, link: str, minutes: int) -> None:
    """A link for adding a passkey, for someone who lost theirs (passwords off)."""
    if not mail_enabled(cfg):
        log.warning("no email server is set up (Settings → Email); sign-in link for %s: %s", email, link)
        return
    from fastapi_mail import MessageSchema, MessageType
    from pydantic import NameEmail

    message = MessageSchema(
        subject="Sign in to Lens",
        recipients=[NameEmail(name or email, email)],
        template_body={"username": name or email, "link": link, "minutes": minutes},
        subtype=MessageType.html,
    )
    await _mailer(cfg).send_message(message, template_name="signin_link.html")


async def send_access_request_email(
    cfg: dict[str, Any], owners: list[str], who: str, rid: int, title: str | None, namespace: str | None, message: str | None
) -> None:
    """Tell a namespace's owners that someone asked for access to one of its recordings."""
    link = f"{app_url(cfg)}/resources/{rid}#access"
    if not owners:
        return
    if not mail_enabled(cfg):
        log.warning("no email server is set up (Settings → Email); %s asked for access to recording %s: %s", who, rid, link)
        return
    from fastapi_mail import MessageSchema, MessageType
    from pydantic import NameEmail

    message_ = MessageSchema(
        subject=f"{who} asked for access to {title or f'recording {rid}'}",
        recipients=[NameEmail(o, o) for o in owners],
        template_body={"who": who, "title": title or f"Recording {rid}", "namespace": namespace, "message": message, "link": link},
        subtype=MessageType.html,
    )
    await _mailer(cfg).send_message(message_, template_name="access_request.html")
