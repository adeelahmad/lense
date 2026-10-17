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

Punctuation (`analysis.cleanup_punctuation`, off by default): a transcript with hardly a full stop in it (imported
captions, some engines) gets full stops, commas and question marks back from a local model, kredor/punctuate-all
(MIT, XLM-RoBERTa, 12 languages), the one the punctuation script this comes from used. "missing" does it for those
transcripts only, "always" for every one; either way a mark the transcript has is kept, and each line is given marks
once. The model and PyTorch (about 2 GB) are fetched the first time a clean step needs them (components.py); until
then, or where it can't run, the rules run alone.

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


# ---------- punctuation, by a local model ----------
PUNCTUATION_MODEL = ("kredor/punctuate-all", "0fe37019de3f5e4fbd83289fd94e07fa588e47df")  # pinned: MIT, 12 languages
PUNCTUATION_FILES = ["config.json", "pytorch_model.bin", "special_tokens_map.json", "tokenizer.json", "tokenizer_config.json"]
PUNCTUATION_LANGS = {"en", "de", "fr", "es", "bg", "it", "pl", "nl", "cs", "pt", "sk", "sl"}
PUNCTUATED = f"{PUNCTUATION_MODEL[0]}@{PUNCTUATION_MODEL[1][:12]}"  # a line the model has given marks to
SURE = 0.7  # a mark the model is less sure of than this isn't added (the script it comes from used 0.7 too)
CHUNK, OVERLAP = 150, 5  # words the model reads at once (well inside its 512 tokens), and how many chunks share
OWN_MARK = re.compile(r"[.,;:!?…]['\"”’)]*$")
SENTENCE_END = re.compile(r"[.!?…]['\"”’)]*$")
NUMBER_MARK = re.compile(r"(?<!\d)[.,;:!?](?!\d)")  # marks the model is shown without (not 3.5 or 1,000)
_PIPE: dict = {}


class Unavailable(RuntimeError):
    """The punctuation model can't run here (yet); the message says why."""


def fetch_punctuation():
    """Download the model at its pinned revision into the models folder (Hugging Face's cache there)."""
    from huggingface_hub import snapshot_download

    name, rev = PUNCTUATION_MODEL
    return snapshot_download(name, revision=rev, allow_patterns=PUNCTUATION_FILES)


def punctuation_ready():
    try:
        from huggingface_hub import snapshot_download

        name, rev = PUNCTUATION_MODEL
        snapshot_download(name, revision=rev, allow_patterns=PUNCTUATION_FILES, local_files_only=True)
        return True
    except Exception:  # noqa: BLE001 - not installed, or not downloaded
        return False


def _pipe():
    if "pipe" not in _PIPE:
        try:
            from huggingface_hub import snapshot_download
            from transformers import pipeline

            name, rev = PUNCTUATION_MODEL
            path = snapshot_download(name, revision=rev, allow_patterns=PUNCTUATION_FILES, local_files_only=True)
            _PIPE["pipe"] = pipeline("token-classification", model=path, aggregation_strategy="none", device=-1)
        except Exception as e:  # noqa: BLE001 - not fetched yet, or it won't load here
            raise Unavailable(f"the punctuation model isn't ready here ({type(e).__name__}; Settings → Components)") from None
    return _PIPE["pipe"]


def _labels(pipe, words):
    """The model's mark after each word ("0": none), read CHUNK words at a time with OVERLAP words shared, as
    deepmultilingualpunctuation does: a word's last piece decides, and a mark it's less than SURE of counts as none."""
    out, k = [], 0
    while k < len(words):
        batch = words[k : k + CHUNK]
        keep = len(batch) if k + CHUNK >= len(words) else len(batch) - OVERLAP
        ends, at = [], 0
        for w in batch:
            at += len(w)
            ends.append(at)
            at += 1
        marks, j = ["0"] * len(batch), 0
        for piece in pipe(" ".join(batch)):
            while j < len(ends) and piece["end"] > ends[j]:
                j += 1
            if j < len(ends):
                marks[j] = piece["entity"] if float(piece["score"]) >= SURE else "0"
        out += marks[:keep]
        k += keep
    return out


def punctuate(texts):
    """The texts with the full stops, commas and question marks the model puts back after words that have no mark;
    the marks they have are kept."""
    pipe = _pipe()
    words = [t.split() for t in texts]
    flat = [NUMBER_MARK.sub("", w) or w for ws in words for w in ws]
    labels = _labels(pipe, flat) if flat else []
    out, k = [], 0
    for ws in words:
        line = [w + m if m in (".", ",", "?") and not OWN_MARK.search(w) else w for w, m in zip(ws, labels[k : k + len(ws)])]
        out.append(" ".join(line))
        k += len(ws)
    return out


def lacks_punctuation(texts):
    """Hardly a sentence's end in it: fewer than one in 40 words."""
    ws = [w for t in texts for w in t.split()]
    return len(ws) >= 20 and sum(1 for w in ws if SENTENCE_END.search(w)) * 40 < len(ws)


def _punctuation(cfg, rec, segs):
    """(the lines' texts with marks put back, the ids of the lines given marks, a note for the job's log)."""
    mode = (cfg.get("analysis") or {}).get("cleanup_punctuation") or "off"
    texts = [s.get("text") or "" for s in segs]
    todo = [k for k, s in enumerate(segs) if s.get("punctuated") != PUNCTUATED and texts[k].strip()]
    lang = str(rec.get("language") or "").lower()[:2]
    known = lang in PUNCTUATION_LANGS or (not lang and english(None, texts[:400]))
    if mode == "off" or not todo or not known or (mode == "missing" and not lacks_punctuation(texts)):
        return texts, [], None
    try:
        marked = punctuate(texts)
    except Unavailable as e:
        return texts, [], f"punctuation not restored: {e}"
    for k in todo:
        texts[k] = marked[k]
    return texts, [segs[k]["id"] for k in todo], f"punctuation restored by {PUNCTUATION_MODEL[0]}"


# ---------- an asset's lines ----------
def plan(db, cfg, rid):
    """What cleaning an asset does: {changes: [(segment id, idx, text, cleaned)], punctuated: [segment ids], note}.
    Nothing for a document or an image."""
    rec = db.one("SELECT source, language FROM $r", r=R("recording", rid)) or {}
    if rec.get("source") not in SPEECH:
        return {"changes": [], "punctuated": [], "note": None}
    segs = db.rows(
        "SELECT record::id(id) AS id, idx, text, lang, speaker, punctuated FROM segment WHERE recording = $r ORDER BY idx", r=rid
    )
    people = set(db.values("SELECT VALUE idx FROM segment_edit WHERE recording = $r AND by != $c AND after.text != NONE", r=rid, c=CLEANER))
    fillers = bool((cfg.get("analysis") or {}).get("cleanup_fillers", True))
    whole = english(rec.get("language"), [s.get("text") or "" for s in segs[:400]])
    marked, punctuated, note = _punctuation(cfg, rec, segs)
    out, prev = [], None
    for s, text in zip(segs, marked):
        ended = prev is None or prev["speaker"] != s.get("speaker") or SENTENCE_END.search(prev["text"]) is not None
        prev = {"speaker": s.get("speaker"), "text": text}
        if s["idx"] in people or not text.strip():
            continue
        lang = s.get("lang")
        en = english(lang, [text]) if lang and lang not in ("auto", "nospeech") else whole
        new = clean_line(text, en, fillers, ended)
        if new and new != (s.get("text") or ""):
            out.append((s["id"], s["idx"], s.get("text") or "", new))
            prev["text"] = new
    kept = {s["id"] for s in segs if s["idx"] in people}
    return {"changes": out, "punctuated": [i for i in punctuated if i not in kept], "note": note}


def clean_recording(db, cfg, rid, say=None):
    """Clean an asset's transcript lines; how many changed. Each change goes into its edit history."""
    p = plan(db, cfg, rid)
    if p["note"] and say:
        say(p["note"])
    changes = p["changes"]
    if p["punctuated"]:
        db.q("UPDATE segment SET punctuated = $m WHERE id IN $ids", m=PUNCTUATED, ids=[R("segment", i) for i in p["punctuated"]])
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
