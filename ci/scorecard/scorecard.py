#!/usr/bin/env python3
"""Lens's scorecard: one JSON line of the numbers that say whether Lens is meeting its goals.

Every number is either measured, with the command or API call behind it, or reported as not measured, with why;
nothing is estimated. The line carries the commit and release it was taken at, so lines taken at successive releases
can be compared (AGENTS.md, Goal: back gains with before-and-after numbers).

- Friction: clicks, keys, swipes and journeys passing on a computer and a phone, from the last accepted survey
  (ci/clicks/baseline.json, baseline-phone.json).
- Tests: test functions defined in the repository, and the backend's tests as pytest collects them (when the backend
  is installed).
- Coverage: the backend's and the frontend's line coverage, from the latest CI artifacts on main.
- CI: the share of pull requests whose first push had a failing check, failed runs on main, the median time from ask
  to merge and the median workflow minutes per merged pull request, over the last --days.
- Outside interest: stars, forks, and issues and pull requests from people who aren't collaborators.
- Install to first answer, and idle RAM and disk on arm64 (as on a Raspberry Pi): not measured yet.

Usage:
  ci/scorecard/scorecard.py                  print the line
  ci/scorecard/scorecard.py --log FILE       compare with FILE's last line, then append the line to it
  ci/scorecard/scorecard.py --offline        only what the checkout can tell (no GitHub calls)

The comparison exits 1 when a guard trips: journeys passing, journeys, tests or coverage went down, a measured number
is no longer measured, or a clicks baseline changed without a new survey. A turn whose guard trips doesn't count as
better, whatever else improved. GitHub calls use GITHUB_TOKEN (or GH_TOKEN) when set; downloading the coverage
artifacts needs one.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import statistics
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
API = "https://api.github.com"
INSIDE = {"OWNER", "MEMBER", "COLLABORATOR"}
RED = {"failure", "timed_out", "startup_failure"}
CLOSES_RE = re.compile(
    r"(?i)\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s*:?\s+#(\d+)\b"
)
PY_TEST_RE = re.compile(r"^\s*(?:async\s+)?def\s+test\w*\s*\(", re.MULTILINE)
JS_TEST_RE = re.compile(
    r"^\s*(?:it|test)(?:\.(?:each\b[^\n]*?\)|only|concurrent))?\s*\(", re.MULTILINE
)
PY_TEST_FILE = re.compile(r"(?:^|/)(?:test_[^/]*|[^/]*_test)\.py$")
JS_TEST_FILE = re.compile(
    r"(?:\.(?:test|spec)\.[cm]?[jt]sx?$)|(?:(?:^|/)__tests__/.*\.[cm]?[jt]sx?$)"
)
BASELINES = {
    "computer": "ci/clicks/baseline.json",
    "phone": "ci/clicks/baseline-phone.json",
}


def measured(value, unit, cmd, better=None, **extra):
    """A measured number. better: "up" or "down" when one direction is an improvement."""
    return {"value": value, "unit": unit, "cmd": cmd, "better": better, **extra}


def not_measured(why, cmd=None):
    return {"value": None, "not_measured": why, "cmd": cmd}


def iso(t: datetime) -> str:
    return t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_time(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def git(*args: str, root: Path = ROOT) -> str | None:
    try:
        out = subprocess.run(
            ["git", *args], cwd=root, capture_output=True, text=True, check=True
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return out.stdout.strip()


# --- Friction (ci/clicks) ---


def friction(root: Path = ROOT) -> dict:
    out = {}
    for device, rel in BASELINES.items():
        path = root / rel
        clicks = "taps" if device == "phone" else "clicks"
        cmd = f"ci/clicks/run.sh{' --device phone' if device == 'phone' else ''} --compare {rel}"
        try:
            raw = path.read_bytes()
            data = json.loads(raw)
        except (OSError, ValueError) as e:
            for name in (
                f"journeys_passing_{device}",
                f"{clicks}_{device}",
                f"keys_{device}",
            ):
                out[name] = not_measured(f"can't read {rel}: {e}", cmd)
            continue
        journeys = data.get("journeys", [])
        source = {
            "file": rel,
            "surveyed": data.get("surveyed"),
            "survey_commit": (data.get("commit") or "")[:12] or None,
            "sha256": hashlib.sha256(raw).hexdigest(),
        }

        def total(key, journeys=journeys):
            return sum(j.get(key) or 0 for j in journeys)

        out[f"journeys_passing_{device}"] = measured(
            sum(1 for j in journeys if j.get("ok")),
            "journeys",
            cmd,
            better="up",
            of=len(journeys),
            source=source,
        )
        counted = [(clicks, clicks), ("keys", "keys"), ("prompts", "passkey touches")]
        if device == "phone":
            counted.insert(2, ("swipes", "swipes"))
        for key, unit in counted:
            field = "clicks" if key == clicks else key
            out[f"{key}_{device}"] = measured(
                total(field), unit, cmd, better="down", source=rel
            )
    return out


# --- Tests ---


def count_tests(files: dict[str, str]) -> dict[str, int]:
    """Test functions defined, per top-level folder, from {path: text}."""
    counts: dict[str, int] = {}
    for path, text in files.items():
        if PY_TEST_FILE.search(path):
            n = len(PY_TEST_RE.findall(text))
        elif JS_TEST_FILE.search(path):
            n = len(JS_TEST_RE.findall(text))
        else:
            continue
        if n:
            top = path.split("/", 1)[0] if "/" in path else "."
            counts[top] = counts.get(top, 0) + n
    return dict(sorted(counts.items()))


def tests_defined(root: Path = ROOT) -> dict:
    cmd = "ci/scorecard/scorecard.py --offline (def test_* in test_*.py; it( and test( in *.test.* and __tests__)"
    listed = git("ls-files", root=root)
    if listed is None:
        return not_measured("not a git checkout", cmd)
    files = {}
    for path in listed.splitlines():
        if PY_TEST_FILE.search(path) or JS_TEST_FILE.search(path):
            try:
                files[path] = (root / path).read_text(
                    encoding="utf-8", errors="replace"
                )
            except OSError:
                pass
    counts = count_tests(files)
    return measured(sum(counts.values()), "tests", cmd, better="up", by_project=counts)


def backend_tests_collected(root: Path = ROOT) -> dict:
    cmd = "cd fastapi_backend && uv run --frozen pytest --collect-only -q"
    backend = root / "fastapi_backend"
    if not (backend / ".venv").is_dir():
        return not_measured(
            "the backend isn't installed here (cd fastapi_backend && uv sync)", cmd
        )
    try:
        out = subprocess.run(
            ["uv", "run", "--frozen", "pytest", "--collect-only", "-q"],
            cwd=backend,
            capture_output=True,
            text=True,
            timeout=600,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        return not_measured(f"pytest didn't run: {e}", cmd)
    m = re.search(r"^(\d+) tests? collected", out.stdout, re.MULTILINE)
    if out.returncode != 0 or not m:
        tail = (out.stdout + out.stderr).strip().splitlines()[-1:] or ["no output"]
        return not_measured(f"pytest couldn't collect the tests: {tail[0]}", cmd)
    return measured(int(m.group(1)), "tests", cmd, better="up")


# --- GitHub ---


class GitHub:
    def __init__(self, repo: str, token: str | None):
        self.repo = repo
        self.token = token
        self.calls = 0

    def request(self, url: str, accept: str = "application/vnd.github+json") -> bytes:
        if not url.startswith("http"):
            url = API + url
        req = urllib.request.Request(
            url, headers={"Accept": accept, "User-Agent": "lens-scorecard"}
        )
        if self.token:
            # not passed on to where an artifact download redirects (signed storage URLs reject it)
            req.add_unredirected_header("Authorization", f"Bearer {self.token}")
        self.calls += 1
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.read()

    def get(self, path: str, params: dict | None = None):
        url = path + ("?" + urllib.parse.urlencode(params) if params else "")
        return json.loads(self.request(url))

    def pages(self, path: str, params: dict, key: str | None = None):
        """Every item of a paged list, newest pages first as the API orders them; stop early with break."""
        params = {**params, "per_page": 100}
        page = 1
        while True:
            data = self.get(path, {**params, "page": page})
            items = data[key] if key else data
            yield from items
            if len(items) < 100:
                return
            page += 1


def runs_created(gh: GitHub, since: datetime, until: datetime, **filters) -> list[dict]:
    """Workflow runs created in [since, until). The API returns at most 1000 per query, so long spans are split."""
    path = f"/repos/{gh.repo}/actions/runs"
    params = {
        **filters,
        "created": f"{iso(since)}..{iso(until - timedelta(seconds=1))}",
    }
    first = gh.get(path, {**params, "per_page": 1})
    if first["total_count"] >= 1000 and until - since > timedelta(minutes=10):
        mid = since + (until - since) / 2
        return runs_created(gh, since, mid, **filters) + runs_created(
            gh, mid, until, **filters
        )
    return list(gh.pages(path, params, key="workflow_runs"))


def runs_of(pr: dict, runs: list[dict]) -> list[dict]:
    """The pull_request runs of a pull request: its head branch and repository, between its opening and closing."""
    head = pr["head"]
    repo = (head.get("repo") or {}).get("full_name")
    opened = parse_time(pr["created_at"])
    closed = (
        parse_time(pr["closed_at"]) + timedelta(minutes=5)
        if pr.get("closed_at")
        else None
    )
    out = []
    for r in runs:
        if (
            r.get("head_branch") != head["ref"]
            or (r.get("head_repository") or {}).get("full_name") != repo
        ):
            continue
        t = parse_time(r["created_at"])
        if t >= opened and (closed is None or t <= closed):
            out.append(r)
    return out


def first_push(runs: list[dict], attempt_one=None) -> str | None:
    """ "red" when a check on the pull request's first push failed, "green" when none did, None while one still runs.

    attempt_one(run) gives a re-run's first conclusion: a check that passed only when run again was red first.
    """
    if not runs:
        return None
    sha = min(runs, key=lambda r: (r["created_at"], r["id"]))["head_sha"]
    red = False
    for r in runs:
        if r["head_sha"] != sha:
            continue
        if r.get("status") != "completed":
            return None
        conclusion = r.get("conclusion")
        if (r.get("run_attempt") or 1) > 1 and attempt_one is not None:
            conclusion = attempt_one(r) or conclusion
        red = red or conclusion in RED
    return "red" if red else "green"


def run_minutes(runs: list[dict]) -> float:
    total = 0.0
    for r in runs:
        start, end = r.get("run_started_at") or r.get("created_at"), r.get("updated_at")
        if start and end and r.get("status") == "completed":
            total += max(
                0.0, (parse_time(end) - parse_time(start)).total_seconds() / 60
            )
    return total


def closing_issues(body: str | None) -> list[int]:
    return sorted({int(n) for n in CLOSES_RE.findall(body or "")})


def ask_time(pr: dict, issue_created: dict[int, str]) -> datetime:
    """When it was asked for: the earliest of the pull request and the issues it closes."""
    times = [parse_time(pr["created_at"])]
    times += [
        parse_time(issue_created[n])
        for n in closing_issues(pr.get("body"))
        if n in issue_created
    ]
    return min(times)


def median(xs):
    return round(statistics.median(xs), 2) if xs else None


def pull_request_metrics(gh: GitHub, since: datetime, until: datetime) -> dict:
    window = f"pull requests opened {iso(since)}..{iso(until)}, bots left out"
    prs = []
    for pr in gh.pages(
        f"/repos/{gh.repo}/pulls",
        {"state": "all", "sort": "created", "direction": "desc"},
    ):
        if parse_time(pr["created_at"]) < since:
            break
        if (
            parse_time(pr["created_at"]) < until
            and (pr.get("user") or {}).get("type") != "Bot"
        ):
            prs.append(pr)
    runs = runs_created(gh, since, until, event="pull_request")

    def attempt_one(run):
        try:
            return gh.get(f"/repos/{gh.repo}/actions/runs/{run['id']}/attempts/1").get(
                "conclusion"
            )
        except urllib.error.HTTPError:
            return None

    verdicts = [first_push(runs_of(pr, runs), attempt_one) for pr in prs]
    done = [v for v in verdicts if v]
    reds = sum(1 for v in done if v == "red")
    out = {
        "red_check_rate": measured(
            round(reds / len(done), 3) if done else None,
            "share of pull requests",
            f"GET /repos/{gh.repo}/pulls and /actions/runs?event=pull_request: {window}; red when a workflow run on the "
            "first push failed (a re-run counts its first attempt)",
            better="down",
            red=reds,
            of=len(done),
            no_result_yet=len(verdicts) - len(done),
        )
    }

    merged = [pr for pr in prs if pr.get("merged_at")]
    wanted = sorted({n for pr in merged for n in closing_issues(pr.get("body"))})
    issue_created = {}
    for n in wanted:
        try:
            issue_created[n] = gh.get(f"/repos/{gh.repo}/issues/{n}")["created_at"]
        except urllib.error.HTTPError:
            pass
    hours = [
        (parse_time(pr["merged_at"]) - ask_time(pr, issue_created)).total_seconds()
        / 3600
        for pr in merged
    ]
    out["ask_to_merge_hours"] = measured(
        median(hours),
        "hours (median)",
        f"GET /repos/{gh.repo}/pulls: {window}, merged; from the earliest of the pull request and the issues it closes "
        "(Closes #n) to the merge. An ask made only in chat starts when its pull request opens.",
        better="down",
        merged=len(merged),
        from_an_issue=sum(
            1
            for pr in merged
            if any(n in issue_created for n in closing_issues(pr.get("body")))
        ),
    )
    minutes = [run_minutes(runs_of(pr, runs)) for pr in merged]
    out["ci_minutes_per_merged_pr"] = measured(
        median(minutes),
        "minutes (median)",
        f"GET /repos/{gh.repo}/actions/runs?event=pull_request: {window}, merged; each workflow run's wall time "
        "(start to finish), added up per pull request. Jobs that run side by side count once, so runner minutes are "
        "higher.",
        better="down",
        merged=len(merged),
    )
    return out


def main_metrics(gh: GitHub, since: datetime, until: datetime) -> dict:
    runs = runs_created(gh, since, until, branch="main", event="push")
    done = [r for r in runs if r.get("status") == "completed"]
    failed = [r for r in done if r.get("conclusion") in RED]
    return {
        "failed_runs_on_main": measured(
            len(failed),
            "workflow runs",
            f"GET /repos/{gh.repo}/actions/runs?branch=main&event=push&created={iso(since)}..{iso(until)}",
            better="down",
            of=len(done),
            workflows=dict(sorted(Counter(r["name"] for r in failed).items())),
        )
    }


def cobertura_rate(xml: str) -> tuple[int, int]:
    """(lines covered, lines) from coverage.py's Cobertura XML."""
    root = re.search(r"<coverage\b[^>]*>", xml)
    covered = re.search(r'\blines-covered="(\d+)"', root.group(0)) if root else None
    lines = re.search(r'\blines-valid="(\d+)"', root.group(0)) if root else None
    if not covered or not lines or not int(lines.group(1)):
        raise ValueError("no lines-covered and lines-valid on <coverage>")
    return int(covered.group(1)), int(lines.group(1))


def lcov_rate(lcov: str) -> tuple[int, int]:
    """(lines hit, lines found) from an lcov file."""
    found = sum(int(n) for n in re.findall(r"^LF:(\d+)$", lcov, re.MULTILINE))
    hit = sum(int(n) for n in re.findall(r"^LH:(\d+)$", lcov, re.MULTILINE))
    if not found:
        raise ValueError("no LF lines")
    return hit, found


def newest_from_main(gh: GitHub, name: str) -> dict | None:
    """The newest unexpired artifact of that name from a run on main (the list comes roughly newest first)."""
    found = []
    for i, a in enumerate(
        gh.pages(f"/repos/{gh.repo}/actions/artifacts", {"name": name}, key="artifacts")
    ):
        if (
            not a.get("expired")
            and (a.get("workflow_run") or {}).get("head_branch") == "main"
        ):
            found.append(a)
        if found and i % 100 == 99:
            break
    return max(found, key=lambda a: a.get("created_at") or "") if found else None


def coverage(gh: GitHub, name: str, member: str, parse) -> dict:
    cmd = f"GET /repos/{gh.repo}/actions/artifacts?name={name}, the newest from main, {member}"
    try:
        artifact = newest_from_main(gh, name)
        if artifact is None:
            return not_measured(
                f"no {name} artifact from main (CI keeps them for a while)", cmd
            )
        if not gh.token:
            return not_measured(
                "downloading CI artifacts needs a GitHub token (GITHUB_TOKEN)", cmd
            )
        z = zipfile.ZipFile(
            io.BytesIO(gh.request(artifact["archive_download_url"], accept="*/*"))
        )
        covered, lines = parse(z.read(member).decode("utf-8", errors="replace"))
    except (urllib.error.URLError, KeyError, ValueError, zipfile.BadZipFile) as e:
        return not_measured(f"couldn't read the {name} artifact: {e}", cmd)
    return measured(
        round(100 * covered / lines, 2),
        "% of lines",
        cmd,
        better="up",
        lines=lines,
        covered=covered,
        commit=(artifact.get("workflow_run") or {}).get("head_sha", "")[:12],
        created=artifact.get("created_at"),
    )


def community(gh: GitHub) -> dict:
    repo = gh.get(f"/repos/{gh.repo}")
    out = {
        "stars": measured(
            repo["stargazers_count"], "stars", f"GET /repos/{gh.repo}", better="up"
        ),
        "forks": measured(
            repo["forks_count"], "forks", f"GET /repos/{gh.repo}", better="up"
        ),
    }
    issues = prs = open_issues = 0
    for item in gh.pages(f"/repos/{gh.repo}/issues", {"state": "all"}):
        if (
            item.get("author_association") in INSIDE
            or (item.get("user") or {}).get("type") == "Bot"
        ):
            continue
        if "pull_request" in item:
            prs += 1
        else:
            issues += 1
            open_issues += item.get("state") == "open"
    cmd = f"GET /repos/{gh.repo}/issues?state=all: authors who aren't owner, member or collaborator, bots left out"
    out["outside_issues"] = measured(issues, "issues", cmd, better="up")
    out["outside_pull_requests"] = measured(prs, "pull requests", cmd, better="up")
    out["open_user_reported_issues"] = measured(
        open_issues, "issues", cmd, better="down"
    )
    return out


def github_metrics(gh: GitHub, since: datetime, until: datetime) -> dict:
    parts = [
        lambda: pull_request_metrics(gh, since, until),
        lambda: main_metrics(gh, since, until),
        lambda: {
            "backend_coverage": coverage(
                gh, "backend-coverage", "coverage.xml", lambda s: cobertura_rate(s)
            )
        },
        lambda: {
            "frontend_coverage": coverage(
                gh, "frontend-coverage", "lcov.info", lambda s: lcov_rate(s)
            )
        },
        lambda: community(gh),
    ]
    names = [
        ["red_check_rate", "ask_to_merge_hours", "ci_minutes_per_merged_pr"],
        ["failed_runs_on_main"],
        ["backend_coverage"],
        ["frontend_coverage"],
        [
            "stars",
            "forks",
            "outside_issues",
            "outside_pull_requests",
            "open_user_reported_issues",
        ],
    ]
    out = {}
    for part, keys in zip(parts, names):
        try:
            out.update(part())
        except (urllib.error.URLError, OSError, ValueError, KeyError) as e:
            out.update({k: not_measured(f"GitHub didn't answer: {e}") for k in keys})
    return out


def offline_github(why: str) -> dict:
    keys = [
        "red_check_rate",
        "ask_to_merge_hours",
        "ci_minutes_per_merged_pr",
        "failed_runs_on_main",
        "backend_coverage",
        "frontend_coverage",
        "stars",
        "forks",
        "outside_issues",
        "outside_pull_requests",
        "open_user_reported_issues",
    ]
    return {k: not_measured(why) for k in keys}


# --- Install and the Pi ---


def install_metrics() -> dict:
    why = "no timed install on an arm64 runner yet (#481)"
    return {
        "install_to_first_answer_s": not_measured(why),
        "idle_ram_mb": not_measured(why),
        "disk_mb": not_measured(why),
    }


# --- The line and the comparison ---


def repo_slug(root: Path = ROOT) -> str:
    url = git("remote", "get-url", "origin", root=root) or ""
    m = re.search(r"github\.com[:/]([^/]+/[^/]+?)(?:\.git)?/?$", url)
    return m.group(1) if m else "adeelahmad/lense"


def scorecard(args, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc).replace(microsecond=0)
    since = now - timedelta(days=args.days)
    repo = args.repo or repo_slug()
    metrics = {}
    metrics.update(friction())
    metrics["tests_defined"] = tests_defined()
    metrics["backend_tests_collected"] = backend_tests_collected()
    gh = None
    if args.offline:
        metrics.update(offline_github("run with --offline"))
    else:
        gh = GitHub(repo, os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN"))
        metrics.update(github_metrics(gh, since, now))
    metrics.update(install_metrics())
    return {
        "at": iso(now),
        "repo": repo,
        "commit": git("rev-parse", "HEAD"),
        "tag": git("describe", "--tags"),
        "release": git("describe", "--tags", "--abbrev=0"),
        "window": {"since": iso(since), "until": iso(now)},
        "github_calls": gh.calls if gh else 0,
        "metrics": metrics,
    }


# what may never go down for a turn to count as better (AGENTS.md: never win by weakening a test or baseline)
GUARDED = [
    "journeys_passing_computer",
    "journeys_passing_phone",
    "tests_defined",
    "backend_tests_collected",
    "backend_coverage",
    "frontend_coverage",
]


def compare(prev: dict, cur: dict) -> tuple[list[str], list[str]]:
    """Lines saying what changed since prev, and the guards that tripped."""
    lines, tripped = [], []
    lines.append(f"Since {prev.get('tag') or prev.get('commit')} ({prev.get('at')}):")
    pm, cm = prev.get("metrics", {}), cur.get("metrics", {})
    for name, now in cm.items():
        was = pm.get(name)
        if was is None:
            continue
        a, b = was.get("value"), now.get("value")
        if a is not None and b is None:
            tripped.append(
                f"{name} was measured ({a}) and isn't now: {now.get('not_measured')}"
            )
            continue
        if not isinstance(a, (int, float)) or not isinstance(b, (int, float)):
            continue
        if name in GUARDED and b < a:
            tripped.append(f"{name} went down: {a} -> {b}")
        of_a, of_b = was.get("of"), now.get("of")
        if (
            name.startswith("journeys_passing")
            and isinstance(of_a, int)
            and isinstance(of_b, int)
            and of_b < of_a
        ):
            tripped.append(f"{name}: journeys went from {of_a} to {of_b}")
        if a == b:
            continue
        better = now.get("better")
        if better not in ("up", "down"):
            verdict = "changed"
        else:
            verdict = "better" if (b > a) == (better == "up") else "worse"
        lines.append(f"  {name}: {a} -> {b} {now.get('unit', '')} ({verdict})")
    for device in BASELINES:
        was = (pm.get(f"journeys_passing_{device}") or {}).get("source") or {}
        now = (cm.get(f"journeys_passing_{device}") or {}).get("source") or {}
        if was.get("sha256") and now.get("sha256") and was["sha256"] != now["sha256"]:
            if was.get("surveyed") == now.get("surveyed"):
                tripped.append(
                    f"{now.get('file')} changed without a new survey (still surveyed {now.get('surveyed')})"
                )
            else:
                lines.append(
                    f"  {now.get('file')}: new survey {now.get('surveyed')} at {now.get('survey_commit')}"
                )
    if len(lines) == 1:
        lines.append("  no measured number changed")
    return lines, tripped


def last_line(path: Path) -> dict | None:
    try:
        rows = [
            line
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    except OSError:
        return None
    return json.loads(rows[-1]) if rows else None


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument(
        "--log",
        type=Path,
        help="JSON Lines file: compare with its last line, then append this one",
    )
    p.add_argument(
        "--days",
        type=int,
        default=7,
        help="the window for the CI numbers (default 7 days)",
    )
    p.add_argument(
        "--repo", help="owner/name on GitHub (default: from the origin remote)"
    )
    p.add_argument("--offline", action="store_true", help="no GitHub calls")
    args = p.parse_args(argv)

    card = scorecard(args)
    line = json.dumps(card, separators=(",", ":"), sort_keys=False)
    print(line)
    tripped = []
    if args.log:
        prev = last_line(args.log)
        if prev:
            lines, tripped = compare(prev, card)
            print("\n".join(lines), file=sys.stderr)
        args.log.parent.mkdir(parents=True, exist_ok=True)
        with args.log.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
        print(f"Appended to {args.log}", file=sys.stderr)
    for t in tripped:
        print(f"Guard: {t}", file=sys.stderr)
    if tripped:
        print(
            "This turn doesn't count as better (AGENTS.md: never win by weakening a test or baseline).",
            file=sys.stderr,
        )
    return 1 if tripped else 0


if __name__ == "__main__":
    sys.exit(main())
