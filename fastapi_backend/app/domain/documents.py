"""Documents and images as resources: their pages, and the text on each.

A PDF's pages are drawn by poppler's pdftoppm and their text read by pdftotext, block by block with where each block
is on the page; without poppler, pypdf reads the text alone (there are no pages to look at then). A page with hardly
any text (a scan) is read by the OCR engine of video.ocr_engine from a sharper drawing of it. An image is one page (a
TIFF one page per frame), read by OCR. The pages are JPEGs in data_dir/frames/<resource>/ (page-0001.jpg, and
thumb-0001.jpg to show them small), served like a video's frames; their text becomes the resource's text, one segment
per block, with its page and where it is on it (a box of fractions of the page). Segments have times all the same,
a reading pace as for transcripts without times, so everything that reads transcripts reads documents too.
"""

from __future__ import annotations

import os
import pathlib
import re
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET

from . import convert, ingest, keyring, store, video, webcapture

R = store.R
TYPES = {
    ".pdf": "application/pdf",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".bmp": "image/bmp",
}
KINDS = ("document", "image")
EXT = store.DOCUMENT_EXT + store.IMAGE_EXT
WORD_MS = 385  # the reading pace segments are given (as for transcripts without times)
BATCH = 20  # pages drawn at a time
OCR_DPI = 300  # how sharp a page is drawn for OCR
MIN_CONF = 35  # OCR blocks read with less confidence (0-100) are left out
MAX_PIXELS = 250_000_000  # images larger than this aren't opened
XHTML = "{http://www.w3.org/1999/xhtml}"


def kind_of(name):
    """'document' or 'image' for a file Lens keeps as one, else None."""
    ext = pathlib.PurePosixPath(str(name or "")).suffix.lower()
    return "document" if ext in store.DOCUMENT_EXT else "image" if ext in store.IMAGE_EXT else None


def content_type(name):
    ext = pathlib.PurePosixPath(str(name or "")).suffix.lower()
    return TYPES.get(ext) or convert.TYPES.get(ext)


def page_name(i):
    return f"page-{i + 1:04d}.jpg"


def thumb_name(i):
    return f"thumb-{i + 1:04d}.jpg"


def pages(db, rid):
    """A resource's pages in order: {idx, width, height, image, thumb, text, chars, label}."""
    return db.rows("SELECT idx, width, height, image, thumb, text, chars, label FROM page WHERE recording = $r ORDER BY idx", r=rid)


# ---------- text into blocks ----------
def _clean(text):
    """A block's lines as one paragraph: words broken across lines joined, spaces made single."""
    t = re.sub(r"(\w)-\s*\n\s*(\w)", r"\1\2", text)
    return re.sub(r"\s+", " ", t).strip()


def _box(x0, y0, x1, y1, w, h):
    """[x, y, w, h] as fractions of the page, kept inside it."""
    if not (w and h):
        return None
    x0, x1 = max(0.0, min(x0, x1)), min(float(w), max(x0, x1))
    y0, y1 = max(0.0, min(y0, y1)), min(float(h), max(y0, y1))
    return [round(x0 / w, 4), round(y0 / h, 4), round((x1 - x0) / w, 4), round((y1 - y0) / h, 4)]


def bbox_pages(xhtml):
    """pdftotext -bbox-layout's pages: [[{text, box}]] in reading order (its flows and blocks)."""
    root = ET.fromstring(xhtml)
    out = []
    for page in root.iter(XHTML + "page"):
        w, h = float(page.get("width") or 0), float(page.get("height") or 0)
        blocks = []
        for block in page.iter(XHTML + "block"):
            lines = [" ".join((wd.text or "").strip() for wd in line.iter(XHTML + "word")) for line in block.iter(XHTML + "line")]
            text = _clean("\n".join(x for x in lines if x.strip()))
            if text:
                box = _box(*(float(block.get(k) or 0) for k in ("xMin", "yMin", "xMax", "yMax")), w, h)
                blocks.append({"text": text, "box": box})
        out.append(blocks)
    return out


def plain_blocks(text, most=800):
    """Text without positions (pypdf's) in blocks: its paragraphs, long ones cut at line ends."""
    out = []
    for para in re.split(r"\n\s*\n", text or ""):
        cur = ""
        for line in para.splitlines():
            if cur and len(cur) + len(line) > most:
                out.append(cur)
                cur = ""
            cur += line + "\n"
        if cur.strip():
            out.append(cur)
    return [{"text": t, "box": None} for t in (_clean(x) for x in out) if t]


def merge_lines(lines):
    """OCR lines ({text, conf, box}) as paragraphs: a line goes with the paragraph whose last line it starts close below,
    overlapping it side to side (the nearest, when there are several); paragraphs in the order they start."""
    out = []
    for ln in sorted(lines, key=lambda x: (x["box"][1], x["box"][0])):
        x, y, w, h = ln["box"]
        best = None
        for p in out:
            lx, ly, lw, lh = p["last"]
            gap = y - (ly + lh)
            if -0.5 * h <= gap <= 0.9 * max(h, lh) and x < lx + lw and lx < x + w and (best is None or gap < best[0]):
                best = (gap, p)
        if best is None:
            out.append({"text": ln["text"], "confs": [ln["conf"]], "box": list(ln["box"]), "last": list(ln["box"])})
            continue
        p = best[1]
        bx, by, bw, bh = p["box"]
        p["text"] += "\n" + ln["text"]
        p["confs"].append(ln["conf"])
        p["box"] = [min(bx, x), by, max(bx + bw, x + w) - min(bx, x), max(by + bh, y + h) - by]
        p["last"] = list(ln["box"])
    return [{"text": p["text"], "conf": sum(p["confs"]) / len(p["confs"]), "box": [round(v, 4) for v in p["box"]]} for p in out]


def ocr_blocks(engine, path):
    """What an OCR engine reads on a page, as blocks: Tesseract's own paragraphs, else its lines put together."""
    found = engine.paragraphs(path) if hasattr(engine, "paragraphs") else merge_lines(engine.lines(path))
    out = []
    for p in found:
        text = _clean(p["text"])
        if p["conf"] < MIN_CONF or len(text) < 2 or sum(c.isalnum() for c in text) < 0.4 * len(text):
            continue
        out.append({"text": text, "box": p["box"]})
    return out


# ---------- drawing pages ----------
def _poppler():
    return shutil.which("pdftoppm"), shutil.which("pdftotext")


def page_count(path):
    """How many pages a PDF has (ValueError when it can't be read)."""
    info = shutil.which("pdfinfo")
    if info:
        out = subprocess.run([info, str(path)], capture_output=True, text=True, timeout=120)
        m = re.search(r"^Pages:\s+(\d+)", out.stdout, re.M)
        if m:
            return int(m.group(1))
    try:
        from pypdf import PdfReader

        return len(PdfReader(str(path)).pages)
    except Exception as e:  # noqa: BLE001 - pypdf raises many kinds of errors on a broken file
        raise ValueError(f"this PDF can't be read ({type(e).__name__}: {e})") from None


def _labels(path, n):
    """The PDF's own page labels (iv, A-1, …) where they differ from the page numbers; {} without them."""
    try:
        from pypdf import PdfReader

        labels = list(PdfReader(str(path)).page_labels)[:n]
    except Exception:  # noqa: BLE001 - labels are a nicety
        return {}
    return {i: str(lab) for i, lab in enumerate(labels) if str(lab) != str(i + 1)}


def _save_page(img, d, i, opts):
    """Keep a drawn page as page-NNNN.jpg (its longest side at most documents.page_pixels) and its thumbnail."""
    from PIL import Image

    img = img.convert("RGB")
    img.thumbnail((opts["page_pixels"], opts["page_pixels"]), Image.LANCZOS)
    img.save(d / page_name(i), quality=85)
    small = img.copy()
    small.thumbnail((opts["thumb_pixels"], opts["thumb_pixels"]), Image.LANCZOS)
    small.save(d / thumb_name(i), quality=80)
    return img.size


def draw_pages(pdftoppm, path, d, first, last, opts):
    """Draw pages first..last (from 0) of a PDF: {page: (width, height)} of those drawn."""
    from PIL import Image

    sizes = {}
    with tempfile.TemporaryDirectory(dir=d) as tmp:
        subprocess.run(
            [
                pdftoppm,
                "-jpeg",
                "-jpegopt",
                "quality=90",
                "-scale-to",
                str(opts["page_pixels"]),
                "-f",
                str(first + 1),
                "-l",
                str(last + 1),
                str(path),
                f"{tmp}/p",
            ],
            capture_output=True,
            timeout=1800,
        )
        for f in pathlib.Path(tmp).glob("p-*.jpg"):
            i = int(f.stem.rsplit("-", 1)[1]) - 1
            with Image.open(f) as img:
                sizes[i] = _save_page(img, d, i, opts)
    return sizes


def _sharp(pdftoppm, path, i, tmp):
    """Page i drawn at OCR_DPI in grey, for OCR; None when it couldn't be."""
    subprocess.run(
        [pdftoppm, "-gray", "-png", "-r", str(OCR_DPI), "-f", str(i + 1), "-l", str(i + 1), str(path), f"{tmp}/o"],
        capture_output=True,
        timeout=600,
    )
    found = sorted(pathlib.Path(tmp).glob("o-*.png"))
    return found[0] if found else None


def _pdf_text(pdftotext, path, first, last):
    """Blocks of text on pages first..last (from 0): pdftotext's, with where they are, else pypdf's."""
    if pdftotext:
        out = subprocess.run(
            [pdftotext, "-bbox-layout", "-enc", "UTF-8", "-f", str(first + 1), "-l", str(last + 1), str(path), "-"],
            capture_output=True,
            timeout=1800,
        )
        if out.returncode == 0:
            try:
                found = bbox_pages(out.stdout.decode("utf-8", "replace"))
                return {first + k: blocks for k, blocks in enumerate(found)}
            except ET.ParseError:
                pass
    try:
        from pypdf import PdfReader

        reader = PdfReader(str(path))
        return {i: plain_blocks(reader.pages[i].extract_text() or "") for i in range(first, last + 1)}
    except Exception:  # noqa: BLE001 - a page pypdf can't read has no text
        return {i: [] for i in range(first, last + 1)}


def read_pdf(path, d, opts, engine, say, why="no OCR engine is available"):
    """Draw a PDF's pages into d and read their text: (pages, blocks by page, notes). `why` there's no OCR engine."""
    n = page_count(path)
    total, n = n, min(n, opts["max_pages"])
    pdftoppm, pdftotext = _poppler()
    labels = _labels(path, n)
    pages, blocks, notes, ocred = [], {}, [], 0
    if not pdftoppm:
        notes.append("its pages couldn't be drawn: poppler-utils (pdftoppm) isn't installed")
    for first in range(0, n, BATCH):
        last = min(n, first + BATCH) - 1
        sizes = draw_pages(pdftoppm, path, d, first, last, opts) if pdftoppm else {}
        text = _pdf_text(pdftotext, path, first, last)
        for i in range(first, last + 1):
            found, how = text.get(i) or [], "pdf"
            chars = sum(len(b["text"]) for b in found)
            if chars < opts["ocr_below_chars"] and engine and pdftoppm:
                with tempfile.TemporaryDirectory(dir=d) as tmp:
                    sharp = _sharp(pdftoppm, path, i, tmp)
                    read = ocr_blocks(engine, sharp) if sharp else []
                if read:
                    found, how, ocred = read, "ocr", ocred + 1
                    chars = sum(len(b["text"]) for b in found)
            size = sizes.get(i)
            pages.append(
                store.clean(
                    {
                        "idx": i,
                        "width": size[0] if size else None,
                        "height": size[1] if size else None,
                        "image": page_name(i) if size else None,
                        "thumb": thumb_name(i) if size else None,
                        "text": how if found else None,
                        "chars": chars,
                        "label": labels.get(i),
                    }
                )
            )
            blocks[i] = found
        if n > BATCH:
            say(f"read {last + 1} of {n} pages")
    scanned = sum(1 for p in pages if p.get("chars", 0) < opts["ocr_below_chars"])
    if scanned and not engine:
        notes.append(f"{scanned} page(s) without text weren't read: {why}")
    if total > n:
        notes.append(f"only the first {n} of its {total} pages were read (documents.max_pages)")
    return pages, blocks, notes, ocred


def _frames(img, path):
    """The frames of an image: every page of a TIFF, else the image itself."""
    from PIL import ImageSequence

    if pathlib.Path(path).suffix.lower() in (".tif", ".tiff"):
        return ImageSequence.Iterator(img)
    return [img]


def _flat(img):
    """An image as RGB: orientation from its EXIF, transparency on white, 16-bit and float images scaled to 8 bits."""
    from PIL import Image, ImageOps

    img = ImageOps.exif_transpose(img)
    if img.mode in ("I;16", "I;16B", "I;16L", "I", "F"):
        import numpy as np

        a = np.asarray(img, dtype=np.float64)
        hi = a.max() or 1.0
        img = Image.fromarray((a * (255.0 / hi)).clip(0, 255).astype("uint8"), "L")
    if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
        rgba = img.convert("RGBA")
        bg = Image.new("RGB", rgba.size, "white")
        bg.paste(rgba, mask=rgba.split()[-1])
        return bg
    return img.convert("RGB")


def read_image(path, d, opts, engine, say, why="no OCR engine is available"):
    """An image as pages (a TIFF's frames) in d, read by OCR: (pages, blocks by page, notes). `why` there's no OCR
    engine."""
    from PIL import Image

    old, Image.MAX_IMAGE_PIXELS = Image.MAX_IMAGE_PIXELS, MAX_PIXELS
    try:
        img = Image.open(path)
        pages, blocks, notes = [], {}, []
        for i, frame in enumerate(_frames(img, path)):
            if i >= opts["max_pages"]:
                notes.append(f"only its first {opts['max_pages']} pages were read (documents.max_pages)")
                break
            flat = _flat(frame)
            size = _save_page(flat.copy(), d, i, opts)
            found = []
            if engine:
                with tempfile.TemporaryDirectory(dir=d) as tmp:
                    sharp = flat.copy()
                    sharp.thumbnail((3000, 3000), Image.LANCZOS)
                    sharp.save(f"{tmp}/o.png")
                    found = ocr_blocks(engine, f"{tmp}/o.png")
            chars = sum(len(b["text"]) for b in found)
            pages.append(
                {
                    "idx": i,
                    "width": size[0],
                    "height": size[1],
                    "image": page_name(i),
                    "thumb": thumb_name(i),
                    "text": "ocr" if found else None,
                    "chars": chars,
                }
            )
            blocks[i] = found
    except Image.DecompressionBombError:
        raise ValueError(f"this image is too large to open (more than {MAX_PIXELS // 1_000_000} million pixels)") from None
    except OSError as e:
        raise ValueError(f"this image can't be read ({e})") from None
    finally:
        Image.MAX_IMAGE_PIXELS = old
    if not engine:
        notes.append(f"its text wasn't read: {why}")
    return pages, blocks, notes, len(pages) if engine else 0


# ---------- the transcribe step for documents and images ----------
def segments_of(blocks):
    """Blocks by page as segments in page order, each with its page, its box and a reading-pace time."""
    out, t = [], 0
    for page in sorted(blocks):
        for b in blocks[page]:
            dur = WORD_MS * max(1, len(b["text"].split()))
            out.append(store.clean({"t0": t, "t1": t + dur, "text": b["text"], "page": page, "box": b.get("box")}))
            t += dur
    return out


def _named(rec, path, learnt):
    """An email's subject as its title and its date as when it was made, unless someone named or dated it otherwise:
    an upload is titled after its file and dated by it until then."""
    out = {}
    stem = pathlib.PurePosixPath(str(path)).stem
    if learnt.get("title") and (not rec.get("title") or rec["title"] == stem):
        out["title"] = learnt["title"][:200]
    if learnt["email"].get("date"):
        out["recorded_at"] = learnt["email"]["date"]
    return out


def _clear(d):
    for pattern in ("page-*.jpg", "thumb-*.jpg"):
        for f in d.glob(pattern):
            f.unlink(missing_ok=True)


def transcribe(db, cfg, rid, say):
    """Draw a document's or an image's pages and read their text into its segments (the transcribe step for them). A
    document that isn't a PDF is made into one first (convert.py): its rendition, which its pages come from; an email
    also gives its sender, recipients, date and subject, and its attachments are kept."""
    rec = db.one("SELECT space, path, remote, source, title, recorded_at, web FROM $r", r=R("recording", rid)) or {}
    if rec.get("web"):  # a web page: captured the first time (webcapture.py)
        try:
            path = webcapture.ensure(db, cfg, rid, rec, say)
        except convert.Unavailable as e:
            raise ValueError(f"this web page can't be captured here: {e}") from None
    else:
        path = ingest.audio_path(db, cfg, rec)
    if not path or not os.path.exists(path):
        raise FileNotFoundError(f"its file isn't there ({rec.get('path')})")
    opts, (engine, why) = cfg["documents"], video.ocr_engine_why(cfg)
    learnt, pdf = {}, path
    if rec["source"] == "document" and convert.needs(path):
        pdf = convert.rendition_path(cfg, rid)
        try:
            learnt = convert.to_pdf(cfg, path, pdf)
        except convert.Unavailable as e:
            raise ValueError(f"this {convert.word(path)} can't be read here: {e}") from None
        say(f"made into a PDF by {'LibreOffice' if learnt['by'] == 'libreoffice' else 'Chromium'}")
    d = video.frames_dir(cfg, rid)
    d.mkdir(parents=True, exist_ok=True)
    _clear(d)
    read = read_image if rec["source"] == "image" else read_pdf
    pages, blocks, notes, ocred = read(pdf, d, opts, engine, say, why)
    if learnt:  # the rendition, read: kept encrypted from here on, like the file it was made from
        keyring.protect(db, cfg, rec["space"], pdf)
    keyring.protect_folder(db, cfg, rec["space"], d)  # and the pages drawn from it
    segs = segments_of(blocks)
    rows = [{**p, "id": R("page", f"{rid}-{p['idx']}"), "recording": rid, "space": rec["space"]} for p in pages]
    db.run(
        ["FOR $p IN $rows { UPSERT $p.id CONTENT $p; }", "DELETE page WHERE recording = $rid AND idx >= $n"],
        rows=rows,
        rid=rid,
        n=len(rows),
    )
    first = next((p for p in pages if p.get("width")), {})
    media = store.clean({"kind": rec["source"], "pages": len(pages), "width": first.get("width"), "height": first.get("height")})
    how = ("pdf" if rec["source"] == "document" else "image") + (f"+ocr:{engine.name}" if ocred and engine else "")
    patch = {"status": "transcribed", "engine": how, "media": media, "transcribed_at": store.now()}
    if learnt:
        patch["rendition"] = {"from": convert.ext_of(path), "by": learnt["by"]}
    if learnt.get("email"):
        patch["email"] = learnt["email"]
        patch.update(_named(rec, path, learnt))
    ingest.write_transcript(db, rid, rec["space"], segs, patch)
    for note in notes:
        say(note)
    if learnt.get("attachments") is not None:
        convert.keep_attachments(db, cfg, rid, learnt["attachments"], say)
    by_ocr = f", {ocred} read by OCR ({engine.name})" if ocred and engine else ""
    say(f"{len(pages)} page(s){by_ocr}, {len(segs)} block(s) of text")
