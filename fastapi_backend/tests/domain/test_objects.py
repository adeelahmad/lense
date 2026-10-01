"""Object detection (docs/api.md#objects): YOLOX's output read the way its own demo reads it (grids, strides, kinds,
non-maximum suppression), on a small model of YOLOX's shape whose answer is known; Ultralytics through its own API;
and detections kept per kind with where they are."""

from __future__ import annotations

import math
import sys
import types

import numpy as np
import pytest
from PIL import Image

from app.domain import objects

SIZE = 128  # the small model's input: 16x16 + 8x8 + 4x4 = 336 cells, at strides 8, 16 and 32
STRIDES = (8, 16, 32)
CELLS = sum((SIZE // s) ** 2 for s in STRIDES)


def _cell(stride, cx, cy):
    """The grid cell a centre falls in: its index in the output, and its column and row."""
    gx, gy = int(cx // stride), int(cy // stride)
    before = sum((SIZE // s) ** 2 for s in STRIDES if s < stride)
    return before + gy * (SIZE // stride) + gx, gx, gy


def _raw(stride, cx, cy, w, h, obj, kind, p):
    """One YOLOX prediction as the model gives it: offsets in its grid cell, log sizes, then scores."""
    cell, gx, gy = _cell(stride, cx, cy)
    row = np.zeros(85, np.float32)
    row[:4] = [cx / stride - gx, cy / stride - gy, math.log(w / stride), math.log(h / stride)]
    row[4], row[5 + kind] = obj, p
    return cell, row


FOUND = (  # in pixels of the model's input
    (8, 48, 64, 32, 48, 0.9, 0, 0.95),  # a person
    (8, 58, 64, 32, 48, 0.8, 0, 0.9),  # the same person again, from the next cell: suppressed
    (16, 96, 32, 48, 32, 0.7, 16, 0.8),  # a dog
    (16, 40, 104, 32, 32, 0.3, 2, 0.5),  # a car, too unsure to keep
)


def _predictions(decoded=False):
    pred = np.zeros((CELLS, 85), np.float32)
    for stride, cx, cy, w, h, obj, kind, p in FOUND:
        cell, row = _raw(stride, cx, cy, w, h, obj, kind, p)
        if decoded:  # as a model exported with its decoding built in answers: pixels
            row[:4] = [cx, cy, w, h]
        pred[cell] = row
    return pred


def _model(path, pred):
    """A model shaped like YOLOX's export (input 1x3xSIZExSIZE, output 1xCELLSx85) that always answers `pred`."""
    import onnx
    from onnx import TensorProto, helper, numpy_helper

    node = helper.make_node("Constant", inputs=[], outputs=["output"], value=numpy_helper.from_array(pred[None], name="pred"))
    graph = helper.make_graph(
        [node],
        "yolox-shaped",
        [helper.make_tensor_value_info("images", TensorProto.FLOAT, [1, 3, SIZE, SIZE])],
        [helper.make_tensor_value_info("output", TensorProto.FLOAT, [1, CELLS, 85])],
    )
    model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 13)])
    model.ir_version = 8
    onnx.save(model, str(path))
    return str(path)


@pytest.fixture
def picture(tmp_path):
    p = tmp_path / "street.jpg"
    Image.new("RGB", (256, 256), (40, 90, 160)).save(p)  # twice the model's input: boxes come back doubled
    return p


def test_yolox_reads_its_model_like_its_own_demo(cfg, tmp_path, picture):
    cfg["video"]["yolox_model"] = _model(tmp_path / "yolox_test.onnx", _predictions())
    found, why = objects.engine(cfg)
    assert why is None and found.name == "yolox" and found.size == (SIZE, SIZE)
    got = found.detect(picture)
    assert [(o["label"], round(o["score"], 3)) for o in got] == [("person", 0.855), ("dog", 0.56)]
    assert got[0]["box"] == [0.25, 0.3125, 0.25, 0.375] and got[1]["box"] == [0.5625, 0.125, 0.375, 0.25]

    # a model exported with its decoding built in gives pixels: the same answer
    cfg["video"]["yolox_model"] = _model(tmp_path / "yolox_decoded.onnx", _predictions(decoded=True))
    assert objects.engine(cfg)[0].detect(picture) == got

    cfg["video"]["object_min_score"] = 0.9  # only what it's surest of
    cfg["video"]["yolox_model"] = _model(tmp_path / "yolox_test.onnx", _predictions())
    assert objects.engine(cfg)[0].detect(picture) == []


def test_pictures_are_letterboxed_as_yolox_was_trained():
    img = Image.new("RGB", (256, 128), (255, 0, 0))  # wide and red
    blob, r = objects._letterbox(img, (SIZE, SIZE))
    assert r == 0.5 and blob.shape == (1, 3, SIZE, SIZE)
    assert blob[0, :, 0, 0].tolist() == [0, 0, 255]  # blue, green, red: YOLOX's channel order
    assert blob[0, :, 80, 0].tolist() == [114, 114, 114]  # below the picture: grey
    with pytest.raises(ValueError, match="doesn't fit a YOLOX model"):
        objects._decode(np.zeros((10, 85), np.float32), (SIZE, SIZE))


def test_without_an_engine_the_step_says_why(cfg, monkeypatch, tmp_path):
    monkeypatch.setattr(objects, "MODELS", tmp_path / "none")
    assert objects.engine(cfg) == (None, "no YOLOX model: set video.yolox_model to a YOLOX .onnx file (the lens:full image has one)")
    (tmp_path / "models").mkdir()
    monkeypatch.setattr(objects, "MODELS", tmp_path / "models")
    _model(tmp_path / "models" / "yolox_s.onnx", _predictions())
    assert objects.yolox_model(cfg) == str(tmp_path / "models" / "yolox_s.onnx")  # the image's own, found
    cfg["video"]["object_engine"] = "off"
    assert objects.engine(cfg) == (None, "object detection is off (video.object_engine)")
    cfg["video"]["object_engine"] = "magic"
    assert objects.engine(cfg) == (None, "there's no object engine called magic")
    cfg["video"]["object_engine"] = "ultralytics"
    monkeypatch.setitem(sys.modules, "ultralytics", None)  # not installed
    assert objects.engine(cfg) == (None, "Ultralytics isn't installed (pip install ultralytics; it's AGPL-3.0)")


def test_ultralytics_through_its_own_api(cfg, monkeypatch, picture):
    class Boxes:
        xyxy = types.SimpleNamespace(tolist=lambda: [[12.8, 25.6, 64.0, 89.6]])
        conf = types.SimpleNamespace(tolist=lambda: [0.81])
        cls = types.SimpleNamespace(tolist=lambda: [16.0])

    class YOLO:
        def __init__(self, weights):
            self.weights = weights

        def __call__(self, path, verbose, conf):
            assert conf == 0.4 and path.endswith("street.jpg")
            return [types.SimpleNamespace(orig_shape=(128, 128), boxes=Boxes(), names={16: "dog"})]

    monkeypatch.setitem(sys.modules, "ultralytics", types.SimpleNamespace(YOLO=YOLO))
    cfg["video"].update(object_engine="ultralytics", ultralytics_model="yolov8n.pt")
    found, _ = objects.engine(cfg)
    assert found.model.weights == "yolov8n.pt"
    assert found.detect(picture) == [{"label": "dog", "score": 0.81, "box": [0.1, 0.2, 0.4, 0.5]}]


def test_detections_are_kept_per_kind():
    dets = [
        {"t": 0, "label": "Person", "score": 0.9, "box": [0.1, 0.1, 0.2, 0.4]},
        {"t": 5000, "label": "person", "score": 0.7, "box": [0.1, 0.1, 0.2, 0.4]},
        {"t": 15000, "label": "person", "score": 0.8, "box": [0.2, 0.1, 0.2, 0.4]},  # missed at 10 s: still one span
        {"t": 40000, "label": "person", "score": 0.6, "box": [0.2, 0.1, 0.2, 0.4]},
        {"t": 5000, "label": "dog", "score": 0.5, "box": [0.5, 0.5, 0.2, 0.2]},
    ]
    person, dog = objects.tracks(dets, 5000, paged=False)
    assert (person["label"], person["spans"], person["count"], person["first_ms"]) == ("person", [[0, 20000], [40000, 45000]], 4, 0)
    assert person["screen_ms"] == 25000 and person["score"] == 0.75
    assert person["boxes"][0] == [0, 0.1, 0.1, 0.2, 0.4, 0.9]
    assert (dog["label"], dog["spans"]) == ("dog", [[5000, 10000]])
    # on pages nothing is bridged: a document's p. 1 and 3 are two spans
    (page,) = objects.tracks([{**d, "t": t} for d, t in zip(dets[:2], (0, 2), strict=True)], 1, paged=True)
    assert page["spans"] == [[0, 1], [2, 3]] and page["screen_ms"] == 2
