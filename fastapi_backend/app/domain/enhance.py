"""Enhancing low quality pictures (docs/enhance.md): the enhance step looks at an image before it's read and, when it's
low quality, makes an enhanced copy that the read step (OCR), faces, objects, NSFW and descriptions then work from. The
original stays as it is: the copy is a rendition, data_dir/renditions/<resource>-enhanced.jpg (encrypted where the
namespace's files are), which the asset page offers to save.

Off by default: Settings → Enhance, `enhance.images`. Run with `force` (an agent's `enhance` tool, POST
/resources/{rid}/enhance, the `enhance` automation action, or a pipeline step that says so), it enhances an image
whatever the settings and its quality.

What counts as low quality (each a reason kept in recording.enhanced.low):
- `small`: its shorter side is under enhance.min_side pixels.
- `blurry`: the variance of its Laplacian (how sharply its tones change), where it's sharpest, is under
  enhance.min_sharpness.
- `noisy`: its noise, estimated with Immerkær's filter where it's plainest (so edges and texture don't count), is over
  NOISE levels.
- `dull`: its tones' standard deviation is under DULL: low contrast, or too dark or too bright.
- `small_faces`: a face is under enhance.min_face_px tall, where the namespace finds faces (faces.mode isn't off), so
  recognition has more to go on. With enhance.only_faces, only pictures with a face are enhanced at all.

It's made bigger to bring its shorter side to min_side and its smallest face to min_face_px, at most enhance.max_scale
times and MAX_SIDE pixels. The engine (`enhance.image_engine`):
- `classic` (the default): Pillow, nothing to fetch. A noisy picture is smoothed with a median filter, enlarged with
  Lanczos, a dull one stretched to its full range of tones, and a soft or enlarged one sharpened (unsharp mask).
- `realesrgan`: Real-ESRGAN general x4v3 (BSD-3-Clause, 5 MB, as Qualcomm AI Hub exports it to ONNX), run by ONNX
  Runtime on the CPU in tiles of 128 pixels. It enlarges four times while taking out blur, noise and JPEG blocks, and
  the result is brought to the size wanted. components.py fetches it and ONNX Runtime the first time the step runs with
  it chosen. A picture over REAL_MAX_PIXELS goes to classic instead, so a small machine isn't swamped.

What a resource keeps (recording.enhanced): `kind`, `low` (the reasons), `measured` (width, height, sharpness, noise,
contrast, brightness, and faces and smallest_face where they were looked for), `enhanced` (whether a copy was made),
and for a copy its `engine`, `scale`, `width`, `height` and `forced`; `at`.
"""

from __future__ import annotations

import io
import os
import pathlib
import threading

import numpy as np

from . import convert, ingest, jobs, keyring, store

R = store.R
ENGINES = ("classic", "realesrgan")
MODEL_NAME = "realesr-general-x4v3"
MODEL_FILES = (f"{MODEL_NAME}/model.onnx", f"{MODEL_NAME}/model.data")  # components.FILES fetches them
MODELS = pathlib.Path("/opt/lens/models")  # models put there by hand, or baked into an image
TILE, PAD, SR = 128, 8, 4  # the model's input, the overlap around each tile, and how much bigger it makes them
REAL_MAX_PIXELS = 1_500_000  # pictures larger than this are enhanced by classic (four times as big would be 24 MP)
MAX_SIDE = 4000  # an enhanced picture's longest side
MEASURE_SIDE = 2048  # a larger picture is measured at this size
NOISE = 6.0  # noise (levels out of 255) over this is noisy
DULL = 30.0  # a standard deviation of tones under this is dull
MIN_SCALE = 1.1  # enlarging less than this isn't worth it
QUALITY = 92  # JPEG quality of the enhanced copy
LIMITS = {"min_side": (64, 4000), "min_sharpness": (0, 10000), "min_face_px": (0, 1024), "max_scale": (1, 4)}
REASONS = {"small": "small", "blurry": "blurry", "noisy": "noisy", "dull": "low contrast", "small_faces": "faces too small"}
_SESSIONS: dict[str, object] = {}
_LOCK = threading.Lock()


def options(cfg):
    return {**store.DEFAULTS["enhance"], **(cfg.get("enhance") or {})}


def check(key, value):
    """A setting's value, checked: ValueError naming the problem."""
    if key in ("images", "only_faces"):
        if not isinstance(value, bool):
            raise ValueError(f"enhance.{key} is true or false")
        return value
    if key == "image_engine":
        if value not in ENGINES:
            raise ValueError(f"enhance.image_engine is one of {', '.join(ENGINES)}")
        return value
    if key in LIMITS:
        lo, hi = LIMITS[key]
        if not (isinstance(value, (int, float)) and not isinstance(value, bool) and lo <= value <= hi):
            raise ValueError(f"enhance.{key} is a number from {lo} to {hi}")
        return int(value) if key in ("min_side", "min_face_px") else round(float(value), 2)
    raise ValueError(f"there's no enhance setting called {key}")


def wants_faces(cfg):
    """Whether the step looks for faces: what has it wait for the face models (components.py)."""
    o = options(cfg)
    return bool(o["images"] and (o["only_faces"] or o["min_face_px"]))


# ---------- the copy ----------
def enhanced_path(cfg, rid):
    return pathlib.Path(cfg["data_dir"]) / "renditions" / f"{int(rid)}-enhanced.jpg"


def files(cfg, rid):
    """The enhanced copies a resource has, as paths."""
    p = enhanced_path(cfg, rid)
    return [p] if p.is_file() else []


def picture(db, cfg, rid, path):
    """The file an image is read from: its enhanced copy when the enhance step made one, else its own file."""
    p = enhanced_path(cfg, rid)
    return keyring.working_copy(db, cfg, str(p)) if p.is_file() else path


def clear(db, cfg, rid):
    """Forget a resource's enhanced copy: the original is read again."""
    for p in files(cfg, rid):
        p.unlink(missing_ok=True)
    db.q("UPDATE $r SET enhanced = NONE", r=R("recording", rid))


def brief(e, rid):
    """A resource's enhancement as the API shows it, with a link to save its copy."""
    if not e:
        return None
    out = dict(e)
    if e.get("enhanced"):
        out["download"] = f"{store.API}/recordings/{int(rid)}/enhanced"
    return out


# ---------- how good it looks ----------
def measure(img):
    """How good an RGB picture looks: its size, sharpness (the variance of the Laplacian where it is sharpest), noise (Immerkær's estimate, out
    of 255), contrast (standard deviation of its tones) and brightness (their mean)."""
    from PIL import Image
    from scipy import ndimage

    w, h = img.size
    g = img.convert("L")
    if max(w, h) > MEASURE_SIDE:
        g.thumbnail((MEASURE_SIDE, MEASURE_SIDE), Image.LANCZOS)
    a = np.asarray(g, dtype=np.float32)
    noise = 0.0
    if min(a.shape) > 2:
        k = np.array([[1, -2, 1], [-2, 4, -2], [1, -2, 1]], dtype=np.float32)
        r = np.abs(ndimage.convolve(a, k))[1:-1, 1:-1]
        g = np.hypot(ndimage.sobel(a, 0), ndimage.sobel(a, 1))[1:-1, 1:-1]
        # where it's plainest (the flattest 30%), so edges and texture aren't taken for noise: the filter's response
        # to noise of deviation s has deviation 6s, and its median magnitude is 0.6745 of that
        noise = float(np.median(r[g <= np.percentile(g, 30)])) / (6 * 0.6745)
    return {
        "width": w,
        "height": h,
        "sharpness": round(_sharpness(ndimage.laplace(a)), 1),
        "noise": round(noise, 2),
        "contrast": round(float(a.std()), 1),
        "brightness": round(float(a.mean()), 1),
    }


def _sharpness(lap):
    """The variance of the Laplacian where the picture is sharpest: block by block, the 90th percentile, so a sharp
    subject on a soft background, or a clean drawing with wide plain areas, isn't taken for blurry."""
    n = max(16, min(64, min(lap.shape) // 4))
    rows, cols = lap.shape[0] // n, lap.shape[1] // n
    if rows * cols < 4:
        return float(lap.var())
    blocks = lap[: rows * n, : cols * n].reshape(rows, n, cols, n).var(axis=(1, 3))
    return float(np.percentile(blocks, 90))


def assess(m, faces, opts):
    """(the reasons a picture is low quality, how many times bigger to make it): `faces`, the heights of its faces in
    pixels, or None where they weren't looked for."""
    low, scale = [], 1.0
    w, h = m["width"], m["height"]
    if min(w, h) < opts["min_side"]:
        low.append("small")
        scale = max(scale, opts["min_side"] / min(w, h))
    if m["sharpness"] < opts["min_sharpness"]:
        low.append("blurry")
    if m["noise"] > NOISE:
        low.append("noisy")
    if m["contrast"] < DULL:
        low.append("dull")
    if faces and opts["min_face_px"] and min(faces) < opts["min_face_px"]:
        low.append("small_faces")
        scale = max(scale, opts["min_face_px"] / max(1, min(faces)))
    scale = min(scale, opts["max_scale"], MAX_SIDE / max(w, h))
    return low, round(scale, 2) if scale >= MIN_SCALE else 1.0


def _face_heights(db, cfg, sid, img):
    """(the heights of the faces on a picture in pixels, None) or (None, why they weren't looked for)."""
    from . import faces, video

    if faces.mode(db, sid) == "off":
        return None, "face detection is off in this namespace"
    engine = video.face_engine(cfg)
    if not engine:
        return None, "no face engine is here yet (Settings → Video)"
    with keyring.scratch(cfg) as d:
        p = d / "picture.png"
        img.save(p)
        found = engine.faces(str(p))
    return [max(1, round(f["box"][3] * img.height)) for f in found], None


# ---------- engines ----------
def model_path(cfg):
    """Real-ESRGAN's model: one put in MODELS by hand, else the one components.py fetched; None without one (its
    weights are a file of their own beside it)."""
    from . import components

    for d in (MODELS, components.models_dir(cfg)):
        if all((d / f).is_file() for f in MODEL_FILES):
            return str(d / MODEL_FILES[0])
    return None


def _session(path):
    import onnxruntime as ort

    with _LOCK:  # one per process
        if path not in _SESSIONS:
            _SESSIONS[path] = ort.InferenceSession(path, providers=["CPUExecutionProvider"])
        return _SESSIONS[path]


class RealEsrgan:
    name = MODEL_NAME

    def __init__(self, cfg):
        path = model_path(cfg)
        if not path:
            raise RuntimeError("Real-ESRGAN isn't here yet (it's fetched the first time the step runs with it chosen)")
        try:
            import onnxruntime  # noqa: F401
        except ImportError:
            raise RuntimeError("ONNX Runtime isn't installed (it's fetched on first use, or pip install \"lens[objects]\")") from None
        self.session = _session(path)
        self.input = self.session.get_inputs()[0].name

    def upscale(self, img):
        """An RGB picture four times as big, tile by tile: each tile overlaps its neighbours by PAD pixels, which are
        cut off again, so no seams show."""
        from PIL import Image

        a = np.asarray(img, dtype=np.float32) / 255.0
        h, w = a.shape[:2]
        step = TILE - 2 * PAD
        H, W = -(-h // step) * step, -(-w // step) * step
        padded = np.pad(a, ((PAD, H - h + PAD), (PAD, W - w + PAD), (0, 0)), mode="edge")
        out = np.empty((H * SR, W * SR, 3), dtype=np.uint8)
        inner = slice(PAD * SR, (PAD + step) * SR)
        for y in range(0, H, step):
            for x in range(0, W, step):
                tile = np.ascontiguousarray(padded[y : y + TILE, x : x + TILE].transpose(2, 0, 1)[None])
                got = self.session.run(None, {self.input: tile})[0][0][:, inner, inner]
                out[y * SR : (y + step) * SR, x * SR : (x + step) * SR] = (got.transpose(1, 2, 0).clip(0, 1) * 255 + 0.5).astype(np.uint8)
        return Image.fromarray(out[: h * SR, : w * SR])


def _size(img, scale):
    return max(1, round(img.width * scale)), max(1, round(img.height * scale))


def classic(img, low, scale):
    """Pillow alone: smoothed if noisy, enlarged, stretched if dull, sharpened if soft or enlarged."""
    from PIL import Image, ImageFilter, ImageOps

    if "noisy" in low:
        img = img.filter(ImageFilter.MedianFilter(3))
    if scale > 1:
        img = img.resize(_size(img, scale), Image.LANCZOS)
    if "dull" in low:
        img = ImageOps.autocontrast(img, cutoff=1, preserve_tone=True)
    if "blurry" in low or scale > 1:
        img = img.filter(ImageFilter.UnsharpMask(radius=2, percent=150 if "blurry" in low else 80, threshold=3))
    return img


def image_engine(cfg, img):
    """(Real-ESRGAN, None) where it's chosen and can take this picture, else (None, why classic does it)."""
    if options(cfg)["image_engine"] != "realesrgan":
        return None, None
    if img.width * img.height > REAL_MAX_PIXELS:
        return None, f"it's over {REAL_MAX_PIXELS // 1_000_000:g} MP, too large for Real-ESRGAN here: enhanced by classic"
    try:
        return RealEsrgan(cfg), None
    except RuntimeError as e:
        return None, f"{e}: enhanced by classic"


def enhance_image(cfg, img, low, scale, say):
    """(the enhanced picture, the engine's name)."""
    from PIL import Image, ImageOps

    real, why = image_engine(cfg, img)
    if why:
        say(why)
    if not real:
        return classic(img, low, scale), "classic"
    big = real.upscale(img)
    if scale != SR:
        big = big.resize(_size(img, scale), Image.LANCZOS)
    if "dull" in low:
        big = ImageOps.autocontrast(big, cutoff=1, preserve_tone=True)
    return big, real.name


# ---------- the step ----------
def _keep(db, rid, e):
    db.q("UPDATE $r SET enhanced = $e", r=R("recording", rid), e=store.clean(e))


def _said(m):
    return f"{m['width']}×{m['height']}, sharpness {m['sharpness']:g}, noise {m['noise']:g}, contrast {m['contrast']:g}"


def step(db, cfg, rid, say, spec=None):
    """The enhance step: an image that looks low quality gets an enhanced copy, which it's read from."""
    rec = db.one("SELECT space, path, remote, source, enhanced FROM $r", r=R("recording", rid)) or {}
    force = bool((spec or {}).get("force"))
    if rec.get("source") != "image":
        raise jobs.Skip("only images are enhanced so far")
    opts = options(cfg)
    if not (opts["images"] or force):
        if (rec.get("enhanced") or {}).get("forced"):  # someone asked for this one: kept
            raise jobs.Skip("enhancing images is off (Settings → Enhance); kept the copy made on request")
        clear(db, cfg, rid)
        raise jobs.Skip("enhancing images is off (Settings → Enhance)")
    path = ingest.audio_path(db, cfg, rec)
    if not path or not os.path.exists(path):
        raise FileNotFoundError(f"its file isn't there ({rec.get('path')})")
    if convert.ext_of(path) in store.CAMERA_EXT:
        clear(db, cfg, rid)
        raise jobs.Skip("camera RAW and HEIC photos aren't enhanced (anytopdf reads them as they are)")
    img = _open(path)
    if img is None:
        clear(db, cfg, rid)
        raise jobs.Skip("it has several frames (a multi-page TIFF or an animation): only single pictures are enhanced")
    heights = None
    if opts["only_faces"] or opts["min_face_px"]:
        heights, why = _face_heights(db, cfg, rec["space"], img)
        if opts["only_faces"] and not force and not heights:
            clear(db, cfg, rid)
            raise jobs.Skip(f"{why} (enhance.only_faces)" if heights is None else "there's no face in it (enhance.only_faces)")
    m = measure(img)
    if heights is not None:
        m["faces"] = len(heights)
        if heights:
            m["smallest_face"] = min(heights)
    low, scale = assess(m, heights, opts)
    now = store.now()
    if scale == 1 and set(low) <= {"small", "small_faces"}:  # only enlarging would help, and max_scale is 1
        low = []
    if not low and not force:
        for p in files(cfg, rid):
            p.unlink(missing_ok=True)
        _keep(db, rid, {"kind": "image", "low": [], "measured": m, "enhanced": False, "at": now})
        return say(f"good enough as it is ({_said(m)})")
    out, engine = enhance_image(cfg, img, low, scale, say)
    buf = io.BytesIO()
    out.save(buf, "JPEG", quality=QUALITY)
    keyring.keep(db, cfg, rec["space"], enhanced_path(cfg, rid), buf.getvalue())
    _keep(
        db,
        rid,
        {
            "kind": "image",
            "low": low,
            "measured": m,
            "enhanced": True,
            "engine": engine,
            "scale": scale,
            "width": out.width,
            "height": out.height,
            "forced": force or None,
            "at": now,
        },
    )
    why = ", ".join(REASONS[x] for x in low) if low else "on request"
    say(f"enhanced ({why}): {m['width']}×{m['height']} to {out.width}×{out.height} by {engine}")


def _open(path):
    """An image file as one flat RGB picture (documents.py's way: its EXIF orientation, transparency on white), or
    None when it has several frames."""
    from PIL import Image

    from . import documents

    old, Image.MAX_IMAGE_PIXELS = Image.MAX_IMAGE_PIXELS, documents.MAX_PIXELS
    try:
        with Image.open(path) as im:
            if getattr(im, "n_frames", 1) > 1:
                return None
            return documents._flat(im)
    except Image.DecompressionBombError:
        raise ValueError(f"this image is too large to open (more than {documents.MAX_PIXELS // 1_000_000} million pixels)") from None
    except OSError as e:
        raise ValueError(f"this image can't be read ({e})") from None
    finally:
        Image.MAX_IMAGE_PIXELS = old


def queue(db, rid, by=None):
    """Enhance an image whatever the settings and its quality, then run what reads it again: the steps of its pipeline
    after enhance (the read step, faces, objects, …). The job's id."""
    from . import content_types, pipelines

    rec = db.one("SELECT space, source FROM $r", r=R("recording", rid))
    if not rec:
        raise KeyError(rid)
    if rec.get("source") != "image":
        raise ValueError("only images are enhanced so far")
    steps, _ = pipelines.resolve(db, rec["space"], None, content_types.of_recording(db, rid)[0])
    rest = [s for s in steps if (s if isinstance(s, str) else s.get("type")) != "enhance"]
    return jobs.add_steps(db, rid, [{"type": "enhance", "force": True}, *rest], by)
