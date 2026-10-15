"""Adding files over S3 (docs/file-tree.md#adding-files): a real S3 client (botocore) puts files into the tree and
they're imported where the path says, as an upload is; what may not be added is refused before it's sent."""

from __future__ import annotations

import datetime as dt
import hashlib
import http.client
import io

import botocore.session
import pytest
from botocore.config import Config
from botocore.exceptions import ClientError

from app.domain import hierarchy, http_body, s3, share_import, shares, store
from tests.helpers import login, make_user, seed, text_pdf

PDF = text_pdf([["Harbour minutes", "The lamp was lit at dusk."]])


@pytest.fixture
def env(client, db, cfg, folder):
    seed(db, cfg, folder)
    pods = store.ns_id(db, "pods")
    talks = hierarchy.create(db, pods, "Talks")
    make_user(db, "ed@x.io", "editor password 1", roles={"pods": "editor"})
    make_user(db, "vi@x.io", "viewer password 1", roles={"pods": "viewer"})
    conf = {"cfg": cfg}
    server = shares.Server("127.0.0.1", 0, db, lambda: conf["cfg"], log=print).start()
    keys = {}
    for who in ("ed", "vi"):
        h = login(client, f"{who}@x.io", f"{'editor' if who == 'ed' else 'viewer'} password 1")
        k = client.post("/api/v1/shares/s3/keys", headers=h, json={}).json()
        keys[who] = (k["id"], k["secret"])
    share_import.REGISTRY.items.clear()
    yield {"port": server.port, "keys": keys, "talks": talks, "pods": pods, "conf": conf}
    server.stop()


def _s3(env, who="ed", **cfg):
    kid, secret = env["keys"][who]
    return botocore.session.get_session().create_client(
        "s3",
        endpoint_url=f"http://127.0.0.1:{env['port']}",
        region_name="us-east-1",
        aws_access_key_id=kid,
        aws_secret_access_key=secret,
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"}, retries={"max_attempts": 1}, **cfg),
    )


def _code(fn):
    with pytest.raises(ClientError) as e:
        fn()
    return e.value.response["Error"]["Code"], e.value.response["Error"].get("Message")


def _recording(db, title):
    return db.one("SELECT record::id(id) AS id, collection, source, size, recorded_at FROM recording WHERE title = $t", t=title)


def test_a_file_put_into_a_collection_is_imported_there(client, db, env):
    c = _s3(env)
    put = c.put_object(Bucket="pods", Key="Collections/Talks/minutes.pdf", Body=PDF, Metadata={"mtime": "1700000000.5"})
    assert put["ETag"] == f'"{hashlib.md5(PDF).hexdigest()}"'
    rec = _recording(db, "minutes")
    assert rec["collection"] == env["talks"] and rec["source"] == "document" and rec["size"] == len(PDF)
    assert rec["recorded_at"].startswith("2023-11-14")  # the file's own time, from rclone's mtime
    assert db.one("SELECT VALUE id FROM job WHERE recording = $r LIMIT 1", r=rec["id"])  # the namespace's pipeline is queued
    # the client finds it under the name it gave, beside the asset's own files
    head = c.head_object(Bucket="pods", Key="Collections/Talks/minutes.pdf")
    assert head["ContentLength"] == len(PDF)
    assert c.get_object(Bucket="pods", Key="Collections/Talks/minutes.pdf")["Body"].read() == PDF
    keys = [o["Key"] for o in c.list_objects_v2(Bucket="pods", Prefix="Collections/Talks/")["Contents"]]
    assert "Collections/Talks/minutes.pdf" in keys and f"Collections/Talks/minutes [{rec['id']}].pdf" in keys
    # only for them: others see the asset as Lens names it
    others = [o["Key"] for o in _s3(env, "vi").list_objects_v2(Bucket="pods", Prefix="Collections/Talks/")["Contents"]]
    assert "Collections/Talks/minutes.pdf" not in others and f"Collections/Talks/minutes [{rec['id']}].pdf" in others
    audit = db.one("SELECT * FROM audit_log WHERE action = 'share.import'")
    assert audit["target"] == f"recording:{rec['id']}" and audit["detail"]["via"] == "s3" and audit["email"] == "ed@x.io"
    # the same file again isn't added twice
    c.put_object(Bucket="pods", Key="Collections/Talks/again.pdf", Body=PDF)
    assert len(db.values("SELECT VALUE id FROM recording WHERE source = 'document'")) == 1
    assert c.head_object(Bucket="pods", Key="Collections/Talks/again.pdf")["ContentLength"] == len(PDF)


def test_folders_become_collections(db, env):
    c = _s3(env)
    c.put_object(Bucket="pods", Key="Collections/Trips/Lisbon 2026/minutes.pdf", Body=PDF)
    trips = next(x for x in hierarchy.of_space(db, env["pods"]) if x["name"] == "Trips")
    lisbon = next(x for x in hierarchy.of_space(db, env["pods"]) if x["name"] == "Lisbon 2026")
    assert lisbon["parent"] == trips["id"] and _recording(db, "minutes")["collection"] == lisbon["id"]
    # a folder marker makes an empty one; a name differing only in case is the same collection
    c.put_object(Bucket="pods", Key="Collections/trips/Porto/", Body=b"")
    porto = next(x for x in hierarchy.of_space(db, env["pods"]) if x["name"] == "Porto")
    assert porto["parent"] == trips["id"]
    assert db.one("SELECT * FROM audit_log WHERE action = 'collection.create' AND detail.name = 'Porto'")["detail"]["via"] == "s3"
    # anywhere else in the namespace: its default collection
    c.put_object(Bucket="pods", Key="Topics/minutes 2.pdf", Body=PDF + b"\n%2")
    assert _recording(db, "minutes 2")["collection"] == store.default_collection(db, env["pods"])
    assert _code(lambda: c.put_object(Bucket="pods", Key="Nowhere/x.pdf", Body=PDF))[0] == "AccessDenied"
    assert _code(lambda: c.put_object(Bucket="pods", Key="Topics/Made up/", Body=b""))[0] == "AccessDenied"


def test_what_may_not_be_added_is_refused(db, cfg, env):
    c = _s3(env)
    assert _code(lambda: _s3(env, "vi").put_object(Bucket="pods", Key="Collections/Talks/x.pdf", Body=PDF))[0] == "AccessDenied"
    assert _code(lambda: c.put_object(Bucket="calls", Key="x.pdf", Body=PDF))[0] == "NoSuchBucket"
    assert _code(lambda: c.put_object(Bucket="pods", Key="Collections/General/ep1 [1].json", Body=b"{}"))[0] == "AccessDenied"
    assert _code(lambda: c.put_object(Bucket="pods", Key="Collections/Talks", Body=PDF))[0] == "AccessDenied"  # a folder
    code, msg = _code(lambda: c.put_object(Bucket="pods", Key="tool.exe", Body=b"MZ"))
    assert code == "InvalidArgument" and "can't be uploaded here" in msg
    assert _code(lambda: c.create_bucket(Bucket="new-ns"))[0] == "AccessDenied"
    assert (
        _code(lambda: c.copy_object(Bucket="pods", Key="b.pdf", CopySource="pods/Collections/General/ep1 [1].json"))[0] == "NotImplemented"
    )
    assert _code(lambda: c.delete_object(Bucket="pods", Key="Collections/General/ep1 [1].json"))[0] == "AccessDenied"
    c.delete_object(Bucket="pods", Key="Collections/General/nothing.pdf")  # nothing there: done
    env["conf"]["cfg"] = {**cfg, "uploads": {**cfg["uploads"], "max_mb": 1}}
    assert _code(lambda: c.put_object(Bucket="pods", Key="big.pdf", Body=PDF + b"\0" * (2 << 20)))[0] == "EntityTooLarge"
    env["conf"]["cfg"] = {**cfg, "shares": {**cfg["shares"], "add_files": False}}
    code, msg = _code(lambda: c.put_object(Bucket="pods", Key="x.pdf", Body=PDF))
    assert code == "AccessDenied" and "read-only" in msg
    assert not db.values("SELECT VALUE id FROM recording WHERE source = 'document'")


def test_files_an_os_writes_for_itself_and_empty_files_are_kept_aside(db, env):
    c = _s3(env)
    c.put_object(Bucket="pods", Key="Collections/Talks/.DS_Store", Body=b"Bud1")
    c.put_object(Bucket="pods", Key="Collections/Talks/report.pdf", Body=b"")
    assert c.get_object(Bucket="pods", Key="Collections/Talks/.DS_Store")["Body"].read() == b"Bud1"
    assert c.head_object(Bucket="pods", Key="Collections/Talks/report.pdf")["ContentLength"] == 0
    assert not db.values("SELECT VALUE id FROM recording WHERE source = 'document'")
    c.put_object(Bucket="pods", Key="Collections/Talks/report.pdf", Body=PDF)  # then its bytes
    assert _recording(db, "report")["collection"] == env["talks"]
    out = c.delete_objects(
        Bucket="pods", Delete={"Objects": [{"Key": "Collections/Talks/.DS_Store"}, {"Key": "Collections/Talks/ep9.pdf"}]}
    )
    assert sorted(d["Key"] for d in out["Deleted"]) == ["Collections/Talks/.DS_Store", "Collections/Talks/ep9.pdf"]
    assert _code(lambda: c.head_object(Bucket="pods", Key="Collections/Talks/.DS_Store"))[0] == "404"
    # kept aside for the person who wrote them only, for a while
    assert "Collections/Talks/report.pdf" not in [o["Key"] for o in _s3(env, "vi").list_objects_v2(Bucket="pods")["Contents"]]
    for names in share_import.REGISTRY.items.values():
        for it in names.values():
            it.expires = 0
    assert "Collections/Talks/report.pdf" not in [o["Key"] for o in c.list_objects_v2(Bucket="pods")["Contents"]]


def test_multipart_uploads(db, env):
    c = _s3(env)
    body = PDF + b"%" * (6 << 20)
    up = c.create_multipart_upload(Bucket="pods", Key="Collections/Talks/long.pdf")
    uid = up["UploadId"]
    assert [u["UploadId"] for u in c.list_multipart_uploads(Bucket="pods").get("Uploads", [])] == [uid]
    e1 = c.upload_part(Bucket="pods", Key="Collections/Talks/long.pdf", UploadId=uid, PartNumber=1, Body=body[: 5 << 20])["ETag"]
    e2 = c.upload_part(Bucket="pods", Key="Collections/Talks/long.pdf", UploadId=uid, PartNumber=2, Body=body[5 << 20 :])["ETag"]
    assert [p["PartNumber"] for p in c.list_parts(Bucket="pods", Key="Collections/Talks/long.pdf", UploadId=uid)["Parts"]] == [1, 2]
    # not someone else's, and not out of order
    assert _code(lambda: _s3(env, "vi").abort_multipart_upload(Bucket="pods", Key="Collections/Talks/long.pdf", UploadId=uid))[0] in (
        "AccessDenied",
        "NoSuchUpload",
    )
    wrong = {"Parts": [{"PartNumber": 2, "ETag": e2}, {"PartNumber": 1, "ETag": e1}]}
    assert (
        _code(lambda: c.complete_multipart_upload(Bucket="pods", Key="Collections/Talks/long.pdf", UploadId=uid, MultipartUpload=wrong))[0]
        == "InvalidPartOrder"
    )
    done = c.complete_multipart_upload(
        Bucket="pods",
        Key="Collections/Talks/long.pdf",
        UploadId=uid,
        MultipartUpload={"Parts": [{"PartNumber": 1, "ETag": e1}, {"PartNumber": 2, "ETag": e2}]},
    )
    assert done["ETag"].endswith('-2"')
    rec = _recording(db, "long")
    assert rec["collection"] == env["talks"] and rec["size"] == len(body)
    assert c.get_object(Bucket="pods", Key="Collections/Talks/long.pdf", Range="bytes=0-4")["Body"].read() == b"%PDF-"
    assert _code(lambda: c.list_parts(Bucket="pods", Key="Collections/Talks/long.pdf", UploadId=uid))[0] == "NoSuchUpload"
    # an upload given up goes
    up = c.create_multipart_upload(Bucket="pods", Key="Collections/Talks/gone.pdf")["UploadId"]
    c.upload_part(Bucket="pods", Key="Collections/Talks/gone.pdf", UploadId=up, PartNumber=1, Body=PDF)
    c.abort_multipart_upload(Bucket="pods", Key="Collections/Talks/gone.pdf", UploadId=up)
    assert not c.list_multipart_uploads(Bucket="pods").get("Uploads")


def _signed_put(env, key, chunks, payload="STREAMING-AWS4-HMAC-SHA256-PAYLOAD", spoil=False, trailer=None):
    """PUT `chunks` aws-chunked by hand, signed as SigV4 streaming uploads are (minio-go and the AWS SDKs do this)."""
    kid, secret = env["keys"]["ed"]
    now = dt.datetime.now(dt.UTC)
    amz, date = now.strftime("%Y%m%dT%H%M%SZ"), now.strftime("%Y%m%d")
    scope = f"{date}/us-east-1/s3/aws4_request"
    host = f"127.0.0.1:{env['port']}"
    path = s3.canonical_path(f"/pods/{key}")
    size = sum(len(c) for c in chunks)
    plain = not payload.startswith("STREAMING-")
    headers = {"host": host, "x-amz-content-sha256": payload, "x-amz-date": amz}
    if not plain:
        headers.update({"x-amz-decoded-content-length": str(size), "content-encoding": "aws-chunked"})
    if trailer:
        headers["x-amz-trailer"] = trailer[0]
    names = sorted(headers)
    canonical = "\n".join(["PUT", path, "", "".join(f"{n}:{headers[n]}\n" for n in names), ";".join(names), payload])
    to_sign = "\n".join(["AWS4-HMAC-SHA256", amz, scope, hashlib.sha256(canonical.encode()).hexdigest()])
    seed = s3.signature(secret, date, "us-east-1", "s3", to_sign)
    signer = http_body.Signed(s3.signing_key(secret, date, "us-east-1", "s3"), amz, scope, seed)
    body = io.BytesIO()
    for c in [] if plain else [*chunks, b""]:
        if payload.startswith("STREAMING-AWS4"):
            sig = hmac_chunk(signer, c)
            if spoil and c:
                sig = "0" * 64
            body.write(f"{len(c):x};chunk-signature={sig}\r\n".encode())
        else:
            body.write(f"{len(c):x}\r\n".encode())
        if c:
            body.write(c + b"\r\n")
    if trailer:
        body.write(f"{trailer[0]}:{trailer[1]}\r\n".encode())
    body.write(b"".join(chunks) if plain else b"\r\n")
    headers["authorization"] = f"AWS4-HMAC-SHA256 Credential={kid}/{scope},SignedHeaders={';'.join(names)},Signature={seed}"
    conn = http.client.HTTPConnection("127.0.0.1", env["port"])
    conn.request("PUT", path, body=body.getvalue(), headers=headers)
    r = conn.getresponse()
    return r.status, r.read()


def hmac_chunk(signer, chunk):
    import hmac

    to_sign = "\n".join(["AWS4-HMAC-SHA256-PAYLOAD", signer.amz, signer.scope, signer.prev, http_body.EMPTY_SHA256,
                         hashlib.sha256(chunk).hexdigest()])  # fmt: skip
    signer.prev = hmac.new(signer.key, to_sign.encode(), hashlib.sha256).hexdigest()
    return signer.prev


def test_streaming_uploads_have_their_chunks_checked(db, env):
    status, _ = _signed_put(env, "Collections/Talks/streamed.pdf", [PDF[:100], PDF[100:]])
    assert status == 200 and _recording(db, "streamed")["size"] == len(PDF)
    status, body = _signed_put(env, "Collections/Talks/spoilt.pdf", [PDF[:100], PDF[100:]], spoil=True)
    assert status == 403 and b"SignatureDoesNotMatch" in body and not _recording(db, "spoilt")
    # unsigned chunks with a CRC32 trailer (the AWS SDKs' default for uploads)
    import base64
    import zlib

    other = PDF + b"\n%3"
    crc = base64.b64encode(zlib.crc32(other).to_bytes(4, "big")).decode()
    status, _ = _signed_put(
        env, "Collections/Talks/trailed.pdf", [other], "STREAMING-UNSIGNED-PAYLOAD-TRAILER", trailer=("x-amz-checksum-crc32", crc)
    )
    assert status == 200 and _recording(db, "trailed")
    status, body = _signed_put(
        env, "Collections/Talks/bad crc.pdf", [other + b"x"], "STREAMING-UNSIGNED-PAYLOAD-TRAILER", trailer=("x-amz-checksum-crc32", crc)
    )
    assert status == 400 and b"BadDigest" in body


def test_a_body_that_isnt_what_was_signed_is_refused(db, env):
    status, body = _signed_put(env, "Collections/Talks/x.pdf", [PDF], hashlib.sha256(b"something else").hexdigest())
    assert status == 400 and b"XAmzContentSHA256Mismatch" in body and not _recording(db, "x")
    status, _ = _signed_put(env, "Collections/Talks/y.pdf", [PDF], hashlib.sha256(PDF).hexdigest())
    assert status == 200 and _recording(db, "y")


def test_people_with_a_role_on_a_collection_add_there_only(client, db, env):
    uid = make_user(db, "co@x.io", "collection password 1")
    hierarchy.give(db, env["talks"], uid, "admin")
    h = login(client, "co@x.io", "collection password 1")
    k = client.post("/api/v1/shares/s3/keys", headers=h, json={}).json()
    env["keys"]["co"] = (k["id"], k["secret"])
    c = _s3(env, "co")
    c.put_object(Bucket="pods", Key="Collections/Talks/Day 1/minutes.pdf", Body=PDF)
    day = next(x for x in hierarchy.of_space(db, env["pods"]) if x["name"] == "Day 1")
    assert day["parent"] == env["talks"] and _recording(db, "minutes")["collection"] == day["id"]
    assert _code(lambda: c.put_object(Bucket="pods", Key="Collections/Other/x.pdf", Body=PDF))[0] == "AccessDenied"
    assert _code(lambda: c.put_object(Bucket="pods", Key="x.pdf", Body=PDF))[0] == "AccessDenied"  # the default collection isn't theirs
