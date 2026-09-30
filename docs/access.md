# Access: who sees which recordings

Lens follows the roles and permissions matrix of Aviary, the audio and video access platform: every recording is
**public**, **restricted** or **private**, can be **featured**, and a public recording can keep some of its parts
closed. Who may open what depends on who is asking.

!!! note "Being built"
    Recordings' access settings, permission on a recording, the web app, IIIF and the pages for visitors (home,
    collections, search and a recording's page) follow this model now. Access requests and IP groups come next.

## A recording's access

| Setting | Values | Default |
|---|---|---|
| `access` | `public`, `restricted` or `private` | the namespace's default access, else `private` |
| `open` | the parts of a public recording anyone may use: `media` (audio or video, frames), `transcript` (the text with who said what, its search and downloads) and `index` (chapters) | the namespace's default parts, else all three |
| `featured` | shown on the home page when public | no |

A recording's **description** (its descriptive metadata: title, date, summary, speakers, subjects, rights) is never a
closed part: whoever may see the recording's page sees it, as IIIF manifests always have.

Publishing is for owners: they set a recording's access, open parts and featured flag (in its Access dialog, or
through its metadata, bulk edits and reverts), and their namespace's default access and parts in its metadata
profile. Editors edit the rest of the metadata. Every change is kept in the metadata history and can be reverted.

## Who is asking

| In the matrix | In Lens |
|---|---|
| public user | a visitor who isn't signed in |
| public user with view permission in an IP group | a visitor whose address is in an IP group with view permission on the recording or its namespace |
| registered user | a signed-in person with no role in the recording's namespace and no permission on it |
| registered user with view or edit permission | a signed-in person given permission on the recording (below) |
| organization user, admin, owner | a member of the recording's namespace (viewer, editor or owner), and admins everywhere |

Members, people given permission and IP-group visitors have **permission**: they see the recording everywhere and open all of
it. Everyone else gets what the recording's access allows:

| | Public user | Registered user | With permission |
|---|---|---|---|
| **public** | listed; page, description and search; only the open parts | the same, and can request access to the closed parts | everything |
| **restricted** | hidden | listed with a lock ("content locked"); the page stays closed; can request access | everything |
| **private** | hidden | hidden | everything |

The home page shows featured public recordings only, to everyone, members included.

## Permission on a recording

Owners give someone permission on one recording in its Access dialog, by the email address of their account (an admin
creates accounts), and take it away there; both are audited. With permission, a person sees all of the recording on
the pages for visitors, whatever its access: a private recording is listed for them in its collection and in search,
and under "Shared with you" on the home page. IIIF lets them in too: its manifest with their token or the IIIF access
cookie, and its media and transcript through the Authorization Flow. Face crops stay with members unless the namespace
publishes them. Permission doesn't open the workspace: that stays with the namespace's members.

In the matrix, view and edit permission open the same pages; Lens has one permission for both.

## Pages

| Aviary page | Lens |
|---|---|
| Home, featured resources | the public home page, `/explore` |
| Collection splash page, all resources | a namespace's public page, `/explore/collections/<name>` |
| Search results page | public search, `/explore/search?q=` |
| Resource detail page | a recording's public page, `/explore/recordings/<id>` (members open it in the workspace too) |

**The home page** shows the featured public recordings, to everyone, and the collections the visitor can see anything
in. **A collection's page** has the namespace's label, summary, rights and provider (its IIIF metadata) and the
recordings the visitor sees, newest first: public ones for everyone, restricted ones with a lock ("content locked" over
the picture) for people who are signed in, and all of them for members. A collection with nothing for the visitor
isn't there for them. The sign-in page links to the home page.

**Search** finds the recordings the visitor sees by their title, and by the lines of the transcripts they may read,
title matches first; each line links to its moment in the recording. A restricted recording (for signed-in people) and
a public one whose transcript is closed match on their title only, so a search never reveals what they say.

**A recording's public page** shows what the visitor may see: its title, date and description, and each open part:
the player, the transcript (with find in the transcript, and its files to download) and the chapters. A closed part
says who can open it and offers visitors to sign in; signing in comes back to the page. Signed-in people without
permission see a restricted recording's title behind a lock ("content locked"); members see all of it, with a link to
the workspace. `?t=<seconds>` opens it at a moment. Owners find the link to share in a public recording's Access
dialog. The page is left out of search
engines unless the recording is public.

The workspace (Library, recording pages, Search, Chat and the rest) stays as it is: namespaces you have no role in
never appear there, and grant holders see the recordings shared with them.

## What IIIF publishes

IIIF follows the same setting. A public recording's manifest is open; its media and transcript are plain links when
those parts are open, otherwise they sit behind the IIIF Authorization Flow, which admits people with permission.
Chapters appear as ranges when the index is open. Restricted and private recordings are not published: their
manifests answer 404 unless the request carries permission, and they are left out of collections. Change Discovery
announces a **Create** when a recording becomes public, an **Update** when a public one changes, and a **Delete**
when it stops being public.

## Moving from the IIIF access levels

Lens used to set access for IIIF only, with four levels. The first start of this version converts them, once:

| Before | After |
|---|---|
| `public` | `public`, all parts open |
| `transcript` (text open, audio after sign-in) | `public`, transcript and index open, media closed |
| `signed-in` (listed, content after sign-in) | `restricted` |
| `private` | `private` |

Namespace defaults convert the same way. Recordings that were `signed-in` were listed in IIIF and no longer are, so the
conversion announces a Delete for each of them. Old metadata history entries convert when they are reverted.
