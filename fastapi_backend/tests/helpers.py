"""Shared test data and helpers (ported from the prototype's suite)."""

from __future__ import annotations

import pathlib
import wave

import numpy as np

PODS1 = """Alice|N|Welcome back. Today we talk about Dyno Therapeutics and how they design a capsid with machine learning.
Bob|Surprise|Wow, really? Dyno Therapeutics trained a model to design the capsid itself?
Alice|N|Yes. The capsid model beat the human designs on the benchmark by a wide margin.
Bob|N|That changes how gene therapy teams at Dyno Therapeutics plan their experiments.
Alice|Happy|It does, and the capsid results were published last spring.
Bob|N|Let us <!--<script>alert(1)</script> move on to the next story now."""
PODS2 = """Alice|N|Second episode. Carol joins us to talk about the exploit benchmark.
Carol|N|The exploit benchmark measures how often a model finishes a working exploit.
Alice|Fear|That is frightening if the exploit rate keeps climbing.
Carol|N|The benchmark team also looked at Dyno Therapeutics as a case study for safety reviews."""
CALLS = """[00:01] Alice: Hi, it's Alice from the lab, calling about the Dyno Therapeutics order.
[00:09] Dave: Thanks, the Dyno Therapeutics shipment leaves on Friday.
[00:15] Alice: Great, please send the capsid samples too."""


def unit(v):
    return v / np.linalg.norm(v)


def write_wav(path, seconds=3.0, sr=16000):
    t = np.arange(int(seconds * sr)) / sr
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes((0.3 * np.sin(2 * np.pi * 220 * t) * 32767).astype(np.int16).tobytes())


def write_docx(path, paras):
    try:
        import docx

        d = docx.Document()
        for p in paras:
            d.add_paragraph(p)
        d.save(str(path))
    except ImportError:
        import zipfile
        from xml.sax.saxutils import escape

        body = "".join(f'<w:p><w:r><w:t xml:space="preserve">{escape(p)}</w:t></w:r></w:p>' for p in paras)
        with zipfile.ZipFile(path, "w") as z:
            z.writestr(
                "word/document.xml",
                f'<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>{body}</w:body></w:document>',
            )


def write_pdf(path, lines):
    content = "BT /F1 11 Tf 60 740 Td 15 TL " + " ".join(f"({l.replace('(', '').replace(')', '')}) Tj T*" for l in lines) + " ET"
    objs = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        f"<< /Length {len(content)} >>\nstream\n{content}\nendstream",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out, offs = "%PDF-1.4\n", []
    for i, o in enumerate(objs, 1):
        offs.append(len(out))
        out += f"{i} 0 obj\n{o}\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n" + "".join(f"{o:010d} 00000 n \n" for o in offs)
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n"
    pathlib.Path(path).write_bytes(out.encode("latin-1"))


def text_pdf(pages, size=(612, 792)):
    """A PDF whose pages hold these paragraphs (each a string; newlines break lines), in Helvetica 14 pt, as bytes."""
    objs = ["<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    contents = []
    for paras in pages:
        y, ops = size[1] - 72, []
        for para in paras:
            for line in para.split("\n"):
                text = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
                ops.append(f"BT /F1 14 Tf 72 {y} Td ({text}) Tj ET")
                y -= 18
            y -= 24
        data = "\n".join(ops)
        objs.append(f"<< /Length {len(data)} >>\nstream\n{data}\nendstream")
        contents.append(len(objs))
    tree = len(objs) + len(pages) + 1
    kids = []
    for c in contents:
        objs.append(
            f"<< /Type /Page /Parent {tree} 0 R /MediaBox [0 0 {size[0]} {size[1]}] /Contents {c} 0 R /Resources << /Font << /F1 1 0 R >> >> >>"
        )
        kids.append(len(objs))
    objs.append(f"<< /Type /Pages /Kids [{' '.join(f'{k} 0 R' for k in kids)}] /Count {len(kids)} >>")
    objs.append(f"<< /Type /Catalog /Pages {tree} 0 R >>")
    out, offs = "%PDF-1.4\n", []
    for i, o in enumerate(objs, 1):
        offs.append(len(out))
        out += f"{i} 0 obj\n{o}\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n" + "".join(f"{o:010d} 00000 n \n" for o in offs)
    out += f"trailer\n<< /Size {len(objs) + 1} /Root {len(objs)} 0 R >>\nstartxref\n{xref}\n%%EOF\n"
    return out.encode("latin-1")


def scan(lines, size=(1400, 1000), mode="RGB"):
    """A page of large printed lines, as a scanner would see it (a PIL image)."""
    from PIL import Image, ImageDraw, ImageFont

    font = ImageFont.load_default(size=56)
    img = Image.new(mode, size, "white")
    draw = ImageDraw.Draw(img)
    for k, line in enumerate(lines):
        draw.text((80, 120 + 140 * k), line, fill="black", font=font)
    return img


def quiet(*_a, **_k):
    return None


def manifests(get, url):
    """The Manifests a IIIF Collection lists, with its collections' (`get` fetches a path, e.g. a client's get)."""
    import urllib.parse

    out, todo = [], [url]
    while todo:
        for x in get(todo.pop(0)).json().get("items", []):
            if x["type"] == "Manifest":
                out.append(x["id"])
            elif x["type"] == "Collection":
                todo.append(urllib.parse.urlsplit(x["id"]).path)
    return out


def seed(db, cfg, folder: pathlib.Path):
    """Three transcripts: two in 'pods' (shared graph), one in 'calls' (isolated). Returns their recording ids."""
    from app.domain import analyze, ingest

    ids = []
    for ns, name, text in (("pods", "ep1.txt", PODS1), ("pods", "ep2.txt", PODS2), ("calls", "call.txt", CALLS)):
        p = folder / name
        p.write_text(text)
        ids.append(ingest.import_transcript(db, cfg, ns, p, log=quiet))
    analyze.analyze_pending(db, cfg, log=quiet)
    return ids


def speaker_names(db, ns):
    from app.domain import speakers, store

    return {s["display"] for s in speakers.list_speakers(db, store.ns_id(db, ns))}


def drain(db, cfg):
    """Run every queued job in this process."""
    from app.domain import jobs

    return jobs.Worker(db, lambda: cfg).drain()


def make_user(db, email, password, admin=False, roles=None):
    """An account plus namespace roles, e.g. roles={"pods": "editor"}."""
    from app.domain import auth, store

    uid = auth.create_account(db, email, password, admin=admin)
    for ns, role in (roles or {}).items():
        auth.set_role(db, uid, store.ns_id(db, ns), role)
    return uid


def login(client, email, password):
    """Sign in and return the Authorization header."""
    r = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}
