# Changelog

The backend (`fastapi_backend`) and the frontend (`nextjs-frontend`) are versioned together.

## 0.2.0 <small>September 30, 2026</small> {id="0.2.0"}

Lens moves from a single-process prototype to a platform on the Next.js FastAPI template.

- **Backend**
    - The prototype's processing engine now lives in `app/domain`, unchanged in behaviour.
    - The API is split into routers under `/api/v1` with Pydantic request and response models, so the OpenAPI schema
      and the frontend's typed client cover every endpoint.
    - Authentication: short-lived JWT access tokens and rotating refresh tokens (with reuse detection) for NextAuth,
      replacing session cookies and CSRF tokens; password reset by email; API tokens unchanged.
    - Media is served through signed links, since `<audio>` and `<img>` can't send bearer tokens.
    - SurrealDB: a connection pool for servers, and automatic retries of write conflicts.
    - Workers run as their own process (`lens worker`); the API can still run them inline for development.
    - Removed the template's Postgres, SQLAlchemy, Alembic, fastapi-users and Vercel backend deployment.
- **Frontend**
    - NextAuth (Auth.js v5) with a credentials provider backed by the API, token refresh, first-run setup and password
      reset. Public registration is gone: admins invite people.
    - A typed API client wired to the session, and rewrites so media and IIIF are served from the web app's origin.
- **Operations**
    - Docker Compose for development (SurrealDB, API, worker, web app, MailHog) and a production-shaped compose file.
    - CI runs lint, type checks, an OpenAPI drift check and the tests against embedded SurrealDB and a SurrealDB server.
