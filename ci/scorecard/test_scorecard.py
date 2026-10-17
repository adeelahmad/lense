"""Tests for the scorecard (python3 -m unittest discover ci/scorecard). No network: GitHub's answers are made up."""

from __future__ import annotations

import io
import json
import tempfile
import unittest
import zipfile
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import measure_install as mi
import scorecard as sc


def run(id, sha, created, conclusion="success", status="completed", attempt=1, **extra):
    return {
        "id": id,
        "name": extra.pop("name", "CI"),
        "head_sha": sha,
        "head_branch": extra.pop("branch", "feature"),
        "head_repository": {"full_name": extra.pop("repo", "o/r")},
        "created_at": created,
        "run_started_at": extra.pop("started", created),
        "updated_at": extra.pop("updated", created),
        "status": status,
        "conclusion": conclusion,
        "run_attempt": attempt,
    }


def pr(created, closed=None, merged=None, branch="feature", body=None):
    return {
        "created_at": created,
        "closed_at": closed,
        "merged_at": merged,
        "body": body,
        "head": {"ref": branch, "repo": {"full_name": "o/r"}},
    }


def baseline(surveyed, journeys):
    return json.dumps({"surveyed": surveyed, "commit": "abc123", "journeys": journeys})


class Friction(unittest.TestCase):
    def test_adds_up_every_journey_and_counts_the_passing_ones(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "ci/clicks").mkdir(parents=True)
            ok = {"ok": True, "clicks": 3, "keys": 2, "prompts": 1, "swipes": 0}
            failed = {"ok": False, "clicks": 1, "keys": 0, "prompts": 0, "swipes": 2}
            (root / sc.BASELINES["computer"]).write_text(baseline("t1", [ok, failed]))
            (root / sc.BASELINES["phone"]).write_text(baseline("t2", [ok, ok, failed]))
            m = sc.friction(root)
        self.assertEqual(m["journeys_passing_computer"]["value"], 1)
        self.assertEqual(m["journeys_passing_computer"]["of"], 2)
        self.assertEqual(m["clicks_computer"]["value"], 4)
        self.assertEqual(m["keys_computer"]["value"], 2)
        self.assertNotIn("swipes_computer", m)
        self.assertEqual(m["journeys_passing_phone"]["value"], 2)
        self.assertEqual(m["taps_phone"]["value"], 7)
        self.assertEqual(m["swipes_phone"]["value"], 2)
        self.assertEqual(m["prompts_phone"]["value"], 2)
        self.assertEqual(m["journeys_passing_phone"]["source"]["surveyed"], "t2")
        self.assertEqual(m["clicks_computer"]["better"], "down")

    def test_a_missing_baseline_is_not_measured(self):
        with tempfile.TemporaryDirectory() as d:
            m = sc.friction(Path(d))
        self.assertIsNone(m["journeys_passing_computer"]["value"])
        self.assertIn("can't read", m["taps_phone"]["not_measured"])


class Tests(unittest.TestCase):
    def test_counts_test_functions_per_folder(self):
        files = {
            "fastapi_backend/tests/test_a.py": "def test_one():\n    pass\n\nasync def test_two():\n    pass\n",
            "fastapi_backend/tests/conftest.py": "def test_helper():\n    pass\n",
            "fastapi_backend/app/thing.py": "def test_not_a_test():\n    pass\n",
            "nextjs-frontend/__tests__/page.test.tsx": "describe('x', () => {\n  it('a', () => {});\n"
            "  test('b', () => {});\n  it.each([1, 2])('c %s', () => {});\n  // it('commented', ...)\n});\n",
            "extensions/browser/src/a.spec.ts": "test('x', () => {});\n",
            "README.md": "def test_in_docs():\n",
        }
        self.assertEqual(
            sc.count_tests(files),
            {"extensions": 1, "fastapi_backend": 2, "nextjs-frontend": 3},
        )

    def test_the_backend_not_installed_is_not_measured(self):
        with tempfile.TemporaryDirectory() as d:
            m = sc.backend_tests_collected(Path(d))
        self.assertIsNone(m["value"])
        self.assertIn("uv sync", m["not_measured"])


class FirstPush(unittest.TestCase):
    def test_green_when_no_check_on_the_first_push_failed(self):
        runs = [
            run(1, "a", "2026-10-01T00:00:00Z"),
            run(2, "a", "2026-10-01T00:00:01Z", "skipped", name="Install"),
            run(3, "b", "2026-10-01T01:00:00Z", "failure"),
        ]
        self.assertEqual(sc.first_push(runs), "green")

    def test_red_when_one_failed(self):
        runs = [
            run(1, "a", "2026-10-01T00:00:00Z"),
            run(2, "a", "2026-10-01T00:00:01Z", "timed_out"),
        ]
        self.assertEqual(sc.first_push(runs), "red")

    def test_a_cancelled_run_is_not_red(self):
        self.assertEqual(
            sc.first_push([run(1, "a", "2026-10-01T00:00:00Z", "cancelled")]), "green"
        )

    def test_none_while_a_check_still_runs_or_none_ran(self):
        runs = [
            run(1, "a", "2026-10-01T00:00:00Z"),
            run(2, "a", "2026-10-01T00:00:00Z", None, "in_progress"),
        ]
        self.assertIsNone(sc.first_push(runs))
        self.assertIsNone(sc.first_push([]))

    def test_a_re_run_counts_its_first_attempt(self):
        runs = [run(1, "a", "2026-10-01T00:00:00Z", attempt=2)]
        self.assertEqual(sc.first_push(runs, lambda r: "failure"), "red")
        self.assertEqual(sc.first_push(runs, lambda r: "success"), "green")


class Matching(unittest.TestCase):
    def test_runs_of_a_pull_request_are_its_branch_while_it_was_open(self):
        runs = [
            run(
                1, "a", "2026-10-01T00:59:00Z"
            ),  # an earlier pull request on the same branch
            run(2, "b", "2026-10-01T01:00:05Z"),
            run(3, "b", "2026-10-01T02:03:00Z"),  # closing it, within five minutes
            run(4, "c", "2026-10-01T03:00:00Z"),  # a later one
            run(5, "b", "2026-10-01T01:30:00Z", branch="other"),
            run(6, "b", "2026-10-01T01:30:00Z", repo="fork/r"),
        ]
        p = pr("2026-10-01T01:00:00Z", closed="2026-10-01T02:00:00Z")
        self.assertEqual([r["id"] for r in sc.runs_of(p, runs)], [2, 3])

    def test_minutes_add_up_finished_runs(self):
        runs = [
            run(1, "a", "2026-10-01T00:00:00Z", updated="2026-10-01T00:10:00Z"),
            run(
                2,
                "a",
                "2026-10-01T00:00:00Z",
                started="2026-10-01T00:01:00Z",
                updated="2026-10-01T00:06:00Z",
            ),
            run(
                3,
                "a",
                "2026-10-01T00:00:00Z",
                None,
                "in_progress",
                updated="2026-10-01T05:00:00Z",
            ),
        ]
        self.assertEqual(sc.run_minutes(runs), 15)


class Asks(unittest.TestCase):
    def test_closing_keywords(self):
        body = "Closes #12, fixes #7 and resolves: #9. Part of #481, see #3."
        self.assertEqual(sc.closing_issues(body), [7, 9, 12])
        self.assertEqual(sc.closing_issues(None), [])

    def test_the_ask_is_the_earliest_of_the_pull_request_and_its_issues(self):
        p = pr("2026-10-02T00:00:00Z", body="Closes #5\nCloses #6")
        created = {5: "2026-10-01T12:00:00Z"}
        self.assertEqual(
            sc.ask_time(p, created), datetime(2026, 10, 1, 12, tzinfo=timezone.utc)
        )
        self.assertEqual(
            sc.ask_time(pr("2026-10-02T00:00:00Z"), created),
            datetime(2026, 10, 2, tzinfo=timezone.utc),
        )


class Coverage(unittest.TestCase):
    def test_cobertura(self):
        xml = '<?xml version="1.0" ?>\n<coverage version="7" lines-valid="200" lines-covered="150" line-rate="0.75">'
        self.assertEqual(sc.cobertura_rate(xml), (150, 200))
        with self.assertRaises(ValueError):
            sc.cobertura_rate("<coverage/>")

    def test_lcov(self):
        lcov = "SF:a.ts\nLF:10\nLH:5\nend_of_record\nSF:b.ts\nLF:30\nLH:30\nend_of_record\n"
        self.assertEqual(sc.lcov_rate(lcov), (35, 40))
        with self.assertRaises(ValueError):
            sc.lcov_rate("")


def card(**values):
    metrics = {}
    for name, v in values.items():
        if isinstance(v, dict):
            metrics[name] = v
        elif v is None:
            metrics[name] = sc.not_measured("gone")
        else:
            better = "down" if name.startswith("clicks") else "up"
            metrics[name] = sc.measured(v, "n", "cmd", better=better)
    return {"at": "t", "tag": "v1", "metrics": metrics}


def journeys(value, of, surveyed, sha):
    return sc.measured(
        value,
        "journeys",
        "cmd",
        better="up",
        of=of,
        source={"file": "f", "surveyed": surveyed, "sha256": sha},
    )


class Compare(unittest.TestCase):
    def test_improvements_trip_nothing(self):
        lines, tripped = sc.compare(
            card(tests_defined=10, clicks_computer=50),
            card(tests_defined=12, clicks_computer=40),
        )
        self.assertEqual(tripped, [])
        self.assertIn("  tests_defined: 10 -> 12 n (better)", lines)
        self.assertIn("  clicks_computer: 50 -> 40 n (better)", lines)

    def test_fewer_tests_or_less_coverage_trips(self):
        _, tripped = sc.compare(
            card(tests_defined=10, backend_coverage=90.0, clicks_computer=40),
            card(tests_defined=9, backend_coverage=89.5, clicks_computer=30),
        )
        self.assertEqual(len(tripped), 2)
        self.assertIn("tests_defined went down: 10 -> 9", tripped)

    def test_dropping_a_journey_trips_even_when_all_pass(self):
        _, tripped = sc.compare(
            card(journeys_passing_phone=journeys(30, 31, "t1", "x")),
            card(journeys_passing_phone=journeys(30, 30, "t2", "y")),
        )
        self.assertEqual(
            tripped, ["journeys_passing_phone: journeys went from 31 to 30"]
        )

    def test_a_number_no_longer_measured_trips(self):
        _, tripped = sc.compare(
            card(frontend_coverage=70.0), card(frontend_coverage=None)
        )
        self.assertEqual(len(tripped), 1)
        self.assertIn("isn't now: gone", tripped[0])

    def test_a_baseline_changed_without_a_new_survey_trips(self):
        _, tripped = sc.compare(
            card(journeys_passing_computer=journeys(31, 31, "t1", "x")),
            card(journeys_passing_computer=journeys(31, 31, "t1", "y")),
        )
        self.assertEqual(len(tripped), 1)
        self.assertIn("without a new survey", tripped[0])
        lines, tripped = sc.compare(
            card(journeys_passing_computer=journeys(31, 31, "t1", "x")),
            card(journeys_passing_computer=journeys(31, 31, "t2", "y")),
        )
        self.assertEqual(tripped, [])
        self.assertIn("new survey t2", lines[-1])


class Main(unittest.TestCase):
    def test_offline_line_then_log_compares_and_appends(self):
        quick = mock.patch.object(
            sc, "backend_tests_collected", return_value=sc.not_measured("skipped")
        )
        with tempfile.TemporaryDirectory() as d, quick:
            log = Path(d) / "loop" / "scorecard.jsonl"
            for _ in range(2):
                out, err = io.StringIO(), io.StringIO()
                with redirect_stdout(out), redirect_stderr(err):
                    code = sc.main(["--offline", "--log", str(log)])
                self.assertEqual(code, 0, err.getvalue())
            rows = log.read_text().splitlines()
        self.assertEqual(len(rows), 2)
        line = json.loads(out.getvalue())
        self.assertEqual(line, json.loads(rows[1]))
        self.assertEqual(line["github_calls"], 0)
        for name, m in line["metrics"].items():
            # every number is measured with its command, or says why not
            self.assertTrue(
                m.get("cmd") if m["value"] is not None else m.get("not_measured"), name
            )
        self.assertIn("Since", err.getvalue())


if __name__ == "__main__":
    unittest.main()


class FakeGitHub(sc.GitHub):
    """Answers the artifacts list and downloads from memory."""

    def __init__(self, artifacts: dict[str, dict[str, str]], token="t"):
        super().__init__("o/r", token)
        self.artifacts = artifacts

    def pages(self, path, params, key=None):
        name = params.get("name")
        if name not in self.artifacts:
            return iter([])
        run = {"head_branch": "main", "head_sha": "abcdef1234567890"}
        return iter(
            [
                {
                    "expired": False,
                    "created_at": "t",
                    "workflow_run": run,
                    "archive_download_url": name,
                },
            ]
        )

    def request(self, url, accept="application/vnd.github+json"):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            for member, text in self.artifacts[url].items():
                z.writestr(member, text)
        return buf.getvalue()


class Install(unittest.TestCase):
    def test_reads_the_timed_install_from_main(self):
        made = {
            "install_to_first_answer_s": 812,
            "idle_ram_mb": 1450.3,
            "disk_mb": 6120.0,
            "build_cache_mb": 900.0,
            "containers_mb": {"backend": 700.0},
            "machine": "GitHub ubuntu-24.04-arm",
            "arch": "aarch64",
            "target": "full",
        }
        gh = FakeGitHub(
            {"install-scorecard": {"install-scorecard.json": json.dumps(made)}}
        )
        m = sc.install_metrics(gh)
        self.assertEqual(m["install_to_first_answer_s"]["value"], 812)
        self.assertEqual(m["idle_ram_mb"]["containers"], {"backend": 700.0})
        self.assertEqual(m["disk_mb"]["build_cache_mb"], 900.0)
        self.assertEqual(m["disk_mb"]["arch"], "aarch64")
        self.assertEqual(m["disk_mb"]["commit"], "abcdef123456")
        self.assertEqual(m["disk_mb"]["better"], "down")

    def test_not_measured_without_an_artifact_or_a_token(self):
        m = sc.install_metrics(FakeGitHub({}))
        self.assertIn("no install-scorecard artifact", m["idle_ram_mb"]["not_measured"])
        m = sc.install_metrics(FakeGitHub({"install-scorecard": {}}, token=None))
        self.assertIn("needs a GitHub token", m["disk_mb"]["not_measured"])

    def test_coverage_from_an_artifact(self):
        xml = '<coverage lines-valid="400" lines-covered="300">'
        gh = FakeGitHub({"backend-coverage": {"coverage.xml": xml}})
        m = sc.coverage(gh, "backend-coverage", "coverage.xml", sc.cobertura_rate)
        self.assertEqual(
            (m["value"], m["lines"], m["commit"]), (75.0, 400, "abcdef123456")
        )


class MeasureInstall(unittest.TestCase):
    def test_sizes(self):
        self.assertAlmostEqual(mi.to_mb("1.5GiB"), 1610.6127, places=3)
        self.assertEqual(mi.to_mb("512MB"), 512)
        self.assertEqual(mi.to_mb("0B"), 0)
        self.assertAlmostEqual(mi.to_mb("12.2kB"), 0.0122)
        with self.assertRaises(ValueError):
            mi.to_mb("lots")

    def test_docker_stats_and_df(self):
        stats = [
            {"Name": "lens-backend-1", "MemUsage": "700MiB / 15.6GiB"},
            {"Name": "lens-db-1", "MemUsage": "1.2GiB / 15.6GiB"},
        ]
        self.assertEqual(
            mi.ram_mb(stats), {"lens-backend-1": 734.0, "lens-db-1": 1288.5}
        )
        df = [
            {"Type": "Images", "Size": "3.2GB"},
            {"Type": "Build Cache", "Size": "0B"},
        ]
        self.assertEqual(mi.disk_mb(df), {"Images": 3200.0, "Build Cache": 0.0})
