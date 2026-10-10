# Access: who sees which assets

Lens follows the roles and permissions matrix of Aviary, the audio and video access platform: every asset is
**public**, **restricted** or **private**, can be **featured**, and a public asset can keep some of its parts
closed. Who may open what depends on who is asking.

## An asset's access

| Setting | Values | Default |
|---|---|---|
| `access` | `public`, `restricted` or `private` | the namespace's default access, else `private` |
| `open` | the parts of a public asset anyone may use: `media` (audio or video, frames; a document's or an image's pages and its file), `transcript` (the asset's text or transcript, its search and downloads) and `index` (chapters, or a document's sections) | the namespace's default parts, else all three |
| `featured` | shown on the home page when public | no |

An asset's **description** (its descriptive metadata: title, date, summary, people, subjects, rights) is never a
closed part: whoever may see the asset's page sees it, as IIIF manifests always have.

Publishing is for owners: they set an asset's access, open parts and featured flag (in its Access dialog, or
through its metadata, bulk edits and reverts), and their namespace's default access and parts in its metadata
profile. Editors edit the rest of the metadata. Every change is kept in the metadata history and can be reverted.
Moving an asset to another namespace doesn't change who sees it: the access it had from its old namespace is pinned
on it.

## Files

A resource's supplementary files ([API](api.md#files)) follow its parts. On a public resource, transcripts, captions
and translations are open when its text is, indexes when its index is, and thumbnails when its media is;
attachments (release forms, notes, anything else) always need permission. Decided with the project owner. People with
permission download all of them; the public page lists the files a visitor may download and says how many more need
permission, which signed-in visitors may ask for. IIIF lists every file, the closed ones behind the Authorization
Flow.

## Who is asking

| In the matrix | In Lens |
|---|---|
| public user | a visitor who isn't signed in |
| public user with view permission in an IP group | a visitor whose address is in an IP group with view permission on the asset or its namespace |
| registered user | a signed-in person with no role in the asset's namespace and no permission on it |
| registered user with view or edit permission | a signed-in person given permission on the asset (below) |
| organization user, admin, owner | a member of the asset's namespace (viewer, editor or owner), and admins everywhere |

Members, people given permission and IP-group visitors have **permission**: they see the asset everywhere and open all of
it. Everyone else gets what the asset's access allows:

| | Public user | Registered user | With permission |
|---|---|---|---|
| **public** | listed; page, description and search; only the open parts | the same, and can request access to the closed parts | everything |
| **restricted** | hidden | listed with a lock ("content locked"); the page stays closed; can request access | everything |
| **private** | hidden | hidden | everything |

The home page shows featured public assets only, to everyone, members included.

## Permission on an asset

Owners give someone permission on one asset in its Access dialog, by the email address of their account (an admin
creates accounts), and take it away there; both are audited. With permission, a person sees all of the asset on
the pages for visitors, whatever its access: a private asset is listed for them in its collection and in search,
and under "Shared with you" on the home page. IIIF lets them in too: its manifest with their token or the IIIF access
cookie, and its media and text through the Authorization Flow. Face crops stay with members unless the namespace
publishes them. Permission doesn't open the workspace: that stays with the namespace's members.

In the matrix, view and edit permission open the same pages; Lens has one permission for both.

## Collection roles

Every asset lives in a collection of its namespace ([API](api.md#collections-of-a-namespace)), and people can be
given a role on a collection: **viewer**, **editor** or **admin**. A role holds for the collections inside it too, and
adds to a namespace role, never takes from it: a viewer of the namespace who is an editor of one collection edits its
assets and only reads the rest.

| | Viewer | Editor | Admin |
|---|---|---|---|
| its assets | see and search them, open their pages, write their own notes | and edit them: text, catalogue record, tags, collection, notes for everyone, reprocessing | and act as their owner: access and featured, permission, deleting |
| the collection | — | — | rename, describe, move it within what they're an admin of, make and delete collections inside it, give roles on it |

Owners of the namespace give roles on any of its collections, admins of a collection on it and the ones inside it
(in the Library: Collection → Manage collections → People). Editors of the namespace still arrange all its
collections; the top of the namespace and its default collection stay theirs. Giving, changing and taking away a role
is audited as `collection.member`. Deleting a collection (only when it's empty) takes the roles given on it.

Someone **without a role in the namespace** who has a role on some of its collections sees just those: their
assets in the Library (the namespace is marked "Some"), in search and on the assets' pages, and the
collections in the Library's Collection filter, starting from the ones they were given. The namespace's own pages
(Home's numbers, People, Graph, Reports, Chat, Activity, Batches) stay with the namespace's members, so nothing outside their
collections shows: those pages say so when such a namespace is picked. On an asset, the entities it mentions are
counted over the assets they see; the namespace's lists of people and faces stay with its members, and renaming
or merging people and faces (which are the namespace's) needs editor access to the namespace. For the pages
visitors see and IIIF, a role on a collection is permission on its assets.

## Asking for access

Someone signed in without permission can ask an asset's owners for access: to the closed parts of a public
asset, or to a restricted one (on its "content locked" page), with a message if they like. The namespace's owners
(admins, when it has none) get an email when mail is set up, and see the request in the asset's Access dialog and
on Home under Needs attention. Approving gives the person permission; declining lets them ask again. The page tells
them where their request stands. Asking again while a request waits only updates its message, and emails the owners
at most once a day; answers are audited.

## IP groups

Owners give a network permission on their namespace's page (Admin → Namespaces), under **IP groups**: a name that
visitors see (a reading room, a campus), its addresses (single addresses or CIDR ranges, none wider than `/8` for IPv4
or `/16` for IPv6), and what the group opens: every asset in the namespace, now and later, or the assets chosen
in each one's Access dialog. A visitor whose address is in a group sees what it opens as if they had permission,
without signing in: listed in its collection and in search, all of its page, and in IIIF (manifests, content,
collections, and the Authorization Flow's probe). The page tells them why ("You're connecting from Reading room").
Adding, changing and deleting groups, and opening or closing an asset to one, are audited.

IP groups need the server to know the visitor's address. Behind the web app, that is what the web app and the proxies
in front of it report in `X-Forwarded-For`, which the server believes only from `server.trusted_proxies` (see
[Configuration](configuration.md#trusted-proxies)). An address the server can't vouch for opens nothing. The IP groups
section shows your address as the server sees it and marks the groups that hold it, or says the server can't tell.

## Pages

| Aviary page | Lens |
|---|---|
| Home, featured resources | the public home page, `/explore` |
| Collection splash page, all resources | a namespace's public page, `/explore/collections/<name>` |
| Search results page | public search, `/explore/search?q=` |
| Resource detail page | an asset's public page, `/explore/recordings/<id>` (members open it in the workspace too) |

**The home page** shows the featured public assets, to everyone, and the collections the visitor can see anything
in. **A collection's page** has the namespace's label, summary, rights and provider (its IIIF metadata) and the
assets the visitor sees, newest first: public ones for everyone, restricted ones with a lock ("content locked" over
the picture) for people who are signed in, and all of them for members and for visitors from an IP group that opens
the namespace. A collection with nothing for the visitor
isn't there for them. The sign-in page links to the home page.

**Search** finds the assets the visitor sees by their title, and by the lines of text they may read,
title matches first; each line links to its moment in the asset. A restricted asset (for signed-in people) and
a public one whose text is closed match on their title only, so a search never reveals what they contain.

**An asset's public page** shows what the visitor may see: its title, date and description, and each open part: the
player or pages, the text (with find in the text, and its files to download), the chapters, and the supplementary
files open to them. A closed part says who can open it and offers visitors to sign in; signing in comes back to the
page. Signed-in people without permission see a restricted asset's title behind a lock ("content locked"); members
see all of it, with a link to the workspace. `?t=<seconds>` opens it at a moment. Owners find the link to share in a
public asset's Access dialog. The page is left out of search engines unless the asset is public.

The workspace (Library, asset pages, Search, Chat and the rest) stays as it is: namespaces you have no role in
never appear there, and grant holders see the assets shared with them.

## What IIIF publishes

IIIF follows the same setting. A public asset's manifest is open; its media and text are plain links when
those parts are open, otherwise they sit behind the IIIF Authorization Flow, which admits people with permission
(visitors from an IP group's addresses need no sign-in). Chapters appear as ranges when the index is open. Supplementary
files follow their parts the same way (see Files), and custom fields appear only when published ([API](api.md#fields)).
Restricted and private assets are not published: their manifests answer 404 unless the request carries permission,
and they are left out of collections. Where a namespace pixelates faces for visitors ([Video](video.md)), its frames
and pages come through IIIF with the faces found pixelated, unless the request (or the IIIF access cookie's account)
has a role in the namespace. Change Discovery announces a **Create** when an asset becomes public, an
**Update** when a public one changes, and a **Delete** when it stops being public.

## Moving from the IIIF access levels

Lens used to set access for IIIF only, with four levels. The first start of this version converts them, once:

| Before | After |
|---|---|
| `public` | `public`, all parts open |
| `transcript` (text open, audio after sign-in) | `public`, text and index open, media closed |
| `signed-in` (listed, content after sign-in) | `restricted` |
| `private` | `private` |

Namespace defaults convert the same way. Assets that were `signed-in` were listed in IIIF and no longer are, so the
conversion announces a Delete for each of them. Old metadata history entries convert when they are reverted.
