"""OCR engines (docs/configuration.md#video-ocr-faces-and-objects): docTR's lines and blocks read the way the other
engines' are, and why there's no OCR when there isn't."""

from __future__ import annotations

import importlib.abc
import sys

import pytest

from app.domain import video
from tests import fake_doctr


@pytest.fixture
def doctr(monkeypatch):
    calls = fake_doctr.install(monkeypatch)
    yield calls
    video._doctr_predictor.cache_clear()


def test_doctr_reads_lines_and_blocks(cfg, doctr, tmp_path):
    cfg["video"]["ocr_engine"] = "doctr"
    engine, why = video.ocr_engine_why(cfg)
    assert (engine.name, why, doctr["models"]) == ("doctr", None, ("fast_base", "crnn_vgg16_bn", True))
    page = tmp_path / "page.png"
    lines = engine.lines(page)
    assert [(x["text"], round(x["conf"], 1), x["box"]) for x in lines] == [
        ("The harbour report", 97.0, [0.1, 0.1, 0.32, 0.05]),
        ("Ships arrived at", 86.7, [0.1, 0.2, 0.24, 0.05]),
        ("Galway 1921", 80.0, [0.6, 0.8, 0.2, 0.05]),
    ]
    assert doctr["read"] == [str(page)]
    blocks = engine.paragraphs(page)  # as docTR laid them out: what documents read their scans by
    assert [(x["text"], x["box"]) for x in blocks] == [
        ("The harbour report\nShips arrived at", [0.1, 0.1, 0.32, 0.15]),
        ("Galway 1921", [0.6, 0.8, 0.2, 0.05]),
    ]
    assert round(blocks[0]["conf"], 1) == 91.8  # its words', on average
    assert video.ocr_engine_why(cfg)[0].model is engine.model  # loaded once


def test_why_there_is_no_ocr(cfg, monkeypatch):
    cfg["video"]["ocr_engine"] = "none"
    assert video.ocr_engine_why(cfg) == (None, "OCR is off (video.ocr_engine)")
    cfg["video"]["ocr_engine"] = "magic"
    assert video.ocr_engine_why(cfg) == (None, "there's no OCR engine called magic")
    cfg["video"]["ocr_engine"] = "doctr"
    monkeypatch.setitem(sys.modules, "doctr", None)  # not installed
    assert video.ocr_engine_why(cfg) == (None, 'docTR isn\'t installed (pip install "lens[doctr]"; it brings PyTorch)')
    cfg["video"]["ocr_engine"] = "rapidocr"
    monkeypatch.setitem(sys.modules, "rapidocr_onnxruntime", None)
    assert video.ocr_engine_why(cfg) == (None, 'RapidOCR isn\'t installed (pip install "lens[rapidocr]")')
    monkeypatch.setattr(video.shutil, "which", lambda name: None)
    cfg["video"]["ocr_engine"] = "tesseract"
    assert video.ocr_engine_why(cfg) == (None, "Tesseract isn't installed")
    monkeypatch.setattr(video.platform, "system", lambda: "Linux")
    cfg["video"]["ocr_engine"] = "auto"  # none of the ones auto looks for
    assert video.ocr_engine_why(cfg) == (None, "no OCR engine is available: install Tesseract (video.ocr_engine)")


class _NoLibGL(importlib.abc.MetaPathFinder):
    """docTR installed, but OpenCV (which it imports) can't find libGL, as on a slim image without it."""

    def find_spec(self, name, path, target=None):
        if name == "doctr.models":
            raise ImportError("libGL.so.1: cannot open shared object file: No such file or directory")
        return None


def test_a_doctr_that_cant_read_says_why(cfg, doctr, monkeypatch):
    cfg["video"]["ocr_engine"] = "doctr"

    def offline(**_kw):
        raise OSError("HTTP Error 403: Forbidden")

    monkeypatch.setattr(sys.modules["doctr.models"], "ocr_predictor", offline)  # its models can't be fetched
    assert video.ocr_engine_why(cfg) == (None, "docTR couldn't load its models (HTTP Error 403: Forbidden)")
    monkeypatch.delitem(sys.modules, "doctr.models")
    monkeypatch.setattr(sys, "meta_path", [_NoLibGL(), *sys.meta_path])
    why = "docTR can't be loaded (libGL.so.1: cannot open shared object file: No such file or directory)"
    assert video.ocr_engine_why(cfg) == (None, why)
