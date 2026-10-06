"""Objects in pictures (docs/api.md#objects): the people, cars, dogs, chairs … (the 80 kinds of the COCO dataset) on a
video's sampled frames, a document's pages and an image, found by a detector and kept per kind with where they are.

Engines (video.object_engine):
- yolox, the default: YOLOX on ONNX Runtime (both Apache-2.0). Its model is a YOLOX .onnx file: video.yolox_model, or
  one in MODELS or the data folder's models (components.py fetches yolox_s there on first use); `pip install "lens[objects]"` brings ONNX Runtime.
- ultralytics: Ultralytics YOLO (video.ultralytics_model, such as yolov8n.pt). It's AGPL-3.0: a server that lets
  others use it must offer them its source, so it's in no image or extra; `pip install ultralytics` to use it.
Without one the objects step is skipped, saying why.

A resource's objects are rows of object_track, one per kind: the spans it's in (time, or pages for a document, as
faces count them) and its boxes on each frame or page. The kinds it has are also on the resource (`objects`), for the
Library's filter and for search.
"""

from __future__ import annotations

import pathlib
from collections import defaultdict

import numpy as np

from . import faces, jobs, keyring, store

R = store.R
MODELS = pathlib.Path("/opt/lens/models")  # models put there by hand, or by older lens:full images
BOXES_MAX = 500  # boxes kept per kind and resource
COCO = (
    "person bicycle car motorcycle airplane bus train truck boat traffic_light fire_hydrant stop_sign parking_meter bench "
    "bird cat dog horse sheep cow elephant bear zebra giraffe backpack umbrella handbag tie suitcase frisbee skis "
    "snowboard sports_ball kite baseball_bat baseball_glove skateboard surfboard tennis_racket bottle wine_glass cup fork "
    "knife spoon bowl banana apple sandwich orange broccoli carrot hot_dog pizza donut cake chair couch potted_plant bed "
    "dining_table toilet tv laptop mouse remote keyboard cell_phone microwave oven toaster sink refrigerator book clock "
    "vase scissors teddy_bear hair_drier toothbrush"
)
LABELS = tuple(w.replace("_", " ") for w in COCO.split())


def yolox_model(cfg):
    """The YOLOX model to use: video.yolox_model, else the first yolox*.onnx in MODELS or the data folder's models
    (fetched by components.py; None without one)."""
    from . import components

    named = cfg["video"].get("yolox_model")
    if named:
        return str(named)
    for d in (MODELS, components.models_dir(cfg)):
        found = sorted(d.glob("yolox*.onnx")) if d.is_dir() else []
        if found:
            return str(found[0])
    return None


def _letterbox(img, size):
    """The picture scaled to fit `size` (height, width) without changing its shape, the rest grey (as YOLOX was
    trained), in YOLOX's channel order (BGR); and the scale."""
    from PIL import Image

    h, w = size
    r = min(h / img.height, w / img.width)
    scaled = img.convert("RGB").resize((max(1, round(img.width * r)), max(1, round(img.height * r))), Image.BILINEAR)
    out = np.full((h, w, 3), 114, dtype=np.uint8)
    out[: scaled.height, : scaled.width] = np.asarray(scaled)[:, :, ::-1]
    return out.transpose(2, 0, 1)[None].astype(np.float32), r


def _decode(pred, size, strides=(8, 16, 32)):
    """YOLOX's raw output (centre offsets and log sizes per grid cell) as boxes in pixels of the letterboxed input. A
    model exported with its decoding built in already gives pixels, and is left as it is: its centres span the
    picture, where raw offsets stay within a few cells of their own."""
    pred = pred.astype(np.float32).copy()
    if float(np.max(np.abs(pred[:, :2]))) > 2 * max(strides):
        return pred
    grids, steps = [], []
    for s in strides:
        gh, gw = size[0] // s, size[1] // s
        xv, yv = np.meshgrid(np.arange(gw), np.arange(gh))
        grids.append(np.stack((xv, yv), 2).reshape(-1, 2))
        steps.append(np.full((gh * gw, 1), s))
    grid, step = np.concatenate(grids), np.concatenate(steps)
    if len(grid) != len(pred):
        raise ValueError(f"the model's output ({len(pred)} boxes) doesn't fit a YOLOX model of input {size[1]}x{size[0]}")
    pred[:, :2] = (pred[:, :2] + grid) * step
    pred[:, 2:4] = np.exp(np.minimum(pred[:, 2:4], 20)) * step
    return pred


def _nms(boxes, scores, iou):
    """Greedy non-maximum suppression: the indices of the boxes kept, best first. boxes are [x1, y1, x2, y2]."""
    order, keep = scores.argsort()[::-1], []
    area = (boxes[:, 2] - boxes[:, 0]) * (boxes[:, 3] - boxes[:, 1])
    while order.size:
        i = order[0]
        keep.append(int(i))
        xx1, yy1 = np.maximum(boxes[i, 0], boxes[order[1:], 0]), np.maximum(boxes[i, 1], boxes[order[1:], 1])
        xx2, yy2 = np.minimum(boxes[i, 2], boxes[order[1:], 2]), np.minimum(boxes[i, 3], boxes[order[1:], 3])
        inter = np.maximum(0, xx2 - xx1) * np.maximum(0, yy2 - yy1)
        order = order[1:][inter / (area[i] + area[order[1:]] - inter + 1e-9) <= iou]
    return keep


class YoloxOnnx:
    name = "yolox"

    def __init__(self, cfg):
        model = yolox_model(cfg)
        if not model or not pathlib.Path(model).is_file():
            raise RuntimeError("no YOLOX model: set video.yolox_model to a YOLOX .onnx file (the lens:full image has one)")
        try:
            import onnxruntime as ort
        except ImportError:
            raise RuntimeError('ONNX Runtime isn\'t installed (pip install "lens[objects]"; the lens:full image has it)') from None
        self.session = ort.InferenceSession(model, providers=["CPUExecutionProvider"])
        inp = self.session.get_inputs()[0]
        self.input = inp.name
        h, w = inp.shape[2:4]
        self.size = (h if isinstance(h, int) else 640, w if isinstance(w, int) else 640)
        self.min_score = float(cfg["video"].get("object_min_score") or 0.4)

    def detect(self, path):
        """[{"label", "score", "box": [x, y, w, h] as fractions of the picture}] on the picture at `path`."""
        from PIL import Image

        with Image.open(path) as img:
            W, H = img.size
            blob, r = _letterbox(img, self.size)
        pred = _decode(self.session.run(None, {self.input: blob})[0][0], self.size)
        scores = pred[:, 4:5] * pred[:, 5:]  # objectness × each kind
        kind, best = scores.argmax(1), scores.max(1)
        ok = best >= self.min_score
        if not ok.any():
            return []
        cx, cy, bw, bh = (pred[ok, i] / r for i in range(4))
        boxes = np.stack([cx - bw / 2, cy - bh / 2, cx + bw / 2, cy + bh / 2], 1)
        kind, best = kind[ok], best[ok]
        out = []
        for i in _nms(boxes, best, 0.45):  # one kind per thing, the likeliest (a truck isn't also a car), as YOLOX's demo
            x1, y1 = max(0.0, float(boxes[i, 0])), max(0.0, float(boxes[i, 1]))
            x2, y2 = min(float(W), float(boxes[i, 2])), min(float(H), float(boxes[i, 3]))
            if x2 > x1 and y2 > y1:
                k = int(kind[i])
                label = LABELS[k] if k < len(LABELS) else f"kind {k}"
                out.append({"label": label, "score": float(best[i]), "box": _fractions(x1, y1, x2, y2, W, H)})
        return out  # best first


class UltralyticsYolo:
    """pip install ultralytics. AGPL-3.0: a server that offers it to others must offer them its source."""

    name = "ultralytics"

    def __init__(self, cfg):
        try:
            from ultralytics import YOLO
        except ImportError:
            raise RuntimeError("Ultralytics isn't installed (pip install ultralytics; it's AGPL-3.0)") from None
        self.model = YOLO(cfg["video"].get("ultralytics_model") or "yolov8n.pt")
        self.min_score = float(cfg["video"].get("object_min_score") or 0.4)

    def detect(self, path):
        r = self.model(str(path), verbose=False, conf=self.min_score)[0]
        H, W = r.orig_shape[:2]
        out = []
        for (x1, y1, x2, y2), score, k in zip(r.boxes.xyxy.tolist(), r.boxes.conf.tolist(), r.boxes.cls.tolist(), strict=True):
            label = str(r.names.get(int(k), f"kind {int(k)}")).replace("_", " ")
            out.append({"label": label, "score": float(score), "box": _fractions(x1, y1, x2, y2, W, H)})
        return sorted(out, key=lambda o: -o["score"])


def _fractions(x1, y1, x2, y2, W, H):
    return [round(x1 / W, 4), round(y1 / H, 4), round((x2 - x1) / W, 4), round((y2 - y1) / H, 4)]


ENGINES = {"yolox": YoloxOnnx, "ultralytics": UltralyticsYolo}


def engine(cfg):
    """(the detector, None), or (None, why there's none)."""
    name = cfg["video"].get("object_engine") or "yolox"
    if name == "off":
        return None, "object detection is off (video.object_engine)"
    if name not in ENGINES:
        return None, f"there's no object engine called {name}"
    try:
        return ENGINES[name](cfg), None
    except RuntimeError as e:
        return None, str(e)


def tracks(dets, step, paged):
    """Detections ({t, label, score, box}) grouped by kind: where each is, how often it was seen and its boxes."""
    groups = defaultdict(list)
    for d in dets:
        groups[d["label"].casefold()].append(d)
    out = []
    for label, g in groups.items():
        g.sort(key=lambda d: (d["t"], -d["score"]))
        sp = faces.spans({d["t"] for d in g}, step, 0 if paged else 2)  # pages it isn't on aren't bridged
        best = max(g, key=lambda d: d["score"])
        out.append(
            {
                "label": label,
                "text": label,  # what search reads; a hit opens at its first span
                "spans": sp,
                "t0": sp[0][0],
                "t1": sp[0][1],
                "screen_ms": sum(b - a for a, b in sp),
                "first_ms": sp[0][0],
                "count": len(g),
                "score": round(float(np.mean([d["score"] for d in g])), 3),
                "frame": best.get("frame"),  # where it's best seen
                "box": best["box"],
                "boxes": [[d["t"], *d["box"], round(d["score"], 3)] for d in g][:BOXES_MAX],
            }
        )
    return sorted(out, key=lambda t: (-t["screen_ms"], t["label"]))


def step_objects(db, cfg, rid, say):
    """The objects step: the kinds of object on a video's sampled frames, or on a document's or an image's pages."""
    from . import documents, video

    rec = db.one("SELECT space, source, media, samples, sample_ms FROM $r", r=R("recording", rid)) or {}
    paged = rec.get("source") in ("document", "image")
    if not paged and (rec.get("media") or {}).get("kind") != "video":
        raise jobs.Skip("it isn't a video, a document or an image")
    if paged:
        frames, step = [[p["idx"], p["image"]] for p in documents.pages(db, rid) if p.get("image")], 1
        if not frames:
            raise jobs.Skip("its pages weren't drawn")
    else:
        frames, step = rec.get("samples") or [], rec.get("sample_ms") or 5000
        if not frames:
            raise jobs.Skip("it has no sampled frames yet (the shots step samples them)")
    found, why = engine(cfg)
    if not found:
        raise jobs.Skip(why)
    d, dets = video.frames_dir(cfg, rid), []
    for t, name in frames:
        with keyring.plain_picture(db, cfg, d / name) as pic:
            dets += [{"t": t, "frame": name, **o} for o in found.detect(pic)]
    kept = tracks(dets, step, paged)
    rows = [store.clean({"recording": rid, "space": rec["space"], **k, "paged": paged or None, "engine": found.name}) for k in kept]
    db.run(
        [
            "DELETE object_track WHERE recording = $r",
            *(["INSERT INTO object_track $rows"] if rows else []),
            "UPDATE $rec SET objects = $labels",
        ],
        r=rid,
        rows=rows,
        rec=R("recording", rid),
        labels=sorted(k["label"] for k in kept) or None,
    )
    where = "on its pages" if paged else "on screen"
    top = ", ".join(f"{k['label']} ({k['count']})" for k in kept[:6])
    say(f"{len(kept)} kind(s) of object {where} ({found.name})" + (f": {top}" if top else ""))


def clear(db, rid):
    db.run(["DELETE object_track WHERE recording = $r", "UPDATE $rec SET objects = NONE"], r=rid, rec=R("recording", rid))


def for_recording(db, rid):
    """A resource's objects, the most seen first."""
    rows = db.rows(
        "SELECT label, spans, screen_ms, first_ms, count, score, frame, box, boxes, paged, engine FROM object_track WHERE recording = $r",
        r=rid,
    )
    return sorted(rows, key=lambda t: (-(t.get("screen_ms") or 0), t["label"]))


def label_counts(rows):
    """{label: how many resources} over resources' `objects`."""
    out: dict[str, int] = defaultdict(int)
    for labels in rows:
        for label in labels or []:
            out[label] += 1
    return dict(out)
