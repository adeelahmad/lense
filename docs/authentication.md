# Authentication

## People and roles

There is no public sign-up. The first admin is created with a one-time **setup code** that the API prints in its log
on first start (or `lens users add you@example.com --admin`). Admins then add people and give them roles per
namespace: viewer, editor or owner.

Passwords are hashed with scrypt and need at least 10 characters. Failed sign-ins are throttled per email and address
(8 per 15 minutes). Unknown emails take as long to reject as wrong passwords.

## The web app: NextAuth with API tokens

```
login form ──► NextAuth Credentials provider ──► POST /api/v1/auth/login
                                                  ◄── { access_token (JWT, 15 min), refresh_token, expires_in, user }
NextAuth stores both in its encrypted session cookie (JWT strategy).
Server components / actions ──► API with  Authorization: Bearer <access_token>
Shortly before expiry, NextAuth's jwt callback ──► POST /api/v1/auth/refresh  (rotates the refresh token)
Sign out ──► POST /api/v1/auth/logout (ends the session on the API too)
```

* **Access tokens** are short-lived JWTs (`ACCESS_TOKEN_EXPIRE_SECONDS`, default 15 minutes) naming the account and
  the session. Every request checks that the account is still active and the session still exists, so disabling
  someone or signing out takes effect immediately.
* **Refresh tokens** are random, stored only as hashes, one session per signed-in device, valid for
  `server.session_hours`. Each refresh rotates the token. A rotated token that comes back within 60 seconds is
  accepted (two tabs refreshing at once); after that it ends the whole session, because it was probably copied.
* An admin changing someone's password, a reset link, or disabling an account ends all of that person's sessions.
  Changing your own password (with your current one) ends your other sessions and keeps the one you used.

| Endpoint | |
|---|---|
| `GET /api/v1/auth/status` | `{setup_required}`: the sign-in page shows the setup form when true |
| `POST /api/v1/auth/setup` | first admin, with the setup code |
| `POST /api/v1/auth/login` · `/refresh` · `/logout` | token pairs |
| `GET /api/v1/auth/me` | the account, roles by namespace, and how the caller authenticated |
| `PATCH /api/v1/auth/me` | change your own name |
| `POST /api/v1/auth/password` | change your own password with your current one (signed in, not with an API token); wrong guesses are throttled like sign-ins; audited as `password.change` |
| `POST /api/v1/auth/password/forgot` · `/reset` | email a one-time reset link (60 minutes); answers the same for unknown emails |

Reset emails go through the SMTP server in `MAIL_*`; without one, the link is written to the API log.

## API tokens

For scripts and integrations: `POST /api/v1/tokens` (while signed in) returns `la_…` once. Tokens are **read** or
**write** scoped, act with their maker's roles, and expire after `days`: by default `tokens.default_days`, at most
`tokens.max_days`, and never (`0`) only where `tokens.never_expire` allows ([Configuration](configuration.md#api-keys);
`GET /api/v1/tokens/limits` says what's allowed). Their maker revokes them, and admins can revoke anyone's
(`/api/v1/admin/tokens`); both are audited as `token.revoke`.

```bash
curl -H "Authorization: Bearer la_…" https://lens.example.org/api/v1/recordings
```

## Share links and signed links

* **Share links** give read-only access to one recording's player and embed, and expire. Each has a short address
  too (`/s/<code>`). Editors see how often each was played and which sites embed it, and revoke one link or all of
  them: `POST/DELETE /api/v1/recordings/<id>/share`, `GET /api/v1/recordings/<id>/shares`,
  `DELETE /api/v1/recordings/<id>/shares/<link id>`. Only hashes of the token and the code are stored. A link that
  no longer works opens a neutral "This link isn't available" page (status 410), the same whatever went wrong, so it
  never tells whether a recording exists.
* **Signed links** are what the API puts in responses for media (`?exp=&sig=`); see [Architecture](architecture.md#media).
  `GET /api/v1/recordings/<id>/embed-link` returns a signed `/embed/<id>` link for people who can read the recording.

## IIIF viewers

Other IIIF viewers sign in through the IIIF Authorization Flow 2.0: the access service at `/iiif/auth/access` is a
small sign-in page served by the API, which sets a `SameSite=None; Secure` cookie scoped to IIIF; the token service
posts a token only to the viewer's origin; the probe service answers with a short-lived signed link. See
[IIIF](iiif.md).

## Audit

Changes to people, roles, settings, sources, shares, tokens and curation go into the audit log (`GET /api/v1/audit`,
admins).
