"""Video: shots and keyframes, text on screen (OCR) and faces, alongside the transcript of the soundtrack.

ffmpeg finds scene changes and samples frames. An OCR engine reads text from the frames: Tesseract (anywhere), Apple
Vision (on a Mac, through pyobjc), RapidOCR (onnxruntime) or docTR (PyTorch). A face engine detects faces and, where a namespace allows
it, describes them for the per-namespace registry in faces.py: OpenCV's YuNet + SFace (Apache-2.0/MIT models from the
OpenCV Zoo; set video.yunet_model and video.sface_model) or InsightFace (its pretrained models are licensed for
non-commercial research only). Frames and face crops live in data_dir/frames/<recording>/.
"""

from __future__ import annotations

import functools
import json
import pathlib
import platform
import re
import shutil
import subprocess
import threading

import numpy as np

from . import ingest, jobs, store

R = store.R
VIDEO_TYPES = {
    ".mp4": "video/mp4",
    ".m4v": "video/mp4",
    ".mov": "video/quicktime",
    ".mkv": "video/x-matroska",
    ".webm": "video/webm",
    ".avi": "video/x-msvideo",
}


def frames_dir(cfg, rid):
    return pathlib.Path(cfg["data_dir"]) / "frames" / str(rid)


def probe_media(path):
    """kind (video or audio), size, frame rate and duration. Cover art in audio files doesn't count as video."""
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)], capture_output=True, text=True, timeout=120
    )
    j = json.loads(out.stdout or "{}")
    streams = j.get("streams") or []
    v = next((s for s in streams if s.get("codec_type") == "video" and not (s.get("disposition") or {}).get("attached_pic")), None)
    dur = int(float((j.get("format") or {}).get("duration") or 0) * 1000)
    has_audio = any(s.get("codec_type") == "audio" for s in streams)
    if not v:
        return {"kind": "audio", "has_audio": has_audio, "duration_ms": dur}
    num, _, den = (v.get("avg_frame_rate") or "0/1").partition("/")
    fps = float(num) / float(den) if den and float(den) else None
    return {
        "kind": "video",
        "width": int(v.get("width") or 0),
        "height": int(v.get("height") or 0),
        "fps": round(fps, 3) if fps else None,
        "vcodec": v.get("codec_name"),
        "has_audio": has_audio,
        "duration_ms": dur,
    }


def scene_changes(path, threshold):
    out = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-nostats",
            "-i",
            str(path),
            "-map",
            "0:v:0",
            "-vf",
            f"select='gt(scene,{threshold})',showinfo",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
        timeout=6 * 3600,
    )
    return sorted({round(float(m.group(1)), 3) for m in re.finditer(r"pts_time:([\d.]+)", out.stderr)})


def shots_of(duration_s, changes, min_len):
    cuts = [0.0]
    for c in changes:
        if c - cuts[-1] >= min_len and duration_s - c >= min_len:
            cuts.append(c)
    cuts.append(duration_s)
    return [(a, b) for a, b in zip(cuts, cuts[1:]) if b > a]


def extract_frame(path, t, dest, width):
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-ss",
            f"{t:.3f}",
            "-i",
            str(path),
            "-frames:v",
            "1",
            "-vf",
            f"scale='min({width},iw)':-2",
            "-q:v",
            "3",
            "-y",
            str(dest),
        ],
        capture_output=True,
        timeout=300,
    )
    return dest.exists()


def sample_frames(path, every, dest_dir, width):
    """One pass over the video: a frame every `every` seconds, named by its time in milliseconds."""
    tmp = dest_dir / "tmp"
    tmp.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(path),
            "-map",
            "0:v:0",
            "-vf",
            f"fps=1/{every},scale='min({width},iw)':-2",
            "-q:v",
            "4",
            "-start_number",
            "0",
            str(tmp / "%06d.jpg"),
        ],
        capture_output=True,
        timeout=6 * 3600,
    )
    out = []
    for f in sorted(tmp.glob("*.jpg")):
        t = int(int(f.stem) * every * 1000)
        dest = dest_dir / f"s{t:09d}.jpg"
        f.replace(dest)
        out.append([t, dest.name])
    shutil.rmtree(tmp, ignore_errors=True)
    return out


# ---------- OCR engines: lines(path) -> [{"text", "conf" (0-100), "box": [x, y, w, h] as fractions of the frame}] ----------
class TesseractOCR:
    name = "tesseract"

    def __init__(self, cfg):
        self.bin = shutil.which("tesseract")
        if not self.bin:
            raise RuntimeError("Tesseract isn't installed")
        self.langs = "+".join(cfg["video"].get("ocr_languages") or ["eng"])

    def _words(self, path):
        """Tesseract's words: (block, paragraph, line, x, y, w, h, conf, text), and the image's size."""
        from PIL import Image

        W, H = Image.open(path).size
        out = subprocess.run([self.bin, str(path), "stdout", "-l", self.langs, "tsv"], capture_output=True, text=True, timeout=300)
        words = []
        for row in out.stdout.splitlines()[1:]:
            c = row.split("\t")
            if len(c) < 12 or c[0] != "5" or not c[11].strip() or float(c[10]) < 0:
                continue
            words.append((c[2], c[3], c[4], int(c[6]), int(c[7]), int(c[8]), int(c[9]), float(c[10]), c[11]))
        return words, W, H

    def _grouped(self, path, key):
        words, W, H = self._words(path)
        groups = {}
        for b, p, ln, x, y, w, h, conf, text in words:
            g = groups.setdefault(key(b, p, ln), {"words": [], "conf": [], "box": [x, y, x + w, y + h], "line": ln})
            g["words"].append(("\n" if g["line"] != ln else "") + text)
            g["line"] = ln
            g["conf"].append(conf)
            g["box"] = [min(g["box"][0], x), min(g["box"][1], y), max(g["box"][2], x + w), max(g["box"][3], y + h)]
        return [
            {
                "text": " ".join(g["words"]).replace(" \n", "\n"),
                "conf": sum(g["conf"]) / len(g["conf"]),
                "box": [
                    round(g["box"][0] / W, 4),
                    round(g["box"][1] / H, 4),
                    round((g["box"][2] - g["box"][0]) / W, 4),
                    round((g["box"][3] - g["box"][1]) / H, 4),
                ],
            }
            for g in groups.values()
        ]

    def lines(self, path):
        return self._grouped(path, lambda b, p, ln: (b, p, ln))

    def paragraphs(self, path):
        """Its paragraphs as Tesseract laid them out (columns kept apart), lines separated by newlines."""
        return self._grouped(path, lambda b, p, ln: (b, p))


class AppleVisionOCR:
    """macOS only (pip install pyobjc-framework-Vision); run it in a worker on the Mac."""

    name = "apple-vision"

    def __init__(self, cfg):
        try:
            import Vision  # noqa: F401
            from Foundation import NSURL  # noqa: F401
        except ImportError:
            raise RuntimeError('Apple Vision needs a Mac worker with pyobjc-framework-Vision (pip install "lens[mac-ocr]")') from None
        self.langs = cfg["video"].get("ocr_languages") or ["eng"]

    def lines(self, path):
        import Vision
        from Foundation import NSURL

        req = Vision.VNRecognizeTextRequest.alloc().init()
        req.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
        handler = Vision.VNImageRequestHandler.alloc().initWithURL_options_(NSURL.fileURLWithPath_(str(path)), None)
        handler.performRequests_error_([req], None)
        out = []
        for obs in req.results() or []:
            cand = obs.topCandidates_(1)[0]
            b = obs.boundingBox()  # normalised, origin bottom-left
            out.append(
                {
                    "text": str(cand.string()),
                    "conf": float(cand.confidence()) * 100,
                    "box": [
                        round(b.origin.x, 4),
                        round(1 - b.origin.y - b.size.height, 4),
                        round(b.size.width, 4),
                        round(b.size.height, 4),
                    ],
                }
            )
        return out


class RapidOCRengine:
    name = "rapidocr"

    def __init__(self, cfg):
        try:
            from rapidocr_onnxruntime import RapidOCR
        except ImportError:
            raise RuntimeError('RapidOCR isn\'t installed (pip install "lens[rapidocr]")') from None
        self.engine = RapidOCR()

    def lines(self, path):
        from PIL import Image

        W, H = Image.open(path).size
        result, _ = self.engine(str(path))
        out = []
        for pts, text, score in result or []:
            xs, ys = [p[0] for p in pts], [p[1] for p in pts]
            out.append(
                {
                    "text": text,
                    "conf": float(score) * 100,
                    "box": [
                        round(min(xs) / W, 4),
                        round(min(ys) / H, 4),
                        round((max(xs) - min(xs)) / W, 4),
                        round((max(ys) - min(ys)) / H, 4),
                    ],
                }
            )
        return out


@functools.lru_cache(maxsize=2)
def _doctr_predictor(detector, recognizer):
    """docTR's two models, loaded once per worker (they're fetched the first time, into DOCTR_CACHE_DIR or
    ~/.cache/doctr), and a lock: one page at a time through them."""
    from doctr.models import ocr_predictor

    return ocr_predictor(det_arch=detector, reco_arch=recognizer, pretrained=True), threading.Lock()


class DocTROCR:
    """docTR (Apache-2.0) on PyTorch: pip install "lens[doctr]". Good on scans and photos of text; it reads the Latin
    alphabet (video.ocr_languages is Tesseract's)."""

    name = "doctr"
    DETECTOR, RECOGNIZER = "fast_base", "crnn_vgg16_bn"  # docTR's own defaults

    def __init__(self, cfg):
        try:
            import doctr.io  # noqa: F401
            import doctr.models  # noqa: F401
        except ModuleNotFoundError as e:
            if (e.name or "").split(".")[0] != "doctr":
                raise RuntimeError(f"docTR can't be loaded ({e})") from None
            raise RuntimeError('docTR isn\'t installed (pip install "lens[doctr]"; it brings PyTorch)') from None
        except ImportError as e:  # installed, but something it needs isn't there (OpenCV's libGL, say)
            raise RuntimeError(f"docTR can't be loaded ({e})") from None
        try:
            self.model, self.lock = _doctr_predictor(self.DETECTOR, self.RECOGNIZER)
        except Exception as e:  # its models couldn't be fetched or loaded: say so, and the step goes on without OCR
            raise RuntimeError(f"docTR couldn't load its models ({str(e)[:200]})") from None

    def _blocks(self, path):
        from doctr.io import DocumentFile

        with self.lock:
            pages = self.model(DocumentFile.from_images([str(path)])).pages
        return [b for page in pages for b in page.blocks]

    @staticmethod
    def _found(lines):
        """docTR's lines (their words, each with its confidence 0-1, and boxes as fractions of the page) as one
        finding: their text a line each, how sure it was of their words on average, and the box around them."""
        words = [w for ln in lines for w in ln.words]
        if not words:
            return None
        pts = [pt for ln in lines for pt in ln.geometry]
        x0, y0 = min(p[0] for p in pts), min(p[1] for p in pts)
        x1, y1 = max(p[0] for p in pts), max(p[1] for p in pts)
        return {
            "text": "\n".join(" ".join(w.value for w in ln.words) for ln in lines if ln.words),
            "conf": 100 * sum(float(w.confidence) for w in words) / len(words),
            "box": [round(float(x0), 4), round(float(y0), 4), round(float(x1 - x0), 4), round(float(y1 - y0), 4)],
        }

    def lines(self, path):
        return [f for b in self._blocks(path) for ln in b.lines if (f := self._found([ln]))]

    def paragraphs(self, path):
        """Its blocks as docTR laid them out, lines separated by newlines."""
        return [f for b in self._blocks(path) if (f := self._found(b.lines))]


OCR_ENGINES = {"tesseract": TesseractOCR, "apple-vision": AppleVisionOCR, "rapidocr": RapidOCRengine, "doctr": DocTROCR}


def ocr_engine_why(cfg):
    """The OCR engine of video.ocr_engine, and None; or None, and why there's none. "auto" is Apple Vision on a Mac,
    else Tesseract, else RapidOCR; docTR only when it's chosen."""
    name = cfg["video"].get("ocr_engine") or "auto"
    if name == "none":
        return None, "OCR is off (video.ocr_engine)"
    if name != "auto" and name not in OCR_ENGINES:
        return None, f"there's no OCR engine called {name}"
    order = (["apple-vision"] if platform.system() == "Darwin" else []) + ["tesseract", "rapidocr"] if name == "auto" else [name]
    why = None
    for n in order:
        try:
            return OCR_ENGINES[n](cfg), None
        except (ImportError, RuntimeError) as e:
            why = why or str(e)
    return None, (why if name != "auto" else "no OCR engine is available: install Tesseract (video.ocr_engine)")


def ocr_engine(cfg):
    return ocr_engine_why(cfg)[0]


# ---------- face engines: faces(path) -> [{"box": [x, y, w, h] fractions, "score", "embedding": unit vector}] ----------
class OpenCVFaces:
    name = "opencv"

    def __init__(self, cfg):
        import cv2

        v = cfg["video"]
        det, rec = v.get("yunet_model"), v.get("sface_model")
        if not (det and rec and pathlib.Path(det).exists() and pathlib.Path(rec).exists()):
            raise RuntimeError("set video.yunet_model and video.sface_model to the OpenCV Zoo ONNX files")
        self.cv2 = cv2
        self.det = cv2.FaceDetectorYN.create(det, "", (320, 320), 0.8)
        self.rec = cv2.FaceRecognizerSF.create(rec, "")

    def faces(self, path):
        img = self.cv2.imread(str(path))
        h, w = img.shape[:2]
        self.det.setInputSize((w, h))
        _, found = self.det.detect(img)
        out = []
        for f in found if found is not None else []:
            emb = self.rec.feature(self.rec.alignCrop(img, f)).flatten().astype(np.float64)
            out.append(
                {
                    "box": [float(f[0]) / w, float(f[1]) / h, float(f[2]) / w, float(f[3]) / h],
                    "score": float(f[-1]),
                    "embedding": emb / (np.linalg.norm(emb) + 1e-9),
                }
            )
        return out


class InsightFaces:
    """pip install insightface onnxruntime. Its pretrained models are for non-commercial research use only."""

    name = "insightface"

    def __init__(self, cfg):
        from insightface.app import FaceAnalysis

        self.app = FaceAnalysis(name="buffalo_l", providers=["CPUExecutionProvider"])
        self.app.prepare(ctx_id=-1)

    def faces(self, path):
        import cv2

        img = cv2.imread(str(path))
        h, w = img.shape[:2]
        return [
            {
                "box": [float(f.bbox[0]) / w, float(f.bbox[1]) / h, float(f.bbox[2] - f.bbox[0]) / w, float(f.bbox[3] - f.bbox[1]) / h],
                "score": float(f.det_score),
                "embedding": f.normed_embedding.astype(np.float64),
            }
            for f in self.app.get(img)
        ]


def face_engine(cfg):
    try:
        return {"opencv": OpenCVFaces, "insightface": InsightFaces}[cfg["video"].get("face_engine") or "opencv"](cfg)
    except (ImportError, RuntimeError, KeyError):
        return None


# ---------- pipeline steps ----------
def _video(db, cfg, rid):
    rec = db.one("SELECT space, path, remote, source, media, samples, duration_ms FROM $r", r=R("recording", rid)) or {}
    if rec.get("source") != "audio":
        return rec, None
    return rec, ingest.audio_path(db, cfg, rec)


def step_shots(db, cfg, rid, say):
    rec, path = _video(db, cfg, rid)
    if rec.get("source") in ("document", "image"):
        raise jobs.Skip("it isn't a video")
    if not path:
        raise jobs.Skip("there is no media file")
    media = probe_media(path)
    if media["kind"] != "video":
        db.q("UPDATE $r SET media = $m", r=R("recording", rid), m=media)
        raise jobs.Skip("it isn't a video")
    v, d = cfg["video"], frames_dir(cfg, rid)
    shutil.rmtree(d, ignore_errors=True)
    d.mkdir(parents=True, exist_ok=True)
    dur = media["duration_ms"] / 1000
    shots = shots_of(dur, scene_changes(path, v["scene_threshold"]), v["min_shot_seconds"])
    rows = []
    for k, (a, b) in enumerate(shots):
        f = d / f"shot{k:04d}.jpg"
        ok = extract_frame(path, a + min(1.0, (b - a) / 4), f, v["frame_width"])
        rows.append(
            store.clean(
                {
                    "recording": rid,
                    "space": rec["space"],
                    "idx": k,
                    "t0": int(a * 1000),
                    "t1": int(b * 1000),
                    "frame": f.name if ok else None,
                }
            )
        )
    samples = sample_frames(path, v["sample_seconds"], d, v["frame_width"])
    db.run(["DELETE shot WHERE recording = $r"] + (["INSERT INTO shot $rows"] if rows else []), r=rid, rows=rows)
    patch = {"media": media, "samples": samples, "sample_ms": int(v["sample_seconds"] * 1000)}
    if not rec.get("duration_ms"):
        patch["duration_ms"] = media["duration_ms"]
    db.q("UPDATE $r MERGE $p", r=R("recording", rid), p=patch)
    say(f"{len(shots)} shot(s), {len(samples)} sampled frame(s)")


def _norm(text):
    return re.sub(r"\W+", " ", text.lower()).strip()


def step_ocr(db, cfg, rid, say):
    rec = db.one("SELECT space, source, media, samples, sample_ms FROM $r", r=R("recording", rid)) or {}
    if rec.get("source") in ("document", "image"):
        raise jobs.Skip("its pages were read when it was transcribed")
    if (rec.get("media") or {}).get("kind") != "video":
        raise jobs.Skip("it isn't a video")
    engine, why = ocr_engine_why(cfg)
    if not engine:
        raise jobs.Skip(why)
    d, step, min_conf = frames_dir(cfg, rid), rec.get("sample_ms") or 5000, cfg["video"]["ocr_min_confidence"]
    open_, done = {}, []
    for t, name in rec.get("samples") or []:
        seen = set()
        for line in engine.lines(d / name):
            text = re.sub(r"\s+", " ", line["text"]).strip()
            if line["conf"] < min_conf or len(text) < 3 or sum(ch.isalnum() for ch in text) < 0.5 * len(text):
                continue
            k = _norm(text)
            seen.add(k)
            span = open_.get(k)
            if span and t - span["last"] <= step * 1.5:
                span["last"], span["confs"] = t, span["confs"] + [line["conf"]]
            else:
                if span:
                    done.append(span)
                open_[k] = {"text": text, "t0": t, "last": t, "box": line["box"], "frame": name, "confs": [line["conf"]]}
        for k in [k for k in open_ if k not in seen]:
            done.append(open_.pop(k))
    done += open_.values()
    rows = [
        {
            "recording": rid,
            "space": rec["space"],
            "t0": s["t0"],
            "t1": s["last"] + step,
            "text": s["text"],
            "box": s["box"],
            "frame": s["frame"],
            "conf": round(sum(s["confs"]) / len(s["confs"]), 1),
            "engine": engine.name,
        }
        for s in sorted(done, key=lambda s: (s["t0"], s["box"][1]))
    ]
    db.run(["DELETE ocr_span WHERE recording = $r"] + (["INSERT INTO ocr_span $rows"] if rows else []), r=rid, rows=rows)
    say(f"{len(rows)} line(s) of text on screen ({engine.name})")


def step_faces(db, cfg, rid, say):
    """Faces in a video's sampled frames, or on a document's or an image's pages (where `t` counts pages, from 0, so a
    face on pages 3 to 5 has one track over them)."""
    from . import faces

    rec = db.one("SELECT space, source, media, samples, sample_ms FROM $r", r=R("recording", rid)) or {}
    paged = rec.get("source") in ("document", "image")
    if not paged and (rec.get("media") or {}).get("kind") != "video":
        raise jobs.Skip("it isn't a video")
    mode = faces.mode(db, rec["space"])
    if mode == "off":
        faces.clear_recording(db, cfg, rid)
        raise jobs.Skip("face detection is off for this namespace")
    engine = face_engine(cfg)
    if not engine:
        raise jobs.Skip("no face engine is configured")
    if paged:
        from . import documents

        frames, step = [[p["idx"], p["image"]] for p in documents.pages(db, rid) if p.get("image")], 1
        if not frames:
            raise jobs.Skip("its pages weren't drawn")
    else:
        frames, step = rec.get("samples") or [], rec.get("sample_ms") or 5000
    d, dets = frames_dir(cfg, rid), []
    for t, name in frames:
        for f in engine.faces(d / name):
            dets.append({"t": t, "frame": name, **f})
    faces.store_tracks(db, cfg, rid, rec["space"], dets, mode, step, say, paged)
