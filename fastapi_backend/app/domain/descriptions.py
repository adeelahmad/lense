"""Descriptions of what's seen (docs/api.md#descriptions): a model that can see images, the one an admin chose as
llm.vision_model, says in a few sentences what each of a document's or an image's pages shows, and each of a video's
shots (from its keyframe), so that they can be searched and read by people who can't see them. Without such a model
the describe step is skipped, saying why."""

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
    text = llm.chat(cfg, [{"role": "system", "content": PROMPT}, {"role": "user", "content": content}], model=model)
    return re.sub(r"\s+", " ", text or "").strip()[:MAX_CHARS]


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
    model, why = model_why(cfg)
    if not model:
        raise jobs.Skip(why)
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
                text = describe(cfg, model, pic, about)
        except (llm.LLMError, OSError) as e:
            failed.append(str(e))
            continue
        if text:
            rows.append(store.clean({"recording": rid, "space": rec["space"], **it, "paged": paged or None, "text": text, "model": model}))
    if failed and not rows:
        raise llm.LLMError(f"{model} couldn't describe its {'pages' if paged else 'shots'}: {failed[0]}")
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
