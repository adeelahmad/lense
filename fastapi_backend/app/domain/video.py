"""Video: shots and keyframes, text on screen (OCR) and faces, alongside the transcript of the soundtrack.

ffmpeg finds scene changes and samples frames. An OCR engine reads text from the frames: Tesseract (anywhere), Apple
Vision (on a Mac, through pyobjc) or RapidOCR (onnxruntime). A face engine detects faces and, where a namespace allows
it, describes them for the per-namespace registry in faces.py: OpenCV's YuNet + SFace (Apache-2.0/MIT models from the
OpenCV Zoo; set video.yunet_model and video.sface_model) or InsightFace (its pretrained models are licensed for
non-commercial research only). Frames and face crops live in data_dir/frames/<recording>/.
"""

from __future__ import annotations

import json
import pathlib
import platform
import re
import shutil
import subprocess

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
            raise RuntimeError("tesseract isn't installed")
        self.langs = "+".join(cfg["video"].get("ocr_languages") or ["eng"])

    def lines(self, path):
        from PIL import Image

        W, H = Image.open(path).size
        out = subprocess.run([self.bin, str(path), "stdout", "-l", self.langs, "tsv"], capture_output=True, text=True, timeout=300)
        groups = {}
        for row in out.stdout.splitlines()[1:]:
            c = row.split("\t")
            if len(c) < 12 or c[0] != "5" or not c[11].strip() or float(c[10]) < 0:
                continue
            key = (c[2], c[3], c[4])
            x, y, w, h, conf = int(c[6]), int(c[7]), int(c[8]), int(c[9]), float(c[10])
            g = groups.setdefault(key, {"words": [], "conf": [], "box": [x, y, x + w, y + h]})
            g["words"].append(c[11])
            g["conf"].append(conf)
            g["box"] = [min(g["box"][0], x), min(g["box"][1], y), max(g["box"][2], x + w), max(g["box"][3], y + h)]
        return [
            {
                "text": " ".join(g["words"]),
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


class AppleVisionOCR:
    """macOS only (pip install pyobjc-framework-Vision); run it in a worker on the Mac."""

    name = "apple-vision"

    def __init__(self, cfg):
        import Vision  # noqa: F401
        from Foundation import NSURL  # noqa: F401

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
        from rapidocr_onnxruntime import RapidOCR

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


def ocr_engine(cfg):
    name = cfg["video"].get("ocr_engine") or "auto"
    order = {"auto": (["apple-vision"] if platform.system() == "Darwin" else []) + ["tesseract", "rapidocr"]}.get(name, [name])
    for n in order:
        try:
            return {"tesseract": TesseractOCR, "apple-vision": AppleVisionOCR, "rapidocr": RapidOCRengine}[n](cfg)
        except (ImportError, RuntimeError, KeyError):
            continue
    return None


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
    rec = db.one("SELECT space, media, samples, sample_ms FROM $r", r=R("recording", rid)) or {}
    if (rec.get("media") or {}).get("kind") != "video":
        raise jobs.Skip("it isn't a video")
    engine = ocr_engine(cfg)
    if not engine:
        raise jobs.Skip("no OCR engine is available")
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
    from . import faces

    rec = db.one("SELECT space, media, samples, sample_ms FROM $r", r=R("recording", rid)) or {}
    if (rec.get("media") or {}).get("kind") != "video":
        raise jobs.Skip("it isn't a video")
    mode = faces.mode(db, rec["space"])
    if mode == "off":
        faces.clear_recording(db, cfg, rid)
        raise jobs.Skip("face detection is off for this namespace")
    engine = face_engine(cfg)
    if not engine:
        raise jobs.Skip("no face engine is configured")
    d, dets = frames_dir(cfg, rid), []
    for t, name in rec.get("samples") or []:
        for f in engine.faces(d / name):
            dets.append({"t": t, "frame": name, **f})
    faces.store_tracks(db, cfg, rid, rec["space"], dets, mode, rec.get("sample_ms") or 5000, say)
