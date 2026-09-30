"""Command line: batch steps, imports, speaker edits, search, reports and the server."""
from __future__ import annotations

import argparse
import json
import pathlib
import shutil
import sys

from .domain import analyze, graph, ingest, render, store
from .domain import search as searchmod
from .domain import speakers as spk


def run_steps(db, cfg, which, ns=None, limit=0, force=False, recording=None, audio=None, log=print):
    if "scan" in which:
        log("scan:", json.dumps(ingest.scan(db, cfg, ns, log)))
    if "transcribe" in which:
        log("transcribe:", ingest.transcribe_pending(db, cfg, ns, limit, force, log), "recording(s)")
    if "diarize" in which:
        log("diarize:", spk.diarize_pending(db, cfg, ns, limit, force, log), "recording(s)")
    if "analyze" in which:
        log("analyze:", analyze.analyze_pending(db, cfg, ns, limit, force, log), "recording(s)")
    if "summarize" in which:
        log("summarize:", analyze.summarize_pending(db, cfg, ns, limit, force, log), "recording(s)")
    if "report" in which:
        log("report:", len(render.build_reports(db, cfg, ns, recording, audio, log)), "file(s) in", pathlib.Path(cfg["data_dir"]) / "reports")


def _main_base(argv=None):
    ap = argparse.ArgumentParser(prog="lens", description="Lens: an archive for recorded speech and video.")
    ap.add_argument("--config", help="YAML config (default: $ARCHIVE_CONFIG or ./archive.yaml)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("init", help="write a starter archive.yaml")
    p.add_argument("--path", default="archive.yaml")
    for name, helptext in (("scan", "register new files in each namespace's folders"), ("transcribe", "transcribe new recordings"),
                           ("diarize", "split speakers and match voice IDs within each namespace"),
                           ("analyze", "named things, keywords, sections and talk statistics"), ("summarize", "optional LLM summaries"),
                           ("run", "scan, transcribe, diarize, analyze, summarize and report, resuming where it stopped")):
        p = sub.add_parser(name, help=helptext)
        p.add_argument("--ns")
        p.add_argument("--limit", type=int, default=0)
        p.add_argument("--force", action="store_true", help="redo recordings already done")
    p = sub.add_parser("report", help="write HTML reports (per recording and per namespace)")
    p.add_argument("--ns")
    p.add_argument("--recording", type=int)
    p.add_argument("--audio", choices=["link", "embed", "none"])
    p = sub.add_parser("import", help="import a transcript: txt, md, mdx, docx, doc, pdf, srt, vtt, json, chunk jsonl, or - for stdin")
    p.add_argument("ns")
    p.add_argument("transcript", help="a file, or - to read pasted text from stdin")
    p.add_argument("--format", default="auto", choices=list(ingest.FORMATS))
    p.add_argument("--audio")
    p.add_argument("--title")
    p.add_argument("--speakers", help='display names for labels, e.g. "F=Host A,M=Host B"')
    p = sub.add_parser("speakers", help="list, rename, merge, undo or link speakers")
    s2 = p.add_subparsers(dest="action", required=True)
    s2.add_parser("list").add_argument("ns")
    x = s2.add_parser("rename")
    x.add_argument("id", type=int)
    x.add_argument("name")
    x = s2.add_parser("merge")
    x.add_argument("src", type=int)
    x.add_argument("dst", type=int)
    s2.add_parser("undo").add_argument("merge_id", type=int)
    x = s2.add_parser("link", help="declare the same person across two namespaces")
    x.add_argument("a", type=int)
    x.add_argument("b", type=int)
    p = sub.add_parser("search")
    p.add_argument("q")
    p.add_argument("--ns")
    p.add_argument("--limit", type=int, default=20)
    p = sub.add_parser("graph")
    p.add_argument("--scope", default="global", help="global, or ns:<name>")
    p.add_argument("--out")
    p = sub.add_parser("serve")
    p.add_argument("--host")
    p.add_argument("--port", type=int)
    sub.add_parser("status")
    sub.add_parser("reindex", help="rebuild the search index (after changing search.tokenizer)")
    a = ap.parse_args(argv)

    if a.cmd == "init":
        dst = pathlib.Path(a.path)
        if dst.exists():
            sys.exit(f"{dst} already exists")
        src = pathlib.Path(__file__).resolve().parent.parent / "archive.example.yaml"
        if src.exists():
            shutil.copy(src, dst)
        else:
            dst.write_text("data_dir: ./archive-data\nnamespaces:\n  default:\n    paths: [./recordings]\n")
        print(f"wrote {dst}; edit the namespaces, then run: lens run")
        return
    cfg = store.load_config(a.config)
    if a.cmd == "serve":
        import os

        import uvicorn

        if a.config:
            os.environ["ARCHIVE_CONFIG"] = a.config
        uvicorn.run("app.main:app", host=a.host or cfg["server"]["host"], port=a.port or cfg["server"]["port"], proxy_headers=True)
        return
    conn = store.connect(cfg)
    try:
        if a.cmd in ("scan", "transcribe", "diarize", "analyze", "summarize", "run", "report"):
            which = ["scan", "transcribe", "diarize", "analyze", "summarize", "report"] if a.cmd == "run" else [a.cmd]
            with store.lock(cfg, "pipeline"):
                run_steps(conn, cfg, which, a.ns, getattr(a, "limit", 0), getattr(a, "force", False),
                          getattr(a, "recording", None), getattr(a, "audio", None))
        elif a.cmd == "import":
            names = dict(kv.split("=", 1) for kv in a.speakers.split(",")) if a.speakers else None
            if a.transcript == "-":
                rid = ingest.import_text(conn, cfg, a.ns, sys.stdin.read(), a.title, a.format, names)
            else:
                rid = ingest.import_transcript(conn, cfg, a.ns, a.transcript, a.audio, a.title, names, a.format)
            print(f"imported recording {rid} into {a.ns}; next: lens analyze && lens report")
        elif a.cmd == "speakers":
            if a.action == "list":
                for s in spk.list_speakers(conn, store.ns_id(conn, a.ns, create=False)):
                    sug = "; maybe " + ", ".join(f"{x['name']} ({x['score']})" for x in s["suggestions"]) if s["suggestions"] else ""
                    print(f"{s['id']:>5}  {s['display']:<24} {s['talk_ms'] / 60000:7.1f} min  {s['recordings']:>3} rec{sug}")
            elif a.action == "rename":
                spk.rename(conn, a.id, a.name)
            elif a.action == "merge":
                print("merge id", spk.merge(conn, a.src, a.dst), "(undo with: lens speakers undo <id>)")
            elif a.action == "undo":
                spk.undo(conn, a.merge_id)
            elif a.action == "link":
                spk.link(conn, a.a, a.b)
        elif a.cmd == "search":
            res = searchmod.search(conn, a.q, a.ns, limit=a.limit)
            print(f"{res['total']} match(es) for {res['query']}")
            for h in res["hits"]:
                snip = h["snippet"].replace("<mark>", "[").replace("</mark>", "]")
                print(f"  {h['namespace']}/{h['recording_id']} {store.tc(h['t0'])} {h['speaker'] or '?'}: {snip}")
        elif a.cmd == "graph":
            g = graph.build(conn, cfg, a.scope)
            text = json.dumps(g, ensure_ascii=False, indent=1)
            if a.out:
                pathlib.Path(a.out).write_text(text, encoding="utf-8")
            print(f"{len(g['nodes'])} nodes, {len(g['edges'])} edges across {', '.join(g['namespaces']) or 'nothing'}")
        elif a.cmd == "status":
            names, agg = store.space_names(conn), {}
            for r in conn.rows("SELECT space, status, duration_ms FROM recording"):
                k = (names.get(r["space"], "?"), r.get("status"))
                c, ms = agg.get(k, (0, 0))
                agg[k] = (c + 1, ms + (r.get("duration_ms") or 0))
            print(f"SurrealDB: {conn.url}")
            for (n, st), (c, ms) in sorted(agg.items()):
                print(f"  {n:<16} {st:<12} {c:>5}  {ms / 3.6e6:6.1f} h")
            for r in conn.rows("SELECT title, error FROM recording WHERE error != NONE LIMIT 10"):
                print(f"  error: {r['title']}: {r['error']}")
        elif a.cmd == "reindex":
            store.reindex(conn, cfg)
            print("search index rebuilt")
    except store.Busy as e:
        sys.exit(f"another '{e}' run is in progress" if str(e) else "busy")
    except (ValueError, KeyError) as e:
        sys.exit(str(e))
    finally:
        conn.close()


PLATFORM_CMDS = ("users", "worker", "watch")


def platform_main(argv, config):
    import getpass
    import threading
    import time

    from .domain import auth, jobs, settings, sources
    ap = argparse.ArgumentParser(prog="lens")
    sub = ap.add_subparsers(dest="cmd", required=True)
    us = sub.add_parser("users", help="accounts and namespace roles").add_subparsers(dest="action", required=True)
    x = us.add_parser("add")
    x.add_argument("email")
    x.add_argument("--name")
    x.add_argument("--admin", action="store_true")
    x.add_argument("--password-stdin", action="store_true", help="read the password from stdin instead of prompting")
    us.add_parser("list")
    x = us.add_parser("role", help="give someone a role in a namespace (none removes it)")
    x.add_argument("email")
    x.add_argument("ns")
    x.add_argument("role", choices=["viewer", "editor", "owner", "none"])
    x = us.add_parser("disable")
    x.add_argument("email")
    x = us.add_parser("password", help="set a new password")
    x.add_argument("email")
    x.add_argument("--password-stdin", action="store_true")
    w = sub.add_parser("worker", help="run queued jobs against a shared database, e.g. transcription with mlx on a Mac")
    w.add_argument("--name")
    w.add_argument("--steps", help="comma-separated steps this worker runs (default: all)")
    w.add_argument("--once", action="store_true", help="run what is queued, then exit")
    x = sub.add_parser("watch", help="scan watched folders on storage sources")
    x.add_argument("--once", action="store_true")
    a = ap.parse_args(argv)
    cfg = store.load_config(config)
    db = store.connect(cfg)
    C = settings.Settings(db, cfg).current

    def password():
        if getattr(a, "password_stdin", False):
            return sys.stdin.readline().rstrip("\n")
        pw = getpass.getpass("Password: ")
        if pw != getpass.getpass("Again: "):
            raise SystemExit("the passwords didn't match")
        return pw

    try:
        if a.cmd == "users":
            if a.action == "add":
                uid = auth.create_account(db, a.email, password(), a.name, a.admin)
                print(f"created account {uid} for {a.email}" + (" (admin)" if a.admin else ""))
            elif a.action == "list":
                for r in db.rows("SELECT record::id(id) AS id, email, name, admin, disabled FROM account ORDER BY id"):
                    flags = ("admin " if r.get("admin") else "") + ("disabled" if r.get("disabled") else "")
                    print(f"  {r['id']:>3}  {r['email']:<32} {r.get('name') or '':<20} {flags}")
            else:
                acct = auth.find_account(db, a.email)
                if not acct:
                    raise SystemExit(f"no account for {a.email}")
                if a.action == "role":
                    auth.set_role(db, acct["id"], store.ns_id(db, a.ns, create=False), None if a.role == "none" else a.role)
                    print(f"{a.email}: {a.role} in {a.ns}")
                elif a.action == "disable":
                    auth.update_account(db, acct["id"], disabled=True)
                    print(f"disabled {a.email}")
                else:
                    auth.update_account(db, acct["id"], password=password())
                    print(f"password changed for {a.email}")
        elif a.cmd == "worker":
            wk = jobs.Worker(db, C, a.name, a.steps.split(",") if a.steps else None, log=print)
            if a.once:
                print(f"ran {wk.drain()} job(s)")
            else:
                print(f"worker {wk.name} runs {', '.join(sorted(wk.can))}; Ctrl-C to stop")
                stop = threading.Event()
                try:
                    wk.loop(stop)
                except KeyboardInterrupt:
                    stop.set()
        elif a.once:
            print(f"scanned {sources.poll_due(db, C(), print)} folder(s)")
        else:
            print("watching storage sources; Ctrl-C to stop")
            try:
                while True:
                    sources.poll_due(db, C(), print)
                    time.sleep(max(5, C()["sources"]["check_seconds"]))
            except KeyboardInterrupt:
                pass
    except (ValueError, KeyError) as e:
        raise SystemExit(str(e)) from None
    finally:
        db.close()


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    i, config = 0, None
    while i < len(argv) and argv[i].startswith("-"):
        if argv[i] == "--config" and i + 1 < len(argv):
            config, i = argv[i + 1], i + 2
        elif argv[i].startswith("--config="):
            config, i = argv[i].split("=", 1)[1], i + 1
        else:
            break
    if i < len(argv) and argv[i] in PLATFORM_CMDS:
        return platform_main(argv[i:], config)
    return _main_base(argv)
