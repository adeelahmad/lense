"""A docTR for tests, shaped like the real one (doctr.io.DocumentFile, doctr.models.ocr_predictor, and its result's
pages, blocks, lines and words, with boxes as fractions of the page): on any page it reads two blocks, a report's title
and first line, and a place and a year."""

from __future__ import annotations

import sys
import types


def _line(*words):
    """A line of (text, confidence, x0, y0, x1, y1) words, its box around theirs."""
    ws = [types.SimpleNamespace(value=v, confidence=c, geometry=((x0, y0), (x1, y1))) for v, c, x0, y0, x1, y1 in words]
    x0, y0 = min(w.geometry[0][0] for w in ws), min(w.geometry[0][1] for w in ws)
    x1, y1 = max(w.geometry[1][0] for w in ws), max(w.geometry[1][1] for w in ws)
    return types.SimpleNamespace(words=ws, geometry=((x0, y0), (x1, y1)))


BLOCKS = [
    types.SimpleNamespace(
        lines=[
            _line(("The", 0.99, 0.1, 0.1, 0.16, 0.15), ("harbour", 0.97, 0.17, 0.1, 0.3, 0.15), ("report", 0.95, 0.31, 0.1, 0.42, 0.15)),
            _line(("Ships", 0.9, 0.1, 0.2, 0.18, 0.25), ("arrived", 0.8, 0.19, 0.2, 0.3, 0.25), ("at", 0.9, 0.31, 0.2, 0.34, 0.25)),
        ]
    ),
    types.SimpleNamespace(lines=[_line(("Galway", 0.9, 0.6, 0.8, 0.72, 0.85), ("1921", 0.7, 0.73, 0.8, 0.8, 0.85))]),
]


def install(monkeypatch):
    """Puts the fake in sys.modules; what it was asked is in the dict returned (models: the detector, recogniser and
    pretrained it was loaded with; read: the pages it was given)."""
    from app.domain import video

    calls: dict = {"read": []}

    def ocr_predictor(det_arch, reco_arch, pretrained):
        calls["models"] = (det_arch, reco_arch, pretrained)
        return lambda doc: types.SimpleNamespace(pages=[types.SimpleNamespace(blocks=BLOCKS)])

    io = types.ModuleType("doctr.io")
    io.DocumentFile = types.SimpleNamespace(from_images=lambda paths: calls["read"].extend(paths) or paths)  # type: ignore[attr-defined]
    models = types.ModuleType("doctr.models")
    models.ocr_predictor = ocr_predictor  # type: ignore[attr-defined]
    pkg = types.ModuleType("doctr")
    pkg.__path__ = []  # a package
    for name, mod in (("doctr", pkg), ("doctr.io", io), ("doctr.models", models)):
        monkeypatch.setitem(sys.modules, name, mod)
    video._doctr_predictor.cache_clear()
    return calls
