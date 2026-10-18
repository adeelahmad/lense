"""Descriptions of what's seen (docs/api.md#descriptions): a model that can see images says in a few sentences what
each of a document's or an image's pages shows, and each of a video's shots (from its keyframe), so that they can be
searched and read by people who can't see them. Which model is llm.describe_engine: "llm", the LLM provider's model an
admin chose as llm.vision_model (the default); or, on this machine, needing no provider and sending no picture
anywhere, "florence-2", Florence-2 (florence.py), or "moondream", Moondream (moondream.py). Without one the describe
step is skipped, saying why."""

from __future__ import annotations

import base64
import io
import re

from . import jobs, keyring, llm, store

R = store.R
PROMPT = (
    "You describe pictures for an archive's catalogue, so that they can be found by searching and understood by people "
    "who can't see them. In two to four plain sentences, say what this one shows: the setting, the people (by what "
    "they're doing and wearing; never guess who they are), the things in it, and any text you can read, quoted. Say "
    "only what can be seen. No preamble."
)
LONGEST = 1024  # pixels on a picture's longest side, as it's sent
MAX_CHARS = 1500  # of a description kept


# what can describe pictures (llm.describe_engine), as agents are told
ENGINES = {
    "llm": "the LLM provider's model that can see images (llm.vision_model): each picture is a request to it",
    "florence-2": "Florence-2 (MIT) on this server's CPU: no picture leaves it and no provider is called; base is about "
    "280 MB, large about 830 MB, fetched the first time a job needs it",
    "moondream": "Moondream on this server, through PyTorch: no picture leaves it; moondream3-preview is Moondream 3 "
    "(BSL 1.1, not open source; about 19 GB, needs an NVIDIA GPU or a Mac's GPU), moondream2 is Moondream 2 "
    "(Apache-2.0, about 3.7 GB, runs on a CPU, slowly); fetched the first time a job needs it",
}


class Failed(RuntimeError):
    """A model on this machine couldn't describe a picture."""


def model_why(cfg):
    """The model that describes pictures, and None; or None, and why there's none."""
    if not llm.configured(cfg):
        return None, "no LLM is configured"
    model = (cfg["llm"].get("vision_model") or "").strip()
    if not model:
        return None, "no model that can see images is chosen (llm.vision_model)"
    return model, None


def picture(path, longest=LONGEST):
    """A picture as a model is sent it: a JPEG data URI, at most `longest` pixels on a side."""
    from PIL import Image

    with Image.open(path) as im:
        im = im.convert("RGB")
        im.thumbnail((longest, longest))
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=85)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def describe(cfg, model, path, about):
    """What the model says the picture at `path` shows; `about` says what it is (page 3 of …)."""
    content = [{"type": "text", "text": about}, {"type": "image_url", "image_url": {"url": picture(path)}}]
    return llm.chat(cfg, [{"role": "system", "content": PROMPT}, {"role": "user", "content": content}], model=model)


def _tidy(text):
    """A description as it's kept: its spaces made single, at most MAX_CHARS."""
    return re.sub(r"\s+", " ", text or "").strip()[:MAX_CHARS]


class Provider:
    """The LLM provider's model that can see images (llm.vision_model)."""

    def __init__(self, cfg, model):
        self.cfg, self.name = cfg, model

    def describe(self, path, about):
        return describe(self.cfg, self.name, path, about)


def engine(cfg):
    """(what describes pictures, None), or (None, why there's none): its `name` and `describe(path, about)`."""
    which = cfg["llm"].get("describe_engine") or "llm"
    if which in ("florence-2", "moondream"):
        from . import florence, moondream

        try:
            return (florence.Florence(cfg) if which == "florence-2" else moondream.Moondream(cfg)), None
        except RuntimeError as e:
            return None, str(e)
    model, why = model_why(cfg)
    return (Provider(cfg, model), None) if model else (None, why)


def status(cfg):
    """What describes pictures: the engine and its model, whether it's ready (and why not), the settings, and the
    engines there are. Nothing is loaded to say so."""
    from . import florence, moondream

    l = cfg["llm"]
    which = l.get("describe_engine") or "llm"
    later = "it isn't here yet: it's fetched the first time a job waits to describe something"
    if which == "florence-2":
        model = florence.name(florence.size(cfg))
        why = None if florence.ready(cfg) else later
    elif which == "moondream":
        model = moondream.name(moondream.choice(cfg))
        why = moondream.cannot_run(cfg) or (None if moondream.ready(cfg) else later)
    else:
        model, why = model_why(cfg)
    return {
        "engine": which,
        "model": model,
        "ready": why is None,
        "why_not": why,
        "vision_model": l.get("vision_model"),
        "florence_model": florence.size(cfg),
        "moondream_model": moondream.choice(cfg),
        "describe_max": int(l.get("describe_max") or 50),
        "engines": ENGINES,
    }


def _clock(ms):
    s = int(ms or 0) // 1000
    return f"{s // 3600}:{s // 60 % 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60}:{s % 60:02d}"


def step_describe(db, cfg, rid, say):
    """The describe step: a description of each page of a document or an image, or of each shot of a video."""
    from . import documents, video

    rec = db.one("SELECT space, source, media, title FROM $r", r=R("recording", rid)) or {}
    paged = rec.get("source") in ("document", "image")
    if not paged and (rec.get("media") or {}).get("kind") != "video":
        raise jobs.Skip("it isn't a video, a document or an image")
    if paged:
        items = [
            {"idx": p["idx"], "t0": p["idx"], "t1": p["idx"] + 1, "frame": p["image"]} for p in documents.pages(db, rid) if p.get("image")
        ]
        if not items:
            raise jobs.Skip("its pages weren't drawn")
    else:
        shots = db.rows("SELECT idx, t0, t1, frame FROM shot WHERE recording = $r ORDER BY idx", r=rid)
        items = [{"idx": s["idx"], "t0": s["t0"], "t1": s["t1"], "frame": s["frame"]} for s in shots if s.get("frame")]
        if not items:
            raise jobs.Skip("it has no shots yet (the shots step finds them)")
    eye, why = engine(cfg)
    if not eye:
        raise jobs.Skip(why)
    model = eye.name
    most = int(cfg["llm"].get("describe_max") or 50)
    title, d, rows, failed = rec.get("title") or "untitled", video.frames_dir(cfg, rid), [], []
    for it in items[:most]:
        if rec["source"] == "image":
            about = f"The image “{title}”" + (f", page {it['idx'] + 1}." if len(items) > 1 else ".")
        elif paged:
            about = f"Page {it['idx'] + 1} of the document “{title}”."
        else:
            about = f"The first frame of a shot in the video “{title}”, at {_clock(it['t0'])}."
        try:
            with keyring.plain_picture(db, cfg, d / it["frame"]) as pic:
                text = _tidy(eye.describe(pic, about))
        except (llm.LLMError, OSError, Failed) as e:
            failed.append(str(e))
            continue
        if text:
            rows.append(store.clean({"recording": rid, "space": rec["space"], **it, "paged": paged or None, "text": text, "model": model}))
    if failed and not rows:
        why = f"{model} couldn't describe its {'pages' if paged else 'shots'}: {failed[0]}"
        raise llm.LLMError(why) if isinstance(eye, Provider) else Failed(why)
    db.run(["DELETE description WHERE recording = $r", *(["INSERT INTO description $rows"] if rows else [])], r=rid, rows=rows)
    what = "page(s)" if paged else "shot(s)"
    note = f"{len(rows)} {what} described ({model})"
    if len(items) > most:
        note += f"; the {len(items) - most} after the first {most} weren't (llm.describe_max)"
    if failed:
        note += f"; {len(failed)} couldn't be: {failed[0][:200]}"
    say(note)


def for_recording(db, rid):
    """A resource's descriptions, in the order of its pages or shots."""
    return db.rows("SELECT idx, t0, t1, frame, paged, text, model FROM description WHERE recording = $r ORDER BY idx", r=rid)
