"""What Lens found about a resource, as plain text for an evidence PDF (evidence.py, docs/api.md#evidence-pdfs).

An evidence PDF holds each resource's own file and, after it, these pages: its details, metadata, tags and topics, the
summary and analysis models wrote, its sections, speakers, emotion and sounds, entities and keywords, what is seen in
it, the places and dates found in it, and (when asked for) the notes, highlights and comments people shared on it.
Each part can be left out (PARTS). What a model wrote says so, and what is cut short says how much more there is.

It's plain text because that is what anytopdf reads the same way everywhere, here or on a conversion node: it lays
Markdown and HTML out as text too. Nothing here calls a model; it reads what Lens already stored.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from functools import cached_property

from . import analyze, comments, content_types, entity_setup, fields, highlights, hierarchy, library, metadata, nsfw, render, store
from . import topics as topicmod

R = store.R
# the parts of an evidence PDF, in their order there, with what each holds (the API's `include`)
PARTS = {
    "file": "its own file: a document's pages, an image, or audio and video with Lens's transcript",
    "details": "what it is, when it was made and added, its length, language, content type and where it came from",
    "metadata": "its description as saved and derived (Dublin Core terms, rights, creators) and its custom fields",
    "tags": "its tags",
    "topics": "the topics it is about, accepted and suggested",
    "summary": "the summary a model wrote: key points, follow-ups, people, overall tone and importance",
    "analysis": "what its pipeline's model steps saved",
    "sections": "the sections analysis split it into",
    "speakers": "who spoke, for how long, and how each was recognised",
    "emotion": "the emotion of what was said and the sounds heard, overall and by speaker, marked on each transcript line",
    "entities": "the people, organisations, places and other names it mentions",
    "keywords": "its keywords",
    "visual": "the faces, objects, descriptions and text on screen seen in it",
    "places": "the places and dates anytopdf found in it, and where a photo was taken",
    "notes": "the notes, highlights and comments people shared on it",
}
# everything Lens found; people's own words (notes, highlights and comments) only when asked for
DEFAULT = tuple(k for k in PARTS if k != "notes")
MAX_ITEMS = 1000  # of one list (text on screen, descriptions …): the rest is counted, not shown
METHODS = {"voice": "matched by voice", "new": "a new voice", "label": "named in the transcript"}


def parts_of(include):
    """The parts asked for, in their order: DEFAULT when nothing was said."""
    asked = set(DEFAULT if include is None else include)
    unknown = asked - set(PARTS)
    if unknown:
        raise ValueError(f"an evidence PDF's parts are {', '.join(PARTS)}")
    return [k for k in PARTS if k in asked]


def marked(d):
    """The player's data with each transcript line marked with its emotion and sound, "(Happy, Laughter) …", as in
    subtitles for the deaf; Neutral and plain speech aren't marked."""
    out = []
    for x in d["segments"]:
        marks = [m for m in (x.get("e"), x.get("v")) if m and m not in ("Neutral", "Speech")]
        out.append({**x, "text": f"({', '.join(marks)}) {x['text']}" if marks else x["text"]})
    return {**d, "segments": out}


class _Page:
    """The text being written: headings, lines and lists."""

    def __init__(self):
        self.lines: list[str] = []

    def heading(self, title):
        self.lines += ["", title, "-" * len(title)]

    def line(self, text=""):
        self.lines.append(str(text))

    def pair(self, label, value):
        value = _words(value)
        if value:
            self.lines.append(f"{label}: {value}")

    def items(self, rows, empty=None):
        rows = [r for r in rows if r]
        for r in rows[:MAX_ITEMS]:
            self.lines.append(f"- {r}")
        if len(rows) > MAX_ITEMS:
            self.lines.append(f"(and {len(rows) - MAX_ITEMS:,} more, not shown here)")
        if not rows and empty:
            self.lines.append(empty)

    def text(self):
        return "\n".join(self.lines).strip() + "\n"


def _words(v):
    """A stored value in words: language maps, lists and {name, role}-like objects flattened."""
    if v is None or v == "" or v == [] or v == {}:
        return ""
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, (int, float)):
        return f"{v:,}" if isinstance(v, int) else f"{v:g}"
    if isinstance(v, list):
        return "; ".join(w for w in (_words(x) for x in v) if w)
    if isinstance(v, dict):
        if all(isinstance(x, list) for x in v.values()):  # a language map
            return "; ".join(w for w in (_words(x) for x in v.values()) if w)
        for k in ("name", "label", "value", "text", "title", "id"):
            if v.get(k):
                rest = [f"{_words(x)}" for kk, x in v.items() if kk in ("role", "type") and x]
                return f"{_words(v[k])}{f' ({rest[0]})' if rest else ''}"
        return "; ".join(f"{k}: {w}" for k, w in ((k, _words(x)) for k, x in v.items()) if w)
    return " ".join(str(v).split())


def _outline(page, v, depth=0):
    """Any JSON a model step saved, as nested lines."""
    pad = "  " * depth
    if isinstance(v, dict):
        for k, x in v.items():
            if isinstance(x, (dict, list)) and x:
                page.line(f"{pad}{k}:")
                _outline(page, x, depth + 1)
            elif _words(x):
                page.line(f"{pad}{k}: {_words(x)}")
    elif isinstance(v, list):
        for x in v[:MAX_ITEMS]:
            if isinstance(x, dict) and x:
                page.line(f"{pad}-")
                _outline(page, x, depth + 1)
            elif _words(x):
                page.line(f"{pad}- {_words(x)}")
        if len(v) > MAX_ITEMS:
            page.line(f"{pad}(and {len(v) - MAX_ITEMS:,} more, not shown here)")
    elif _words(v):
        page.line(f"{pad}{_words(v)}")


def _n(n, what):
    """1 turn, 2 turns."""
    n = n or 0
    return f"{n:,} {what}{'' if n == 1 else 's'}"


def _share(part, whole):
    return f"{100 * part / whole:.0f}%" if whole else ""


class Report:
    """One resource's findings. `places`: whether the reader may see where it was (members of its namespace)."""

    def __init__(self, db, cfg, rid, d=None, places=True):
        self.db, self.cfg, self.rid, self.places = db, cfg, rid, places
        self.rec = db.one("SELECT * FROM $r", r=R("recording", rid)) or {}
        if not self.rec:
            raise KeyError(rid)
        self.d = d or render.player_data(db, rid)
        self.kind = render.kind(self.rec)
        self.paged = self.kind in ("document", "image")
        # a document's lines have reading-pace times: a moment on it is the page of the line it falls in
        self._pages = [(x["t0"], x["t1"], x.get("p")) for x in self.d["segments"] if x.get("p") is not None]

    def at(self, t0, t1=None):
        """Where in it a moment is: "p. 3" on a document, else "1:02" or "1:02 to 1:30"."""
        if t0 is None:
            return ""
        if self.paged:
            page = next((p for a, b, p in self._pages if a <= t0 < b), None)
            if page is None and self._pages:
                page = max((p for a, _, p in self._pages if a <= t0), default=self._pages[0][2])
            return f"p. {page + 1}" if page is not None else ""
        return f"{store.tc(t0)} to {store.tc(t1)}" if t1 is not None and t1 > t0 else store.tc(t0)

    @cached_property
    def stats(self):
        """Its talk statistics: speakers, emotions, sounds and languages (analyze.talk_stats), with speakers' names."""
        return render.recording_stats(self.db, self.rid)

    def where(self, t, paged=None):
        """A face's, an object's or a description's place: a page (from 0) on pages, else a time."""
        return f"p. {t + 1}" if (self.paged if paged is None else paged) else store.tc(t)

    def text(self, parts):
        title = self.rec.get("title") or f"Resource {self.rid}"
        page = _Page()
        page.line(f"What Lens found: {title}")
        page.line()
        page.line(
            f"Lens wrote these pages on {store.now()[:10]} from what it holds about resource {self.rid}. What a model "
            "wrote says so; the rest comes from the file and Lens's own steps."
        )
        for part in parts:
            if part != "file" and (part != "places" or self.places):
                getattr(self, f"_{part}")(page)
        return page.text()

    # ---------- the parts ----------
    def _details(self, page):
        rec, d, st = self.rec, self.d, self.stats
        page.heading("Details")
        page.pair("Type", self.kind)
        page.pair("Namespace", d.get("namespace"))
        if rec.get("collection") is not None:
            page.pair("Collection", " / ".join(s["name"] for s in hierarchy.path(self.db, rec["collection"])))
        page.pair("Recorded", (rec.get("recorded_at") or "")[:16].replace("T", " "))
        page.pair("Added", (rec.get("created_at") or "")[:16].replace("T", " "))
        media = d.get("media") or {}
        if self.paged:
            page.pair("Pages", media.get("pages") or len(d.get("pages") or []) or None)
        elif d.get("duration_ms"):
            page.pair("Length", store.tc(d["duration_ms"]))
        if media.get("width") and media.get("height"):
            page.pair(
                "Size", f"{media['width']} × {media['height']} pixels" + (f", {media['fps']:g} frames a second" if media.get("fps") else "")
            )
        page.pair("Language", rec.get("language") if rec.get("language") not in ("none", "nospeech") else None)
        if len(st.get("languages") or {}) > 1:
            page.pair(
                "Languages spoken", ", ".join(f"{k} ({_n(n, 'line')})" for k, n in sorted(st["languages"].items(), key=lambda kv: -kv[1]))
            )
        page.pair("Words", st.get("words"))
        try:
            ct, chosen = content_types.of_recording(self.db, self.rid)
            page.pair("Content type", f"{ct['label']} ({'chosen by a person' if chosen else 'recognised by Lens'})" if ct else None)
        except KeyError:
            pass
        origins = library.Origins(self.db, self.cfg)
        came = origins.of(rec)
        page.pair("Came from", origins.names([came])[came])
        page.pair("Status", rec.get("status"))
        page.pair("NSFW", self._nsfw())
        if rec.get("attached_to") and rec["attached_to"].get("resource"):
            email = self.db.one("SELECT title FROM $r", r=R("recording", int(rec["attached_to"]["resource"]))) or {}
            page.pair("Attached to the email", email.get("title") or f"resource {rec['attached_to']['resource']}")
        email = rec.get("email") or {}
        for k, label in (("subject", "Email subject"), ("from", "From"), ("to", "To"), ("cc", "Cc"), ("date", "Sent")):
            page.pair(label, email.get(k))
        web = rec.get("web") or {}
        page.pair("Web page", web.get("url"))
        if web.get("final") and web.get("final") != web.get("url"):
            page.pair("It ended at", web["final"])
        page.pair("Captured", (web.get("captured_at") or "")[:16].replace("T", " "))
        told = rec.get("described") or {}
        for k, label in (
            ("title", "Its own title"),
            ("creators", "Its authors"),
            ("subject", "Its subject"),
            ("description", "Its description"),
            ("publisher", "Its publisher"),
            ("language", "Its language"),
            ("created", "Made"),
            ("modified", "Last changed"),
            ("pages", "Pages it says it has"),
            ("words", "Words it says it has"),
            ("format", "Its format"),
        ):
            page.pair(label, told.get(k))
        made = rec.get("rendition") or {}
        if made.get("from") and made.get("by"):
            who = "the service that sent it" if made["by"] == "pushed" else made["by"]
            page.pair("Read from", f"a PDF {who} made of its {made['from']} file")

    def _nsfw(self):
        """Its NSFW classification, where classifying is on in its namespace (docs/nsfw.md)."""
        b = nsfw.brief(self.rec.get("nsfw"), nsfw.policy(self.db, self.cfg, self.rec["space"]))
        if not b:
            return None
        how = ["by a person"] if b.get("manual") is not None else []
        if b.get("score") is not None:
            how.append(f"its most likely picture scored {b['score']:.2f}, flagged from {b['threshold']:.2f}")
        if b.get("checked") is not None:
            how.append(f"{b['checked']} of {b.get('of') or b['checked']} pictures checked")
        said = "flagged" if b["flagged"] else "not flagged"
        return f"{said} ({'; '.join(how)})" if how else said

    def _metadata(self, page):
        meta = metadata.effective(self.db, self.cfg, self.rid)
        page.heading("Metadata")
        labels = (
            ("label", "Title"),
            ("summary", "Description"),
            ("navDate", "Date"),
            ("language", "Language"),
            ("creators", "Creators"),
            ("contributors", "Contributors"),
            ("subjects", "Subjects"),
            ("identifiers", "Identifiers"),
            ("rights", "Rights"),
            ("attribution", "Attribution"),
            ("provider", "Provider"),
            ("homepage", "Homepage"),
            ("related", "Related"),
            ("access", "Access"),
            ("open", "Open to anyone when public"),
            ("featured", "Featured"),
        )
        for k, label in labels:
            page.pair(label, meta.get(k))
        for pair in meta.get("metadata") or []:
            page.pair(_words(pair.get("label")), pair.get("value"))
        for term, vals in sorted((meta.get("terms") or {}).items()):
            page.pair(f"dcterms:{term}", vals)
        for s in meta.get("statements") or []:
            page.pair(s["p"], s["o"])
        defs, row = fields.resource_fields(self.db, self.rid)
        for f in fields.shown(defs, row):
            if f["value"] is not None:
                page.pair(f["field"]["label"], fields.display(f["field"], f["value"]))

    def _tags(self, page):
        page.heading("Tags")
        page.line(", ".join(self.rec.get("tags") or []) or "None.")

    def _topics(self, page):
        found = topicmod.of_recording(self.db, self.rid)
        page.heading("Topics")
        how = {
            "person": "added by a person",
            "entity": "made from an entity",
            "analysis": "found by analysis",
            "assistant": "by an assistant",
        }
        page.items(
            [
                f"{t['label']} ({'accepted' if t['status'] == 'accepted' else 'suggested, not accepted yet'}"
                f"{', ' + how[t['source']] if t.get('source') in how else ''})"
                for t in sorted(found, key=lambda t: (t["status"] != "accepted", -(t.get("weight") or 0), t["label"].casefold()))
            ],
            "None.",
        )

    def _summary(self, page):
        s = self.d.get("summary") or {}
        page.heading("Summary (written by a model)")
        if not s:
            page.line("None yet.")
            return
        page.line(s.get("summary") or s.get("tldr") or "")
        for key, title in (("key_points", "Key points"), ("action_items", "Follow-ups")):
            rows = []
            for x in s.get(key) or []:
                x = x if isinstance(x, dict) else {"text": x}
                where = f"p. {x['page'] + 1}" if x.get("page") is not None else self.at(x.get("t0"))
                rows.append(f"{x.get('who') + ': ' if x.get('who') else ''}{x.get('text')}{f' (at {where})' if where else ''}")
            if rows:
                page.line()
                page.line(f"{title}:")
                page.items(rows)
        page.line()
        page.pair("Topics it names", s.get("topics"))
        page.pair("People it names", s.get("people"))
        page.pair("Overall tone", s.get("sentiment"))
        if s.get("importance"):
            page.pair("Importance", f"{s['importance']} of 5")
        known = {"summary", "tldr", "key_points", "action_items", "topics", "people", "sentiment", "importance"}
        _outline(page, {k: v for k, v in s.items() if k not in known})

    def _analysis(self, page):
        rows = self.db.rows("SELECT key, value, origin, created_at FROM output WHERE recording = $r ORDER BY key", r=self.rid)
        page.heading("Analysis (written by models)")
        if not rows:
            page.line("None.")
        for o in rows:
            by = (o.get("origin") or {}).get("model") if isinstance(o.get("origin"), dict) else None
            page.line()
            when = (o.get("created_at") or "")[:10]
            page.line(f"{o['key']}{f' (by {by})' if by else ''}{f', {when}' if when else ''}:")
            _outline(page, o.get("value"), 1)

    def _sections(self, page):
        segs, secs = self.d["segments"], self.d.get("sections") or []
        page.heading("Sections")
        rows = []
        for x in secs:
            first = segs[x["seg0"]] if 0 <= x.get("seg0", -1) < len(segs) else None
            where = f"p. {first['p'] + 1}" if self.paged and first and first.get("p") is not None else self.at(x.get("t0"), x.get("t1"))
            rows.append(f"{where}: {x.get('title')}" if where else x.get("title"))
        page.items(rows, "None.")

    def _speakers(self, page):
        st = self.stats
        apps = {a["speaker"]: a for a in self.db.rows("SELECT speaker, method, score FROM appearance WHERE recording = $r", r=self.rid)}
        page.heading("Speakers")
        speech = st.get("speech_ms") or 0
        rows = []
        for s in st.get("speakers") or []:
            a = apps.get(s.get("speaker_id")) or {}
            how = METHODS.get(a.get("method"), a.get("method"))
            if how and a.get("score") is not None:
                how += f", {a['score']:.2f}"
            rows.append(
                f"{s['name']}: {store.tc(s['talk_ms'])} ({_share(s['talk_ms'], speech)} of the speech), {_n(s.get('turns'), 'turn')}, "
                f"{_n(s.get('words'), 'word')}, {s.get('wpm', 0):g} words a minute{f'; {how}' if how else ''}"
            )
        page.items(rows, "None found.")

    def _emotion(self, page):
        st = self.stats
        page.heading("Emotion and sounds")
        emo = {k: v for k, v in (st.get("emotions") or {}).items() if v}
        total = sum(emo.values())
        if total:
            page.line("Share of the speech time:")
            page.items([f"{e}: {_share(ms, total)} ({store.tc(ms)})" for e, ms in sorted(emo.items(), key=lambda kv: -kv[1])])
        names = {sp["key"]: sp["name"] for sp in self.d["speakers"]}
        by = defaultdict(Counter)
        for x in self.d["segments"]:
            if x.get("e"):
                by[names.get(x.get("s"), "Unattributed")][x["e"]] += max(0, (x.get("t1") or 0) - (x.get("t0") or 0))
        if len(by) > 1 or (by and not total):
            page.line()
            page.line("By speaker:")
            page.items(
                [
                    f"{who}: " + ", ".join(f"{e} {_share(ms, sum(c.values()))}" for e, ms in c.most_common() if ms)
                    for who, c in sorted(by.items(), key=lambda kv: -sum(kv[1].values()))
                ]
            )
        events = st.get("events") or {}
        if events:
            page.line()
            page.line("Sounds heard:")
            page.items([f"{e}: {_n(n, 'time')}" for e, n in sorted(events.items(), key=lambda kv: -kv[1])])
        if total or events:
            page.line()
            page.line(
                "Each transcript line is marked with its emotion and sound, such as (Happy, Laughter); unmarked lines were neutral speech."
            )
        else:
            page.line("None read.")

    def _entities(self, page):
        from .entities import TYPES

        ments = self.db.rows("SELECT entity, text FROM mentions WHERE recording = $r", r=self.rid)
        ents = (
            {
                e["id"]: e
                for e in self.db.rows(
                    "SELECT record::id(id) AS id, space, name, type FROM entity WHERE id IN $ids AND hidden != true",
                    ids=[R("entity", i) for i in {m["entity"] for m in ments}],
                )
            }
            if ments
            else {}
        )
        own = entity_setup.labels(self.db, {e["space"] for e in ents.values()})
        counts = Counter(m["entity"] for m in ments if m["entity"] in ents and ents[m["entity"]]["type"] != "NUMBER")
        groups = defaultdict(list)
        for eid, n in counts.most_common():
            e = ents[eid]
            groups[TYPES.get(e["type"]) or own.get(e["space"], {}).get(e["type"]) or e["type"].title()].append(f"{e['name']} ({n})")
        page.heading("Entities")
        if not groups:
            page.line("None found.")
        for kind, names in sorted(groups.items(), key=lambda kv: kv[0] == "Date"):
            page.line(
                f"{kind}: {', '.join(names[:MAX_ITEMS])}{f' (and {len(names) - MAX_ITEMS:,} more)' if len(names) > MAX_ITEMS else ''}"
            )

    def _keywords(self, page):
        words = analyze.keywords(self.db, self.rid, 60) if self.rec.get("analyzed_at") else []
        page.heading("Keywords")
        page.line(", ".join(w for w, _ in words) or "None.")

    def _visual(self, page):
        d = self.d
        page.heading("What's seen")
        shown = False
        if d.get("shots"):
            page.pair("Shots", len(d["shots"]))
            shown = True
        people = d.get("faces") or []
        if people:
            page.line()
            page.line(f"Faces ({'recognised' if d.get('faces_mode') == 'recognize' else 'found'}):")
            unit = (lambda n: f"on {_n(n, 'page')}") if self.paged else (lambda n: f"{store.tc(n)} on screen")
            page.items([f"{t['name']}: {unit(t.get('screen_ms') or 0)}, first at {self.where(t.get('first_ms') or 0)}" for t in people])
            shown = True
        objs = d.get("objects") or []
        if objs:
            page.line()
            page.line("Objects:")
            rows = []
            for o in objs:
                seen, n = o.get("screen_ms") or 0, o.get("count") or 0
                long = f"on {_n(seen, 'page')}" if o.get("paged") else f"{store.tc(seen)} on screen"
                first = self.where(o.get("first_ms") or 0, o.get("paged"))
                rows.append(f"{o['label']}: found {_n(n, 'time')}, {long}, first at {first}, {o.get('score') or 0:.2f} sure")
            page.items(rows)
            shown = True
        said = d.get("descriptions") or []
        if said:
            models = sorted({x.get("model") for x in said if x.get("model")})
            page.line()
            page.line(f"What each {'page' if self.paged else 'shot'} shows (written by {', '.join(models) or 'a model'}):")
            page.items([f"{self.where(x['t0'], x.get('paged'))}: {_words(x['text'])}" for x in said])
            shown = True
        screen = d.get("screen_text") or []
        if screen:
            page.line()
            page.line("Text on screen:")
            page.items([f"{store.tc(x['t0'])}: {_words(x['text'])}" for x in screen])
            shown = True
        if not shown:
            page.line("Nothing found." if self.kind == "video" or self.paged else "None: it has no pictures.")

    def _places(self, page):
        e = self.rec.get("enrichment") or {}
        page.heading("Places and dates")
        if not e:
            page.line("None found.")
            return

        def place(p):
            where = ", ".join(x for x in (p.get("name"),) if x)
            if p.get("lat") is not None and p.get("lon") is not None:
                where += f" ({p['lat']:.5f}, {p['lon']:.5f})"
            if p.get("said") and p.get("said") != p.get("name"):
                where += f', named as "{p["said"]}"'
            return where

        if e.get("place"):
            p = e["place"]
            page.pair("Where it was taken", place(p) + (f", {p['km']:.1f} km from there" if p.get("km") is not None else ""))
        if e.get("places"):
            page.line("Places it names:")
            page.items([place(p) for p in e["places"]])
        if e.get("dates"):
            page.line("Dates it gives:")
            page.items([x["text"] + (f" ({x['iso']})" if x.get("iso") and x["iso"] != x["text"] else "") for x in e["dates"]])
        page.pair("Found by", e.get("by"))

    def _notes(self, page):
        hl = highlights.on(self.db, self.rid)
        cm = comments.on(self.db, self.rid)
        nt = self.db.rows(
            "SELECT account, text, t0, t1, quote, created_at FROM note WHERE recording = $r AND shared = true ORDER BY created_at",
            r=self.rid,
        )
        accounts = {x["account"] for x in [*hl, *cm, *nt] if x.get("account") is not None}
        people = (
            {
                a["id"]: a.get("name") or "someone"
                for a in self.db.rows(
                    "SELECT record::id(id) AS id, name FROM account WHERE id IN $ids", ids=[R("account", a) for a in accounts]
                )
            }
            if accounts
            else {}
        )

        def about(x):
            where = self.at(x.get("t0"), x.get("t1"))
            quote = f' "{_words(x["quote"])}"' if x.get("quote") else ""
            return f"{where}{quote}: " if where or quote else ""

        page.heading("Notes, highlights and comments")
        if not (hl or cm or nt):
            page.line("None shared.")
        if hl:
            page.line("Highlights:")
            page.items(
                [
                    f"{about(h)}{h.get('label') or h.get('colour', '') + ' highlight'} ({people.get(h.get('account'), 'someone')})"
                    for h in hl
                ]
            )
        if nt:
            page.line("Shared notes:")
            page.items(
                [
                    f"{about(n)}{_words(n['text'])} ({people.get(n['account'], 'someone')})"
                    for n in sorted(nt, key=lambda n: n.get("t0") or 0)
                ]
            )
        if cm:
            page.line("Comments:")
            page.items(
                [
                    f"{'reply: ' if c.get('parent') is not None else about(c)}{_words(c['text'])} ({people.get(c.get('account'), 'someone')}"
                    f"{', resolved' if c.get('resolved') and c.get('parent') is None else ''})"
                    for c in cm
                ]
            )


def write(db, cfg, rid, parts, d=None, places=True) -> str:
    """The text of what Lens found about the resource, for these parts."""
    return Report(db, cfg, rid, d, places).text(parts)


def has_findings(parts):
    """Whether these parts ask for any findings, beyond the file itself."""
    return any(p != "file" for p in parts)
