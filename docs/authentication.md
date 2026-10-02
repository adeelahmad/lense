# Authentication

## People and roles

There is no public sign-up. The first admin is created with a one-time **setup code** that the API prints in its log
on first start (or `lens users add you@example.com --admin`, or `LENS_ADMIN_EMAIL` and `LENS_ADMIN_PASSWORD` in
`.env`). On a fresh install the web app then opens a short setup wizard ([Configuration](configuration.md#first-run-setup)). Admins then add people and give them roles per
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
| `GET /api/v1/auth/status` | `{setup_required, wizard_pending}`: the sign-in page shows the setup form while the first admin is missing; admins are taken to the setup wizard while it is pending |
| `POST /api/v1/auth/setup` | first admin, with the setup code |
| `POST /api/v1/auth/login` · `/refresh` · `/logout` | token pairs |
| `GET /api/v1/auth/me` | the account, roles by namespace, and how the caller authenticated (`via`: `access`, `token` or `oauth`) |
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
curl -H "Authorization: Bearer la_…" https://lens.example.org/api/v1/resources
```

## OAuth

For apps that sign people in instead of asking them for a key: MCP clients (Claude, Cursor and others, connecting
to [Lens's MCP server](mcp.md)), desktop and web apps. Lens is the OAuth 2.1 authorization server itself: it issues the tokens, and people sign in with the Lens
account they have. Nothing here creates an account.

```
app ──► GET /.well-known/oauth-authorization-server          where everything is
app ──► POST /api/v1/oauth/register                          { client_id }   (once per app)
app ──► browser: /oauth/authorize?client_id=…&redirect_uri=…&code_challenge=…&code_challenge_method=S256&scope=…&state=…
          the person signs in if they aren't, sees which app asks and for what, and chooses Allow or Deny
        ◄── browser: <redirect_uri>?code=…&state=…
app ──► POST /api/v1/oauth/token   grant_type=authorization_code, code, redirect_uri, code_verifier, client_id
        ◄── { access_token (lo_…), refresh_token (lr_…), expires_in, scope }
app ──► API with  Authorization: Bearer lo_…
app ──► POST /api/v1/oauth/token   grant_type=refresh_token      before the access token expires
```

* **Apps register themselves** (dynamic client registration, RFC 7591), without an account: registering gives an app
  nothing until someone allows it. Its redirect addresses must be https, this machine's (`http://localhost` or
  `127.0.0.1`, any port, for desktop apps), or a scheme of its own (`cursor://…`); addresses with a user name or a
  backslash in them, and the browser's and the operating system's own schemes, are refused. Apps nobody gave access
  to are forgotten after a week, and one address registers at most 8 apps in 15 minutes.
* **PKCE is required** (S256). The code works once, for five minutes, only with the verifier and the redirect address
  it was given for. A code that comes back after it was swapped ends the access it gave (it was probably
  intercepted). The consent page can't be framed by another site.
* **The consent page** (`/oauth/authorize` in the web app) names the app, where it returns the person to and what it
  asks for: `read` (browse, search, chat), or `read write` (also import, edit, reprocess). The person can give read
  only to an app that asked for both. Only a signed-in person answers it: API keys and other apps can't.
* **Tokens act as the person**, with their roles in each namespace and collection, exactly like the API does for them:
  what they can't see, the app can't. An admin's app has the admin's roles in every namespace but not the
  administration (people, settings, the audit log, everyone's keys): that stays with admins signed in or with their
  own API key.
* **Lifetimes** are the admins' ([Configuration](configuration.md#api-keys)): the access token lasts
  `tokens.oauth_access_minutes` (60), and the app stays signed in for `tokens.oauth_refresh_days` (30) after it last
  renewed, at most `tokens.max_days`.
* **Refresh tokens rotate**: each renewal gives a new one. A swapped one that comes back within 60 seconds is refused
  and changes nothing (two requests at once: the app keeps the pair it got first); after that it ends the access,
  because it was probably copied. Only hashes of tokens, codes and app secrets are kept.
* **Taking access away**: people see the apps they allowed under API tokens → Apps with access, and revoke one there
  (`DELETE /api/v1/oauth/grants/<id>`); an app hands its token back with `POST /api/v1/oauth/revoke`. Either way its
  tokens stop working at once. Disabling an account stops its apps too. Allowing an app again replaces what it had.
* Registering, allowing and revoking are audited: `oauth.client.register`, `oauth.grant`, `oauth.revoke`.

`/.well-known/oauth-authorization-server` (RFC 8414) and `/.well-known/oauth-protected-resource` (RFC 9728) are
served on the web app's address as well as the API's, and every 401 that asks for a token points at the second in
`WWW-Authenticate: Bearer resource_metadata="…"`, as MCP clients expect. The addresses in them are the web app's when the request came
through it: the host it reports in `X-Forwarded-Host` when the web app is a trusted proxy
([Configuration](configuration.md#trusted-proxies)), else `FRONTEND_URL`. The web app reports the `Host` the browser
sent, or, with `TRUST_PROXY_HEADERS=true`, what a reverse proxy in front of it says in `X-Forwarded-Host`; leave that
off unless such a proxy sets the header, or anyone could choose the address. Set `FRONTEND_URL` to the address people
use, or apps will be sent to the wrong place to sign in.

| Endpoint | |
|---|---|
| `POST /api/v1/oauth/register` | register an app |
| `GET` · `POST /api/v1/oauth/authorize` | what the consent page shows, and the person's answer |
| `POST /api/v1/oauth/token` | a code or a refresh token for a new pair of tokens (form-encoded) |
| `POST /api/v1/oauth/revoke` | an app hands back a token |
| `GET /api/v1/oauth/grants` · `DELETE …/{id}` | the apps you gave access to; take one's away |

## Share links and signed links

* **Share links** give read-only access to one recording's player and embed, and expire. Each has a short address
  too (`/s/<code>`). Editors see how often each was played and which sites embed it, and revoke one link or all of
  them: `POST/DELETE /api/v1/resources/<id>/share`, `GET /api/v1/resources/<id>/shares`,
  `DELETE /api/v1/resources/<id>/shares/<link id>`. Only hashes of the token and the code are stored. A link that
  no longer works opens a neutral "This link isn't available" page (status 410), the same whatever went wrong, so it
  never tells whether a recording exists.
* **Signed links** are what the API puts in responses for media (`?exp=&sig=`); see [Architecture](architecture.md#media).
  `GET /api/v1/resources/<id>/embed-link` returns a signed `/embed/<id>` link for people who can read the recording.

## IIIF viewers

Other IIIF viewers sign in through the IIIF Authorization Flow 2.0: the access service at `/iiif/auth/access` is a
small sign-in page served by the API, which sets a `SameSite=None; Secure` cookie scoped to IIIF; the token service
posts a token only to the viewer's origin; the probe service answers with a short-lived signed link. See
[IIIF](iiif.md).

## Audit

Changes to people, roles, settings, sources, shares, tokens, apps given access and curation go into the audit log (`GET /api/v1/audit`,
admins).
