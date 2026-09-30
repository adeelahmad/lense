"""Process settings, read from the environment (and .env).

Two layers of configuration exist on purpose:

* ``Settings`` (this module) holds what the process needs before it can reach the database: secrets, the database
  connection, CORS, mail, and where archive.yaml lives. It is read from the environment only.
* The archive configuration (``archive.yaml`` merged over built-in defaults, then over settings saved in the app) holds
  everything about how recordings are processed. See ``app.domain.store.DEFAULTS`` and ``app.domain.settings``.
"""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # OpenAPI docs ("" disables /docs and /openapi.json)
    OPENAPI_URL: str = "/openapi.json"
    PROJECT_NAME: str = "Lens"

    # archive.yaml (namespaces, processing defaults). Missing is fine: built-in defaults apply.
    ARCHIVE_CONFIG: str = "archive.yaml"

    # Access tokens handed to the web app (NextAuth) and API clients
    ACCESS_SECRET_KEY: str = Field(min_length=16)
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_SECONDS: int = 900
    # Signed links for <audio>, <video> and <img> tags, which can't send an Authorization header
    MEDIA_URL_EXPIRE_SECONDS: int = 6 * 3600
    PASSWORD_RESET_EXPIRE_MINUTES: int = 60

    # Background work inside the API process: None follows workers.inline from the archive configuration.
    # docker-compose runs a separate worker service and sets this to false.
    RUN_BACKGROUND: bool | None = None

    # Email (password reset)
    MAIL_USERNAME: str | None = None
    MAIL_PASSWORD: str | None = None
    MAIL_FROM: str | None = None
    MAIL_SERVER: str | None = None
    MAIL_PORT: int | None = None
    MAIL_FROM_NAME: str = "Lens"
    MAIL_STARTTLS: bool = True
    MAIL_SSL_TLS: bool = False
    USE_CREDENTIALS: bool = True
    VALIDATE_CERTS: bool = True

    # Frontend (Next.js): used for links in emails
    FRONTEND_URL: str = "http://localhost:3000"

    # CORS: the browser only calls the API directly for streams (job events, chat); list the frontend's origin
    CORS_ORIGINS: set[str] = {"http://localhost:3000"}

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    @property
    def mail_enabled(self) -> bool:
        return bool(self.MAIL_SERVER and self.MAIL_FROM)


settings = Settings()
