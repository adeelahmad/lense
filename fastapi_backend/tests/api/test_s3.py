"""The file tree over S3 (docs/file-tree.md#s3): a real S3 client (botocore) lists and reads each person's namespaces
with a key they made in the web app; writes, wrong signatures and revoked keys are refused."""

from __future__ import annotations

import threading
import urllib.request

import botocore.session
import pytest
from botocore.config import Config
from botocore.exceptions import ClientError

from app.domain import hierarchy, s3, shares, store, topics
from tests.helpers import login, make_user, seed, web_relay


@pytest.fixture
def env(client, db, cfg, folder):
    a, b, call = seed(db, cfg, folder)
    topics.tag(db, topics.create(db, store.ns_id(db, "pods"), "Gene therapy"), [a])
    talks = hierarchy.create(db, store.ns_id(db, "pods"), "Talks")
    db.q("UPDATE $r SET collection = $c", r=store.R("recording", b), c=talks)
    make_user(db, "vi@x.io", "viewer password 1", roles={"pods": "viewer"})
    hv = login(client, "vi@x.io", "viewer password 1")
    server = shares.Server("127.0.0.1", 0, db, lambda: cfg).start()
    yield {"hv": hv, "port": server.port, "folder": folder}
    server.stop()


def _key(client, h, **body):
    r = client.post("/api/v1/shares/s3/keys", headers=h, json=body)
    assert r.status_code == 200, r.text
    return r.json()


def _s3(port, kid, secret, path=""):
    return botocore.session.get_session().create_client(
        "s3",
        endpoint_url=f"http://127.0.0.1:{port}{path}",
        region_name="us-east-1",
        aws_access_key_id=kid,
        aws_secret_access_key=secret,
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"}, retries={"max_attempts": 1}),
    )


def test_people_read_their_namespaces_over_s3(client, env):
    k = _key(client, env["hv"], name="laptop")
    assert k["id"].startswith("LK") and k["port"] == 9555 and k["webdav_port"] == 9556
    c = _s3(env["port"], k["id"], k["secret"])
    assert [b["Name"] for b in c.list_buckets()["Buckets"]] == ["pods"]
    top = c.list_objects_v2(Bucket="pods", Delimiter="/")
    assert [p["Prefix"] for p in top["CommonPrefixes"]] == ["Collections/", "Entities/", "Topics/"] and "Contents" not in top
    talks = c.list_objects_v2(Bucket="pods", Prefix="Collections/Talks/", Delimiter="/")
    assert [o["Key"] for o in talks["Contents"]] == [
        "Collections/Talks/ep2 [2].json",
        "Collections/Talks/ep2 [2].original.txt",
        "Collections/Talks/ep2 [2].txt",
    ]
    assert [o["Key"] for o in c.list_objects_v2(Bucket="pods", Prefix="Collections/Talks/ep2 [2].t")["Contents"]] == [
        "Collections/Talks/ep2 [2].txt"
    ]
    # v1 listing, recursive, a page at a time
    keys, marker = [], ""
    while True:
        page = c.list_objects(Bucket="pods", Marker=marker, MaxKeys=2)
        keys += [o["Key"] for o in page.get("Contents", [])]
        if not page["IsTruncated"]:
            break
        marker = keys[-1]
    assert keys == sorted(keys, key=str.encode) and keys[0] == "Collections/General/ep1 [1].json"
    assert len([k for k in keys if k.startswith("Collections/")]) == 6 and "Topics/Gene therapy/ep1 [1].txt" in keys
    # v2 pages
    first = c.list_objects_v2(Bucket="pods", MaxKeys=4)
    rest = c.list_objects_v2(Bucket="pods", ContinuationToken=first["NextContinuationToken"])
    assert [o["Key"] for o in first["Contents"] + rest["Contents"]] == keys
    # objects, whole and in part, as listed
    own = (env["folder"] / "ep1.txt").read_bytes()
    got = c.get_object(Bucket="pods", Key="Collections/General/ep1 [1].original.txt")
    assert got["Body"].read() == own and got["ContentLength"] == len(own) and len(got["ETag"].strip('"')) != 32
    part = c.get_object(Bucket="pods", Key="Collections/General/ep1 [1].original.txt", Range="bytes=3-9")
    assert part["Body"].read() == own[3:10] and part["ContentRange"] == f"bytes 3-9/{len(own)}"
    head = c.head_object(Bucket="pods", Key="Collections/General/ep1 [1].txt")
    assert head["ContentLength"] == next(o["Size"] for o in keys_of(c, "Collections/General/") if o["Key"].endswith("ep1 [1].txt"))
    assert c.get_object(Bucket="pods", Key="Collections/General/ep1 [1].txt")["Body"].read().startswith(b"Alice: Welcome back.")
    # a presigned link works on its own
    url = c.generate_presigned_url("get_object", Params={"Bucket": "pods", "Key": "Collections/General/ep1 [1].json"}, ExpiresIn=60)
    assert b'"namespace": "pods"' in urllib.request.urlopen(url).read()
    assert c.get_bucket_location(Bucket="pods")["LocationConstraint"] is None


def keys_of(c, prefix):
    return c.list_objects_v2(Bucket="pods", Prefix=prefix)["Contents"]


def _code(fn):
    with pytest.raises(ClientError) as e:
        fn()
    return e.value.response["Error"]["Code"]


def test_what_s3_refuses(client, env):
    k = _key(client, env["hv"])
    c = _s3(env["port"], k["id"], k["secret"])
    assert _code(lambda: c.list_objects_v2(Bucket="calls")) == "NoSuchBucket"  # not theirs
    assert _code(lambda: c.get_object(Bucket="pods", Key="Collections/General/nope.txt")) == "NoSuchKey"
    assert _code(lambda: c.put_object(Bucket="pods", Key="x.txt", Body=b"hi")) == "AccessDenied"
    assert _code(lambda: c.delete_object(Bucket="pods", Key="Collections/General/ep1 [1].json")) == "AccessDenied"
    assert _code(lambda: _s3(env["port"], k["id"], "wrong").list_buckets()) == "SignatureDoesNotMatch"
    assert _code(lambda: _s3(env["port"], "LKAAAAAAAAAAAAAAAAAA", "x").list_buckets()) == "InvalidAccessKeyId"
    # revoked: it stops working at once
    assert client.delete(f"/api/v1/shares/s3/keys/{k['id']}", headers=env["hv"]).status_code == 200
    assert _code(lambda: c.list_buckets()) == "InvalidAccessKeyId"
    # unsigned
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(f"http://127.0.0.1:{env['port']}/")
    assert e.value.code == 403


def test_at_the_web_apps_address_once_an_admin_allows_it(client, cfg, env):
    """The web app relays S3 at /s3 (nextjs-frontend/lens-relay.js), so remote access reaches it: clients sign that
    path, and it's turned away until an admin turns on shares.remote."""
    k = _key(client, env["hv"])
    with web_relay(env["port"], "/s3") as port:
        c = _s3(port, k["id"], k["secret"], "/s3")
        assert _code(lambda: c.list_buckets()) == "AccessDenied"
        cfg["shares"] = {**(cfg.get("shares") or {}), "remote": True}
        assert [b["Name"] for b in c.list_buckets()["Buckets"]] == ["pods"]
        talks = c.list_objects_v2(Bucket="pods", Prefix="Collections/Talks/", Delimiter="/")
        assert "Collections/Talks/ep2 [2].txt" in [o["Key"] for o in talks["Contents"]]
        own = (env["folder"] / "ep1.txt").read_bytes()
        got = c.get_object(Bucket="pods", Key="Collections/General/ep1 [1].original.txt", Range="bytes=2-5")
        assert got["Body"].read() == own[2:6]
        assert c.head_object(Bucket="pods", Key="Collections/General/ep1 [1].original.txt")["ContentLength"] == len(own)
        link = c.generate_presigned_url("get_object", Params={"Bucket": "pods", "Key": "Collections/General/ep1 [1].original.txt"})
        assert "/s3/pods/" in link and urllib.request.urlopen(link).read() == own
        assert _code(lambda: _s3(port, k["id"], "wrong", "/s3").list_buckets()) == "SignatureDoesNotMatch"
        assert _code(lambda: c.put_object(Bucket="pods", Key="x.txt", Body=b"hi")) == "AccessDenied"
    # at its own port, as before
    assert [b["Name"] for b in _s3(env["port"], k["id"], k["secret"]).list_buckets()["Buckets"]] == ["pods"]


def test_admins_open_them_at_the_web_apps_address(client, db, env):
    make_user(db, "ad@x.io", "admin password 1", admin=True)
    ha = login(client, "ad@x.io", "admin password 1")
    assert client.put("/api/v1/settings/shares", json={"remote": "yes"}, headers=ha).status_code == 400
    assert client.put("/api/v1/settings/shares", json={"remote": True}, headers=env["hv"]).status_code == 403
    assert client.put("/api/v1/settings/shares", json={"remote": True}, headers=ha).status_code == 200
    assert client.get("/api/v1/shares/s3", headers=env["hv"]).json()["remote"] is True


def test_keys_are_made_while_signed_in(client, db, env):
    r = client.post("/api/v1/tokens", headers=env["hv"], json={"name": "script", "scope": "write", "days": 10})
    token = {"Authorization": f"Bearer {r.json()['token']}"}
    assert client.post("/api/v1/shares/s3/keys", headers=token, json={}).status_code == 403
    k = _key(client, env["hv"], name="nas")
    st = client.get("/api/v1/shares/s3", headers=env["hv"]).json()
    assert st["enabled"] is False and st["port"] == 9555 and st["webdav"]["port"] == 9556 and st["remote"] is False
    assert [(x["id"], x["name"]) for x in st["keys"]] == [(k["id"], "nas")] and "secret" not in st["keys"][0]
    sealed = db.one("SELECT sealed FROM $r", r=store.R("file_key", k["id"]))["sealed"]
    assert k["secret"] not in sealed  # kept sealed
    assert client.get("/api/v1/shares/s3/keys", headers=env["hv"]).status_code == 403  # everyone's: admins


def test_other_servers_use_the_same_keys(client, db, cfg, env):
    """SMB checks a key's secret itself (any case: NTLM lowercases user names); NFS signs in with a service key."""
    uid = db.one("SELECT record::id(id) AS id FROM account WHERE email = 'vi@x.io'")["id"]
    k = _key(client, env["hv"])
    assert s3.lookup_key(db, cfg, k["id"].lower()) == {"account": uid, "secret": k["secret"]}
    assert s3.lookup_key(db, cfg, "LKNOTAKEY") is None
    endpoint, kid, secret = s3.service_key(db, cfg, uid, "nfs")
    assert endpoint == "http://worker:9555" and s3.service_key(db, cfg, uid, "nfs") == (endpoint, kid, secret)
    c = _s3(env["port"], kid, secret)
    assert [b["Name"] for b in c.list_buckets()["Buckets"]] == ["pods"]
    inside = s3.internal_endpoint(db, lambda: cfg)
    assert inside.startswith("http://127.0.0.1:") and s3.internal_endpoint(db, lambda: cfg) == inside
    c = _s3(int(inside.rsplit(":", 1)[1]), kid, secret)
    assert [b["Name"] for b in c.list_buckets()["Buckets"]] == ["pods"]
    assert s3.revoke_service_key(db, uid, "nfs") and s3.lookup_key(db, cfg, kid) is None


def test_the_servers_follow_the_settings(db, cfg):
    conf = {"enabled": False, "s3": True, "s3_port": 0, "webdav": True, "webdav_port": 0, "bind": "127.0.0.1"}
    svc = shares.Service(db, lambda: {**cfg, "shares": conf}, name="t")
    svc.apply()
    assert not svc.servers and shares.status(db, cfg)["s3"]["service"] is None
    conf["enabled"] = True
    svc.apply()
    assert set(svc.servers) == {"s3", "webdav"}
    assert db.one("SELECT * FROM app_service:⟨share-s3⟩")["running"] is True
    conf["webdav"] = False
    svc.apply()
    assert set(svc.servers) == {"s3"} and db.one("SELECT * FROM app_service:⟨share-webdav⟩") is None
    conf["enabled"] = False
    svc.apply()
    assert not svc.servers and shares.status(db, cfg)["s3"]["service"] is None
    stop = threading.Event()
    stop.set()
    svc.loop(stop)


def test_signature_v4_matches_aws(db, cfg):
    """The example from AWS's documentation of Signature Version 4 for S3 (GET Object with a range)."""
    assert (
        s3.signature(
            "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
            "20130524",
            "us-east-1",
            "s3",
            "AWS4-HMAC-SHA256\n20130524T000000Z\n20130524/us-east-1/s3/aws4_request\n"
            "7344ae5b7ee6c3e7e6b0fe0640412a37625d1fbfff95c48bbb2dc43964946972",
        )
        == "f0e8bdb87c964420e857bd35b5d6ed310bd44f0170aba48dd91039c6036bdb41"
    )
    assert s3.canonical_path("/pods/Gene%20therapy/a%20%5B1%5D.txt") == "/pods/Gene%20therapy/a%20%5B1%5D.txt"
