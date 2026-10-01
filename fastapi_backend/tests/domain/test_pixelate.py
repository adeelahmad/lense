"""Pixelating faces for visitors (docs/video.md): a face found becomes blocks, a margin around it too, and the rest of
the picture stays as it is; which faces are on which picture (a sampled frame, a shot's keyframe, a page, a crop)."""

from __future__ import annotations

from PIL import Image

from app.domain import faces, store

R = store.R


def test_a_face_becomes_blocks_and_nothing_else_changes():
    img = Image.new("RGB", (200, 100))
    px = img.load()
    for x in range(200):
        for y in range(100):
            px[x, y] = (255, 255, 255) if (x + y) % 2 else (0, 0, 0)  # every pixel unlike its neighbours
    box = [0.25, 0.2, 0.3, 0.5]
    out = faces.pixelate(img.copy(), [box])
    W, H = out.size
    m = faces.MARGIN
    x0, y0 = int((box[0] - box[2] * m) * W), int((box[1] - box[3] * m) * H)
    x1, y1 = int((box[0] + box[2] * (1 + m)) * W) + 1, int((box[1] + box[3] * (1 + m)) * H) + 1
    region = out.crop((x0, y0, x1, y1))
    rows = max(1, round(faces.CELLS * (y1 - y0) / (x1 - x0)))
    coarse = region.resize((faces.CELLS, rows), Image.NEAREST).resize(region.size, Image.NEAREST)
    assert list(region.getdata()) == list(coarse.getdata())  # nothing finer than the blocks is left
    assert region.getdata() != img.crop((x0, y0, x1, y1)).getdata()
    for xy in ((0, 0), (199, 99), (x0 - 1, y0), (x1, y1 - 1)):
        assert out.getpixel(xy) == img.getpixel(xy)  # outside the face, and its margin: as it was
    assert faces.pixelate(img.copy(), [[0.5, 0.5, 0.001, 0.001]]).tobytes() == img.tobytes()  # too small to be anything


def test_which_faces_are_on_which_picture(db):
    rid = 7
    db.q("CREATE $r CONTENT $d", r=R("recording", rid), d={"title": "x", "sample_ms": 1000})
    db.q("INSERT INTO shot $rows", rows=[{"recording": rid, "idx": 0, "t0": 0, "t1": 3000, "frame": "shot0000.jpg"}])
    db.q(
        "INSERT INTO face_track $rows",
        rows=[
            {"recording": rid, "boxes": [[0, 0.1, 0.1, 0.2, 0.2], [1000, 0.15, 0.1, 0.2, 0.2], [5000, 0.5, 0.5, 0.1, 0.1]]},
            {"recording": rid, "paged": True, "boxes": [[1, 0.3, 0.3, 0.2, 0.2]]},
        ],
    )
    on = lambda name: faces.boxes_on(db, rid, name)  # noqa: E731
    assert on("s000005000.jpg") == [[0.5, 0.5, 0.1, 0.1]]  # a sampled frame, by its time
    assert on("s000002000.jpg") is None
    # a shot's keyframe (taken 750 ms in): the faces found on the sampled frames within a step of it
    assert on("shot0000.jpg") == [[0.1, 0.1, 0.2, 0.2], [0.15, 0.1, 0.2, 0.2]]
    assert on("shot0001.jpg") is None
    assert on("page-0002.jpg") == [[0.3, 0.3, 0.2, 0.2]] == on("thumb-0002.jpg")  # a page, numbered from 1
    assert on("page-0001.jpg") is None
    assert on("face-1.jpg") == [[0.0, 0.0, 1.0, 1.0]]  # a crop is all face
    assert faces.boxes_on(db, 8, "shot0000.jpg") is None  # nothing found there
