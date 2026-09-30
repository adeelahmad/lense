"""Text analysis: named things, keywords, sections, talk statistics and optional summaries."""
from __future__ import annotations

import json
import math
import os
import re
import urllib.request
from collections import Counter, defaultdict

from . import store

STOP = set("""a about above after again against all almost also am an and any are aren as at be because been before being
below between both but by can cannot could did do does doing down during each even ever every few for from further get gets
getting go goes going gone got had has have having he her here hers herself him himself his how i if in into is it its itself
just let lets like ll lot lots made make makes making many may me might more most much must my myself no nor not now of off
often on once one only or other our ours out over own per quite rather re really right same say says saying see she should so
some such than that the their theirs them themselves then there these they thing things think this those though through to too
under until up upon us use used using very ve want was way ways we well were what when where whether which while who whom why
will with within without would yeah yes yet you your yours yourself okay ok exactly actually basically essentially literally
honestly absolutely totally simply mean means know knows kind sort gonna gotta wanna two three first second third look looks
looking talk talking talked said tell told come comes came back take takes took give gives gave put puts something anything
everything nothing someone anyone everyone around another whole part parts point able across along already although always
among anyway become becomes behind beyond done else enough far fact great good bit big little large small long high low real
sure seems seem um uh hmm mhm oh hey hi hello thanks thank maybe probably pretty still doing alright yep yup nope don didn
doesn isn wasn weren won wouldn couldn shouldn let's i'm it's that's there's you're we're they're i've i'll i'd what's""".split())
WORD = re.compile(r"[^\W\d_][\w'’-]*")


def _stem(w):
    if len(w) > 4 and w.endswith("ies"):
        return w[:-3] + "y"
    if len(w) > 3 and w.endswith("s") and not w.endswith(("ss", "us", "is")):
        return w[:-1]
    return w


def words(text):
    out = []
    for m in WORD.finditer(text):
        w = re.sub(r"'s$", "", m.group(0).lower().replace("’", "'")).strip("'-")
        if len(w) > 2 and w not in STOP and "'" not in w:
            out.append((_stem(w), w))
    return out


# ---------- named things (rules; spaCy optional) ----------
STOPCAP = set(("I A The This That These Those It Its So And But Or If When While What Why How Who Where Which Yes No Okay Ok Right "
               "Exactly Well Now Then There Here They We You He She Our Your My His Her Their In On At For From To Of With As By Is "
               "Are Was Were Be Do Does Did Not Just Also Even Every Each Some Any All One Two Three First Second Third Let Imagine "
               "Because Despite Instead However Meaning Specifically Absolutely Precisely Essentially Crucially Honestly Oh Wow Wait "
               "Hey Sure Thank Thanks Keep Put Something Nothing Everything Anyone Someone Think Look Like Whether Though Although Yet "
               "Still Once After Before Over Under Across Against Between Into Than Most More Many Much Such Very Really Only Both "
               "Either Neither Get Got Go Going Come Plus Hi Hello Dear Great Good Sounds Perhaps Maybe Actually Basically Finally "
               "Luckily Unfortunately Fortunately Interestingly Obviously Clearly Suddenly Today Tomorrow Yesterday Never Always Often "
               "Sometimes Again Back Next Last Part Chapter Section Figure Table Step Note Mr Mrs Ms Dr Prof Yeah Yep Um Uh Hmm "
               "Alright Anyway Cool Nice Awesome Totally Definitely Correct Sorry Please").split())
ACR_STOP = {"I", "A", "OK", "AI", "TV", "PM", "AM", "US", "UK", "EU", "OMG"}
TITLES = {"Dr", "Mr", "Mrs", "Ms", "Prof", "Professor", "Sir", "Dame", "Senator", "President", "Minister"}
CONTRACTION = re.compile(r"['’](?:m|re|ve|ll|d|t)$", re.I)
CONNECT = {"of", "for", "and", "de", "la", "van", "von", "the", "&"}
ORG_END = re.compile(r"^(Inc|Ltd|Corp|Corporation|Company|Co|LLC|Group|Bank|Therapeutics|Labs?|Laboratories|University|Institute|"
                     r"Foundation|Agency|Department|Committee|Council|Association|Society|Commission|Ministry|Office|Board|"
                     r"Partners|Capital|Systems|Technologies|Holdings|Hospital|School|College)$")
TOK = re.compile(r"[^\W_](?:[\w’'&.-]*[^\W_])?")
MONTHS = "January|February|March|April|May|June|July|August|September|October|November|December"
DATE_RX = re.compile(rf"\b(?:(?:{MONTHS})\s+(?:19|20)\d{{2}}|(?:{MONTHS})\s+\d{{1,2}}(?:st|nd|rd|th)?(?:,\s*\d{{4}})?|\d{{1,2}}\s+(?:{MONTHS})(?:\s+\d{{4}})?|(?:19|20)\d{{2}})\b")
NUM_RX = re.compile(r"(?:[$€£¥]\s?)?(?<![\w.])\d[\d,]*(?:\.\d+)?(?:\s?(?:%|percent\b|percentage points?\b|×|x\b|hours?\b|days?\b|"
                    r"weeks?\b|months?\b|years?\b|pages?\b|million\b|billion\b|thousand\b|trillion\b|k\b))?")


def _base(t):
    return re.sub(r"[’']s$", "", t)


def _cap(t):
    return t[:1].isupper()


def _ver(t):
    return re.fullmatch(r"\d+(?:\.\d+)*", t) is not None


def _acr(t):
    return re.fullmatch(r"[A-Z0-9&]{2,8}", t) is not None and any(c.isupper() for c in t)


def ent_key(name):
    return re.sub(r"\s+", " ", _base(name).lower()).strip()


def parse_gazetteer(items):
    out = []
    for line in items or []:
        name, _, typ = (x.strip() for x in str(line).partition("|"))
        if name:
            pat = re.escape(name).replace(r"\ ", r"\s+")
            out.append((name, typ.upper() if typ else "TERM", re.compile(rf"(?<!\w){pat}(?!\w)", re.I)))
    return sorted(out, key=lambda g: -len(g[0]))


def extract_entities(text, gaz=()):
    out, taken = [], []

    def add(s, e, t, typ):
        if any(s < b and e > a for a, b in taken):
            return
        taken.append((s, e))
        out.append((s, t, typ))

    for _, typ, rx in gaz:
        for m in rx.finditer(text):
            add(m.start(), m.end(), m.group(0), typ)
    for m in DATE_RX.finditer(text):
        add(m.start(), m.end(), m.group(0), "DATE")
    toks = [(m.group(0), m.start(), m.end()) for m in TOK.finditer(text)]

    def initial(k):
        return k == 0 or re.search(r"[.!?:;\"“(\n]", text[toks[k - 1][2] - 1:toks[k][1]]) is not None

    k = 0
    while k < len(toks):
        if not _cap(toks[k][0]):
            k += 1
            continue
        j = k
        while j + 1 < len(toks):
            gap = text[toks[j][2]:toks[j + 1][1]]
            if re.search(r"[^\s-]", gap) and not (_base(toks[j][0]) in TITLES and re.fullmatch(r"\.\s+", gap)):
                break
            nx = toks[j + 1][0]
            if _cap(nx) or (_ver(nx) and _cap(toks[j][0])):
                j += 1
                continue
            if nx in CONNECT and j + 2 < len(toks) and _cap(toks[j + 2][0]) and not re.search(r"[^\s-]", text[toks[j + 1][2]:toks[j + 2][1]]):
                j += 2
                continue
            break
        a, b, k = k, j, j + 1
        while a <= b and ((_base(toks[a][0]) in STOPCAP and not (_base(toks[a][0]) in TITLES and a < b)) or toks[a][0] in CONNECT
                          or CONTRACTION.search(toks[a][0])):
            a += 1
        while b >= a and toks[b][0] in CONNECT:
            b -= 1
        span = toks[a:b + 1]
        if not span:
            continue
        first = _base(span[0][0])
        if len(span) == 1:
            if _acr(first):
                if first in ACR_STOP:
                    continue
            elif initial(a) or first in STOPCAP or len(first) < 3:
                continue
        s0 = span[0][1]
        name = _base(text[s0:span[-1][2]])
        last = _base(span[-1][0])
        typ = ("PERSON" if first.rstrip(".") in TITLES and len(span) > 1 else "ORG" if ORG_END.match(last)
               else "PRODUCT" if any(ch.isdigit() for x in span for ch in x[0]) and not _acr(name) else "TERM")
        add(s0, s0 + len(name), name, typ)
    for m in NUM_RX.finditer(text):
        s = m.group(0).strip()
        if re.search(r"[%$€£¥×]|percent|point|hour|day|week|month|year|page|illion|thousand|\bk\b|x$", s, re.I) or len(re.sub(r"\D", "", s)) >= 2:
            add(m.start(), m.start() + len(s), s, "NUMBER")
    out.sort()
    return [(t, typ) for _, t, typ in out]


_NLP = {}
SPACY_MAP = {"PERSON": "PERSON", "ORG": "ORG", "PRODUCT": "PRODUCT", "GPE": "PLACE", "LOC": "PLACE", "FAC": "PLACE",
             "DATE": "DATE", "EVENT": "EVENT", "WORK_OF_ART": "WORK", "LAW": "WORK", "MONEY": "NUMBER", "PERCENT": "NUMBER",
             "QUANTITY": "NUMBER", "NORP": "TERM", "LANGUAGE": "TERM"}


def spacy_entities(text, model):
    if model not in _NLP:
        import spacy
        _NLP[model] = spacy.load(model)
    return [(e.text, SPACY_MAP[e.label_]) for e in _NLP[model](text).ents if e.label_ in SPACY_MAP]


# ---------- sections (TextTiling) ----------
def _cos(a, b):
    num = sum(v * b.get(k, 0) for k, v in a.items())
    na, nb = math.sqrt(sum(v * v for v in a.values())), math.sqrt(sum(v * v for v in b.values()))
    return num / (na * nb) if na and nb else 0.0


def tiling(toks, wcounts):
    n, total = len(toks), sum(wcounts)
    if n < 12 or total < 900:
        return []
    k = max(3, min(8, round(n / 40)))

    def bag(lo, hi):
        c = Counter()
        for i in range(lo, hi):
            c.update(toks[i])
        return c

    score = [0.0] * n
    for i in range(1, n):
        score[i] = _cos(bag(max(0, i - k), i), bag(i, min(n, i + k)))
    depth = [0.0] * n
    for i in range(1, n):
        left = score[i]
        j = i
        while j - 1 >= 1 and score[j - 1] >= left:
            left, j = score[j - 1], j - 1
        right = score[i]
        j = i
        while j + 1 < n and score[j + 1] >= right:
            right, j = score[j + 1], j + 1
        depth[i] = (left - score[i]) + (right - score[i])
    mean = sum(depth[1:]) / (n - 1)
    target = max(3, min(14, round(total / 700))) - 1
    gap = max(4, round(n / (target * 2.2 + 2)))
    cands = sorted((i for i in range(2, n - 2) if depth[i] > mean and depth[i] >= depth[i - 1] and depth[i] >= depth[i + 1]),
                   key=lambda i: (-depth[i], i))
    picked = []
    for i in cands:
        if len(picked) >= target:
            break
        if i >= gap and n - i >= gap and all(abs(p - i) >= gap for p in picked):
            picked.append(i)
    return sorted(picked)


def section_titles(starts, toks, surface, seg_ents, nseg):
    tfs, df, names = [], Counter(), {}
    for k, st in enumerate(starts):
        end = starts[k + 1] if k + 1 < len(starts) else nseg
        tf = Counter()
        for i in range(st, end):
            tf.update(toks[i])
            for name, typ in seg_ents[i]:
                if typ not in ("NUMBER", "DATE"):
                    key = "§" + ent_key(name)
                    tf[key] += 1.5
                    names.setdefault(key, name)
        df.update(tf.keys())
        tfs.append(tf)
    titles = []
    for tf in tfs:
        ranked = sorted(tf.items(), key=lambda kv: (-kv[1] * math.log(1 + len(starts) / df[kv[0]]), kv[0]))
        picked = []
        for key, _ in ranked:
            label = names.get(key) or surface.get(key, key)
            if any(label.lower() in p.lower() or p.lower() in label.lower() for p in picked):
                continue
            picked.append(label)
            if len(picked) == 3:
                break
        t = ", ".join(picked)
        titles.append(t[:1].upper() + t[1:] if t else "Untitled")
    return titles


# ---------- per recording ----------
R = store.R


def talk_stats(segs):
    by, prev = defaultdict(lambda: {"talk_ms": 0, "words": 0, "turns": 0}), object()
    emo, events, langs = Counter(), Counter(), Counter()
    for s in segs:
        k = s.get("speaker")
        d, b = s["t1"] - s["t0"], by[k]
        b["talk_ms"] += d
        b["words"] += len(s["text"].split())
        if k != prev:
            b["turns"] += 1
            prev = k
        emo[s.get("emotion") or "Unknown"] += d
        if s.get("event") and s["event"] != "Speech":
            events[s["event"]] += 1
        if s.get("lang"):
            langs[s["lang"]] += 1
    spk = [{"speaker_id": k, "talk_ms": v["talk_ms"], "words": v["words"], "turns": v["turns"],
            "wpm": round(v["words"] / (v["talk_ms"] / 60000), 1) if v["talk_ms"] else 0} for k, v in by.items()]
    return {"segments": len(segs), "words": sum(v["words"] for v in by.values()), "speech_ms": sum(v["talk_ms"] for v in by.values()),
            "speakers": sorted(spk, key=lambda x: -x["talk_ms"]), "emotions": dict(emo), "events": dict(events), "languages": dict(langs)}


def analyze_recording(db, cfg, rid):
    nid = db.one("SELECT space FROM $r", r=R("recording", rid))["space"]
    segs = db.rows("SELECT record::id(id) AS id, idx, t0, t1, speaker, text, emotion, event, lang FROM segment WHERE recording = $r ORDER BY idx", r=rid)
    gaz = parse_gazetteer(cfg["analysis"].get("gazetteer"))
    use_spacy = cfg["analysis"]["entities"] == "spacy"
    seg_ents, toks, wc, tn, ts = [], [], [], Counter(), defaultdict(Counter)
    for s in segs:
        seg_ents.append(spacy_entities(s["text"], cfg["analysis"]["spacy_model"]) if use_spacy else extract_entities(s["text"], gaz))
        ws = words(s["text"])
        toks.append([w for w, _ in ws])
        wc.append(len(s["text"].split()))
        for st, sf in ws:
            tn[st] += 1
            ts[st][sf] += 1
        for (a, sa), (b, sb) in zip(ws, ws[1:]):
            tn[a + " " + b] += 1
            ts[a + " " + b][sa + " " + sb] += 1
    surface = {t: c.most_common(1)[0][0] for t, c in ts.items()}
    starts = [0] + tiling(toks, wc) if segs else []
    titles = section_titles(starts, toks, surface, seg_ents, len(segs)) if segs else []
    keys = {ent_key(n) for es in seg_ents for n, _ in es} - {""}
    alias = {k: max((L for L in keys if len(L) > len(k) and L.endswith(" " + k)), key=len, default=k) for k in keys}
    first = {}
    for es in seg_ents:
        for name, typ in es:
            first.setdefault(alias.get(ent_key(name), ent_key(name)), (name, typ))
    known = {r["key"]: r["id"] for r in db.rows("SELECT record::id(id) AS id, key FROM entity WHERE space = $s AND key IN $k",
                                                  s=nid, k=sorted(first))} if first else {}
    if first:  # names merged into another entity keep pointing at it
        known.update({r["key"]: r["entity"] for r in db.rows("SELECT key, entity FROM entity_alias WHERE space = $s AND key IN $k", s=nid, k=sorted(first))})
    for key, (name, typ) in first.items():
        if key and key not in known:
            known[key] = db.next_id("entity")
            db.q("CREATE $r CONTENT $d", r=R("entity", known[key]), d={"space": nid, "key": key, "ekey": f"{nid}:{key}", "name": name, "type": typ})
    over = {(o["segment"], o["key"]): o["target"] for o in db.rows("SELECT segment, key, target FROM entity_override WHERE recording = $r", r=rid)}
    ments = []
    for s, es in zip(segs, seg_ents):
        for n, _ in es:
            k = ent_key(n)
            if not k:
                continue
            eid = over.get((s["id"], k), known[alias.get(k, k)])  # people's corrections outrank the extractor
            if eid:
                ments.append(store.clean({"in": R("segment", s["id"]), "out": R("entity", eid), "recording": rid, "space": nid, "entity": eid,
                                          "speaker": s.get("speaker"), "text": n}))
    terms = [{"recording": rid, "space": nid, "term": t, "surface": surface[t], "n": n} for t, n in tn.items() if " " not in t or n >= 2]
    secs = []
    for k, st in enumerate(starts):
        end = starts[k + 1] if k + 1 < len(starts) else len(segs)
        secs.append({"recording": rid, "idx": k, "seg0": st, "seg1": end,
                     "t0": segs[st]["t0"], "t1": segs[end - 1]["t1"], "title": titles[k]})
    stmts = ["DELETE mentions WHERE recording = $rid", "DELETE term WHERE recording = $rid", "DELETE section WHERE recording = $rid"]
    stmts += (["INSERT INTO term $terms"] if terms else []) + (["INSERT INTO section $secs"] if secs else [])
    db.run(stmts, rid=rid, terms=terms, secs=secs)
    if ments:
        try:
            db.run(["INSERT RELATION INTO mentions $ments"], ments=ments)
        except Exception:  # noqa: BLE001 - servers without INSERT RELATION: one edge at a time
            for m in ments:
                db.q("RELATE $a->mentions->$b CONTENT $d", a=m["in"], b=m["out"], d={k: v for k, v in m.items() if k not in ("in", "out")})
    db.q("UPDATE $r SET stats = $st, status = 'analyzed', analyzed_at = $t", r=R("recording", rid), st=talk_stats(segs), t=store.now())


def analyze_pending(db, cfg, ns=None, limit=0, force=False, log=print):
    where = "status IN ['transcribed', 'diarized', 'analyzed']" if force else "status IN ['transcribed', 'diarized']"
    if ns:
        where += " AND space = $s"
    rows = db.rows(f"SELECT record::id(id) AS id, title FROM recording WHERE {where} ORDER BY id", s=store.ns_id(db, ns, create=False) if ns else None)
    rows = rows[:limit or None]
    for r in rows:
        analyze_recording(db, cfg, r["id"])
        log(f"  {r['title']}: analysed")
    return len(rows)


# ---------- keywords (for word clouds) ----------
def keywords(db, rid, top=60):
    nid = db.one("SELECT space FROM $r", r=R("recording", rid))["space"]
    N = len(db.rows("SELECT recording FROM term WHERE space = $s GROUP BY recording", s=nid))
    rows = [r for r in db.rows("SELECT term, surface, n FROM term WHERE recording = $r", r=rid) if r["n"] >= 2 or N <= 1]
    if not rows:
        return []
    df = {x["term"]: x["df"] for x in db.rows("SELECT term, count() AS df FROM term WHERE space = $s AND term IN $t GROUP BY term",
                                              s=nid, t=[r["term"] for r in rows])}
    scored = [(r["surface"], (1 + math.log(r["n"])) * (math.log((N + 1) / (df.get(r["term"], 1) + 1)) + 1) * (1.3 if " " in r["term"] else 1))
              for r in rows]
    return _dedupe(sorted(scored, key=lambda x: -x[1]), top)


def ns_keywords(db, nid, top=80):
    multi = len(db.rows("SELECT space FROM term GROUP BY space")) > 1
    N = len(db.rows("SELECT recording FROM term GROUP BY recording")) or 1
    rows = [r for r in db.rows("SELECT term, math::sum(n) AS n, count() AS dfn FROM term WHERE space = $s GROUP BY term", s=nid) if r["n"] >= 3]
    if not rows:
        return []
    names = [r["term"] for r in rows]
    surf = {}
    for r in db.rows("SELECT term, surface FROM term WHERE space = $s AND term IN $t", s=nid, t=names):
        surf.setdefault(r["term"], r["surface"])
    dfg = {x["term"]: x["dfg"] for x in db.rows("SELECT term, count() AS dfg FROM term WHERE term IN $t GROUP BY term", t=names)} if multi else {}
    scored = [(surf.get(r["term"], r["term"]), math.log(1 + r["n"]) * ((math.log((N + 1) / (dfg.get(r["term"], 1) + 1)) + 1) if multi
              else math.sqrt(r["dfn"])) * (1.3 if " " in r["term"] else 1)) for r in rows]
    return _dedupe(sorted(scored, key=lambda x: -x[1]), top)


def _dedupe(scored, top):
    out, seen = [], set()
    for word, s in scored:
        if word in seen or (len(word.split()) == 1 and any(word in o.split() for o, _ in out[:10])):
            continue
        seen.add(word)
        out.append((word, round(s, 3)))
        if len(out) == top:
            break
    return out


# ---------- optional summaries through any OpenAI-compatible server ----------
SUMMARY_SCHEMA = {"type": "object", "additionalProperties": False,
                  "required": ["summary", "topics", "action_items", "people", "sentiment", "importance"],
                  "properties": {"summary": {"type": "string"}, "topics": {"type": "array", "items": {"type": "string"}},
                                 "action_items": {"type": "array", "items": {"type": "string"}},
                                 "people": {"type": "array", "items": {"type": "string"}},
                                 "sentiment": {"type": "string", "enum": store.EMOTIONS}, "importance": {"type": "integer"}}}
SUMMARY_SYSTEM = ("You summarise one recorded conversation for a searchable archive. Use only what the transcript says. "
                  "summary: at most 120 words. topics: up to 6 short noun phrases. action_items: follow-ups or commitments "
                  "stated in the conversation, naming who when said; empty if none. people: people mentioned by name. "
                  "sentiment: the overall emotional tone, one of " + ", ".join(store.EMOTIONS) + ". importance: 1 (routine) "
                  "to 5 (critical). Reply with JSON only.")


def _llm(cfg, user):
    l = cfg["llm"]
    body = {"model": l["model"], "temperature": 0, "messages": [{"role": "system", "content": SUMMARY_SYSTEM},
                                                                 {"role": "user", "content": user}],
            "response_format": {"type": "json_schema", "json_schema": {"name": "summary", "strict": True, "schema": SUMMARY_SCHEMA}}}
    headers = {"Content-Type": "application/json"}
    key = l.get("api_key") or (os.environ.get(l["api_key_env"]) if l.get("api_key_env") else None)
    if key:
        headers["Authorization"] = "Bearer " + key
    req = urllib.request.Request(l["base_url"].rstrip("/") + "/chat/completions", data=json.dumps(body).encode(), headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=l.get("timeout", 300)) as r:
        text = json.loads(r.read().decode())["choices"][0]["message"]["content"]
    text = re.sub(r"^```(?:json)?|```$", "", text.strip()).strip()
    obj = json.loads(text[text.find("{"):text.rfind("}") + 1])
    missing = [k for k in SUMMARY_SCHEMA["required"] if k not in obj]
    if missing:
        raise ValueError(f"summary missing {missing}")
    obj["sentiment"] = store.norm_emotion(obj["sentiment"]) or "Neutral"
    obj["importance"] = max(1, min(5, int(obj["importance"])))
    return obj


def summarize_recording(db, cfg, rid):
    names = {r["id"]: r.get("name") or r["label"] for r in db.rows(
        "SELECT record::id(id) AS id, name, label FROM speaker WHERE space = $s", s=db.one("SELECT space FROM $r", r=R("recording", rid))["space"])}
    lines = [f"[{store.tc(r['t0'])}] {names.get(r.get('speaker'), 'Speaker')}: {r['text']}"
             for r in db.rows("SELECT idx, t0, text, speaker FROM segment WHERE recording = $r ORDER BY idx", r=rid)]
    chunks, cur = [], ""
    for line in lines:
        if cur and len(cur) + len(line) > cfg["llm"]["max_chars"]:
            chunks.append(cur)
            cur = ""
        cur += line + "\n"
    chunks.append(cur)
    parts = [_llm(cfg, ("Part %d of %d of the transcript:\n\n" % (i + 1, len(chunks)) if len(chunks) > 1 else "Transcript:\n\n") + c)
             for i, c in enumerate(chunks)]
    out = parts[0] if len(parts) == 1 else _llm(cfg, "Combine these summaries of consecutive parts of one conversation into one:\n\n" + json.dumps(parts))
    db.q("UPDATE $r SET summary = $s, summarized_at = $t", r=R("recording", rid), s=out, t=store.now())
    return out


def summarize_pending(db, cfg, ns=None, limit=0, force=False, log=print):
    if not (cfg["llm"].get("base_url") and cfg["llm"].get("model")):
        log("  no llm.base_url/model configured; summaries skipped")
        return 0
    where = "status = 'analyzed'" + ("" if force else " AND summarized_at = NONE") + (" AND space = $s" if ns else "")
    done = 0
    for r in db.rows(f"SELECT record::id(id) AS id, title FROM recording WHERE {where} ORDER BY id", s=store.ns_id(db, ns, create=False) if ns else None)[:limit or None]:
        try:
            summarize_recording(db, cfg, r["id"])
            done += 1
            log(f"  {r['title']}: summarised")
        except Exception as e:  # noqa: BLE001
            log(f"  {r['title']}: summary failed ({type(e).__name__}: {e})")
    return done
