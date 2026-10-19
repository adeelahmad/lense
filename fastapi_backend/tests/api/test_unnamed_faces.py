"""Naming faces in bulk (docs/video.md#naming-faces): a namespace's unnamed face groups, the largest first, each with
crops, the assets it's in and a name to offer; naming one makes it that person, or joins the face that has the name,
with undo; through the API and MCP."""

from __future__ import annotations

import pytest

from app.domain import faces, store
from tests.api.test_mcp import rpc, tool
from tests.helpers import login, make_user, seed

R = store.R


@pytest.fixture
def env(client, db, cfg, folder):
    """In pods (recognising faces): a face in both episodes, one on two pages of ep1 that looks like Mara, one briefly
    in ep1 linked to Alice's voice, Mara herself, and a face whose only track was removed as not a face."""
    ep1, ep2, _ = seed(db, cfg, folder)
    pods = store.ns_id(db, "pods")
    faces.set_mode(db, pods, "recognize", "Name the hosts", "root@x.io")
    big, paged, brief, mara, gone = (faces.new_face(db, pods) for _ in range(5))
    faces.rename(db, mara, "Mara")
    alice = next(s["id"] for s in faces.spk.list_speakers(db, pods) if s["display"] == "Alice")
    db.q("UPDATE $r SET speaker = $s", r=R("face", brief), s=alice)
    db.q("CREATE face_suggestion CONTENT $d", d={"kind": "face", "face": paged, "candidate": mara, "score": 0.61, "space": pods})

    def track(face, rid, ms, cover, paged=None):
        return store.clean(
            {
                "recording": rid,
                "space": pods,
                "local": "P1",
                "face": face,
                "spans": [[0, ms]],
                "screen_ms": ms,
                "first_ms": 0,
                "cover": cover,
                "paged": paged,
            }
        )

    db.q(
        "INSERT INTO face_track $rows",
        rows=[
            track(big, ep1, 3000, "face-1.jpg"),
            track(big, ep2, 9000, "face-1.jpg"),
            track(paged, ep1, 2, "face-3.jpg", paged=True),  # two pages: as long as ten seconds on screen
            track(brief, ep1, 1000, "face-2.jpg"),
            track(mara, ep2, 4000, "face-2.jpg"),
        ],
    )
    make_user(db, "root@x.io", "root password 1", admin=True)
    make_user(db, "vi@x.io", "viewer password 1", roles={"pods": "viewer"})
    make_user(db, "ed@x.io", "editor password 1", roles={"pods": "editor"})
    return {
        "ep1": ep1,
        "ep2": ep2,
        "pods": pods,
        "big": big,
        "paged": paged,
        "brief": brief,
        "mara": mara,
        "gone": gone,
        "h": login(client, "root@x.io", "root password 1"),
        "hv": login(client, "vi@x.io", "viewer password 1"),
        "he": login(client, "ed@x.io", "editor password 1"),
    }


def name_face(client, h, **args):
    """name_face over MCP (its `name` argument would clash with the tool helpers' own): (result, error)."""
    res = rpc(client, h, "tools/call", {"name": "name_face", "arguments": args})["result"]
    return (None, res["content"][0]["text"]) if res["isError"] else (res["structuredContent"], None)


def unnamed(client, h):
    r = client.get("/api/v1/namespaces/pods/faces/unnamed", headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def test_unnamed_faces_largest_first_named_in_a_row_with_undo(client, db, env):
    hv, he, ep1, ep2 = env["hv"], env["he"], env["ep1"], env["ep2"]
    got = unnamed(client, hv)
    assert got["mode"] == "recognize" and got["total"] == 3  # not Mara (named), nor a face with no tracks left
    big, paged, brief = got["groups"]
    assert [g["id"] for g in got["groups"]] == [env["big"], env["paged"], env["brief"]]  # most assets, then longest
    assert [g["guess"] for g in got["groups"]] == [None, "Mara", "Alice"]  # a suggested face, a linked voice
    assert [r["id"] for r in big["recordings"]] == [ep2, ep1] and big["recordings"][0]["title"]  # longest seen first
    assert [t["recording"] for t in big["thumbnails"]] == [ep2, ep1]
    assert all("sig=" in t["cover_url"] for t in big["thumbnails"])  # an <img> can't send a bearer token
    assert (big["appearances"], big["screen_ms"], paged["pages"], paged["screen_ms"]) == (2, 12000, 2, 0)
    assert client.get("/api/v1/namespaces/pods/faces/unnamed?limit=1", headers=hv).json()["total"] == 3

    # a new name: the face becomes that person; Undo is the old rename with no name
    assert client.post(f"/api/v1/faces/{env['big']}/name", headers=hv, json={"name": "Ann"}).status_code == 403
    assert client.post(f"/api/v1/faces/{env['big']}/name", headers=he, json={"name": "  "}).status_code == 400
    r = client.post(f"/api/v1/faces/{env['big']}/name", headers=he, json={"name": " Ann "})
    assert r.json() == {"ok": True, "name": "Ann", "into": None, "merge": None}
    assert [g["id"] for g in unnamed(client, hv)["groups"]] == [env["paged"], env["brief"]]
    assert {f["display"] for f in client.get("/api/v1/namespaces/pods/faces", headers=hv).json()["faces"]} >= {"Ann", "Mara"}
    assert client.post(f"/api/v1/faces/{env['big']}", headers=he, json={"name": ""}).status_code == 200
    assert unnamed(client, hv)["total"] == 3

    # a name another face has (any case): it joins that person, and undoing the merge brings it back
    r = client.post(f"/api/v1/faces/{env['paged']}/name", headers=he, json={"name": "mara"}).json()
    assert (r["into"], r["name"]) == (env["mara"], "mara") and r["merge"]
    assert db.values("SELECT VALUE face FROM face_track WHERE recording = $r AND paged = true", r=ep1) == [env["mara"]]
    assert [g["id"] for g in unnamed(client, hv)["groups"]] == [env["big"], env["brief"]]
    assert client.post(f"/api/v1/faces/merges/{r['merge']}/undo", headers=he).status_code == 200
    assert [g["id"] for g in unnamed(client, hv)["groups"]] == [env["big"], env["paged"], env["brief"]]

    actions = [a["action"] for a in client.get("/api/v1/audit", headers=env["h"]).json()]
    assert {"face.rename", "face.merge", "face.merge.undo"} <= set(actions)


def test_only_where_faces_are_recognised(client, db, env):
    faces.set_mode(db, env["pods"], "detect")
    assert unnamed(client, env["hv"]) == {"mode": "detect", "total": 0, "groups": []}  # no identities to name
    assert client.get("/api/v1/namespaces/calls/faces/unnamed", headers=env["hv"]).status_code == 404  # not theirs


def test_agents_list_and_name_faces(client, db, env):
    hv, he = env["hv"], env["he"]
    got = tool(client, hv, "list_unnamed_faces", namespace="pods")
    first = got["faces"][0]
    assert got["total"] == 3 and first["face_id"] == env["big"] and first["namespace"] == "pods"
    assert first["assets"][0] == {
        "recording_id": env["ep2"],
        "title": first["assets"][0]["title"],
        "url": f"http://localhost:3000/resources/{env['ep2']}",
    }
    assert (first["appearances"], first["screen_minutes"]) == (2, 0.2)
    assert [f["guess"] for f in got["faces"][1:]] == ["Mara", "Alice"]
    assert "curate" in name_face(client, hv, face_id=env["brief"], name="Alice")[1]  # viewers can't
    assert name_face(client, he, face_id=env["brief"], name="Alice")[0] == {"face_id": env["brief"], "name": "Alice"}
    joined, _ = name_face(client, he, face_id=env["paged"], name="Mara")
    assert joined["joined_face_id"] == env["mara"] and joined["merge_id"]
    assert [f["face_id"] for f in tool(client, he, "list_unnamed_faces")["faces"]] == [env["big"]]
