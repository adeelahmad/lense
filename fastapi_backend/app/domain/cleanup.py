"""Cleaning up a transcript before analysis: the clean step (Settings → Analysis → Clean up transcripts).

Speech comes out of transcription as it was said: "um" and "uh", stutters ("I I I looked"), a lower-case "i", stray
spaces before commas, sentences that start in lower case. The extractor, the chapters, the keywords and search all
read that text, so the clean step tidies each line first, with rules: no model, nothing to fetch, nothing sent out.

- repeated words: a word said three times or more in a row is kept once, and a doubled pronoun, article or
  conjunction ("the the", "I, I") once. "had had", "that that" and "very very" stay as they are.
- fillers (`analysis.cleanup_fillers`, on with cleanup): "um", "uh", "erm" and "hmm" go, with the comma after them.
- spacing: one space between words, none before a comma or a full stop, one after a comma.
- capitals: "i" is "I", and each sentence starts with a capital (a line that carries on the one before doesn't).

The rules are English ones ("um" is a word in German and Portuguese), so they run on English lines: the line's or the
asset's language when transcription gave one, else when the text reads as English. Other languages only get a word
said three times or more in a row kept once, and their sentences capitalised. A document's or an image's text is kept
as it was read.

Every line it changes is kept in the asset's edit history, by "text cleanup": Change history lists them and Revert
puts a line back. A line someone corrected is never cleaned, since people's corrections outrank the rules, and running
the rules again on what they made changes nothing.
"""

from __future__ import annotations

import re

from . import store

R = store.R
CLEANER = "text cleanup"  # who the edit history says changed a line
SPEECH = ("audio", "transcript")  # documents and images keep their text as read
FILLERS = {"um", "umm", "uh", "uhh", "uhm", "erm", "er", "hmm"}
# said twice in a row these are nearly always a stumble; had, that, is, very and so are often doubled on purpose
# ("what it is is", "very very good")
DOUBLED = set(
    """i i'm i've i'll i'd a an the and but or to of in on at for with we you he she it they my our your their this
    if it's we're you're they're there's""".split()
)
MARKERS = {"okay", "ok", "yeah", "yes", "no", "well", "so", "right", "oh", "alright", "anyway", "actually", "hey", "hi", "sorry"}
ENGLISH = set(
    """the and to of a in is it you that i was for on are with as be this have not but they at we what so or if can
    all there my your do just about he she one would like get will an by me from how know think yeah okay""".split()
)
ABBREV = {"mr", "mrs", "ms", "dr", "prof", "st", "vs", "etc", "e.g", "i.e", "approx", "no", "fig", "inc", "ltd", "jr", "sr"}
WORD = re.compile(r"[^\W\d_][\w'’]*")
SENTENCE = re.compile(r"(?<![.…])([.!?])(\s+)([^\W\d_])")
STRAY = re.compile(r"\s+([,.;!?])(?=\s|$)")
AFTER_COMMA = re.compile(r"([,;])(?=[^\W\d_])")
I_ALONE = re.compile(r"(?<![\w'’-])i(?=$|[\s,.;:!?]|['’](?:m|ve|ll|d)\b)")
OWN_END = re.compile(r"(?<![.…])([.!?])$")  # a token's own full stop, question or exclamation mark (not "um...")
FIRST = re.compile(r"[\"“'‘(]*([^\W\d_])")
LINK = re.compile(r"[\w.+-]+@|https?:|www\.")


def _bare(token):
    return token.strip(",.;:!?-—–…\"“”'‘’()").lower().replace("’", "'")


def english(lang, texts):
    """Whether the English rules apply: to the language transcription gave, else to text that reads as English."""
    if lang and str(lang).lower() not in ("auto", "nospeech", "unknown", "none"):
        return str(lang).lower().startswith("en")
    ws = [w.lower() for t in texts for w in WORD.findall(t)][:3000]
    return len(ws) >= 3 and sum(w in ENGLISH for w in ws) / len(ws) >= 0.2


def _repeats(tokens, doubled):
    """A word three times or more in a row once; twice in a row once too when it's one of `doubled`."""
    out, k = list(tokens), 0
    while k < len(out):
        w = _bare(out[k])
        j = k
        while w and j + 1 < len(out) and _bare(out[j + 1]) == w:
            j += 1
        if j - k >= 2 or (j - k == 1 and w in doubled):
            last = out[j]
            if out[k][:1].isupper() and not last[:1].isupper():  # "I i think", "The the"
                last = last[:1].upper() + last[1:]
            out[k : j + 1] = [last]
        k += 1
    return out


def _is_filler(token):
    w = _bare(token)
    core = token.strip(",.;:!?-—–…")
    return w in FILLERS and not (core.isupper() and len(core) > 1) and re.fullmatch(r"[\w'’]+", core) is not None


def _fillers(tokens):
    """Without "um" and "uh": a sentence's end the filler carried moves to the word before, and a comma before it goes
    with it unless it follows a word like "okay" or "well"."""
    out, k = list(tokens), 0
    while k < len(out):
        if not _is_filler(out[k]):
            k += 1
            continue
        t = out.pop(k)
        if not k:
            continue
        prev, m = out[k - 1], OWN_END.search(t)
        if m and prev[-1] not in ".!?":
            out[k - 1] = prev.rstrip(",;:") + m.group(1)
        elif t.endswith(",") and prev.endswith(",") and _bare(prev) not in MARKERS:  # "was, uh, there"; "Okay, um, so"
            out[k - 1] = prev[:-1]
    return out


def _sentences(text):
    """Each sentence starting with a capital, past abbreviations ("Dr. Smith", "e.g. this")."""

    def up(m):
        before = text[: m.start()].rsplit(None, 1)
        word = before[-1].lower().rstrip(".") if before else ""
        if m.group(1) == "." and (word in ABBREV or len(word) == 1):
            return m.group(0)
        return m.group(1) + m.group(2) + m.group(3).upper()

    return SENTENCE.sub(up, text)


def clean_line(text, english_rules=True, fillers=True, starts_sentence=True):
    """A transcript line as the rules leave it. `starts_sentence`: the line before ended a sentence (or there's none)."""
    tokens = text.split()
    if english_rules:
        if fillers:
            tokens = _fillers(tokens)
        tokens = _repeats(tokens, DOUBLED)
    else:
        tokens = _repeats(tokens, ())
    out = " ".join(tokens)
    if english_rules:
        out = AFTER_COMMA.sub(r"\1 ", STRAY.sub(r"\1", out))
        out = I_ALONE.sub("I", out)
    out = _sentences(out)
    m = FIRST.match(out)  # the line's first word, past an opening quote or bracket
    if starts_sentence and m and m.group(1).islower() and not LINK.match(out[m.start(1) :]):
        out = out[: m.start(1)] + m.group(1).upper() + out[m.end(1) :]
    return out.strip()


def plan(db, cfg, rid):
    """[(segment id, idx, text, cleaned)] for the lines the rules change; [] for a document or an image."""
    rec = db.one("SELECT source, language FROM $r", r=R("recording", rid)) or {}
    if rec.get("source") not in SPEECH:
        return []
    segs = db.rows("SELECT record::id(id) AS id, idx, text, lang, speaker FROM segment WHERE recording = $r ORDER BY idx", r=rid)
    people = set(db.values("SELECT VALUE idx FROM segment_edit WHERE recording = $r AND by != $c AND after.text != NONE", r=rid, c=CLEANER))
    fillers = bool((cfg.get("analysis") or {}).get("cleanup_fillers", True))
    whole = english(rec.get("language"), [s.get("text") or "" for s in segs[:400]])
    out, prev = [], None
    for s in segs:
        text = s.get("text") or ""
        ended = prev is None or prev["speaker"] != s.get("speaker") or re.search(r"[.!?…]['\"”’)]*$", prev["text"]) is not None
        prev = {"speaker": s.get("speaker"), "text": text}
        if s["idx"] in people or not text.strip():
            continue
        lang = s.get("lang")
        en = english(lang, [text]) if lang and lang not in ("auto", "nospeech") else whole
        new = clean_line(text, en, fillers, ended)
        if new and new != text:
            out.append((s["id"], s["idx"], text, new))
            prev["text"] = new
    return out


def clean_recording(db, cfg, rid):
    """Clean an asset's transcript lines; how many changed. Each change goes into its edit history."""
    changes = plan(db, cfg, rid)
    if not changes:
        return 0
    last = int(db.values("UPSERT $r SET n += $k RETURN VALUE n", r=R("seq", "segment_edit"), k=len(changes))[0])
    t, first = store.now(), last - len(changes) + 1
    ups = [{"id": R("segment", sid), "text": new} for sid, _, _, new in changes]
    edits = [
        {"recording": rid, "idx": idx, "before": {"text": old}, "after": {"text": new}, "by": CLEANER, "at": t, "n": first + k}
        for k, (_, idx, old, new) in enumerate(changes)
    ]
    for a in range(0, len(ups), 500):
        db.run(
            ["FOR $u IN $ups { UPDATE $u.id SET text = $u.text; }", "INSERT INTO segment_edit $edits"],
            ups=ups[a : a + 500],
            edits=edits[a : a + 500],
        )
    return len(changes)


def clean_pending(db, cfg, ns=None, limit=0, force=False, log=print):
    """The command line's clean: transcripts not analysed yet (all of them with force)."""
    where = "status IN ['transcribed', 'diarized', 'analyzed']" if force else "status IN ['transcribed', 'diarized']"
    where += " AND source IN $src"
    if ns:
        where += " AND space = $s"
    rows = db.rows(
        f"SELECT record::id(id) AS id, title FROM recording WHERE {where} ORDER BY id",
        src=list(SPEECH),
        s=store.ns_id(db, ns, create=False) if ns else None,
    )
    rows = rows[: limit or None]
    for r in rows:
        log(f"  {r['title']}: {clean_recording(db, cfg, r['id'])} line(s) cleaned")
    return len(rows)
