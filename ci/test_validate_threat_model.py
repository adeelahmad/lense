#!/usr/bin/env python3
"""Tests for validate_threat_model.py: python3 ci/test_validate_threat_model.py (needs pyyaml and git)"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml

from validate_threat_model import PROSE_VERSION, changed_since, validate

PROSE = """# Model

## 1.11 Provided

| ID | Property |
| --- | --- |
| P-ONE | One. |

## 1.12 Not provided

| ID | Not provided |
| --- | --- |
| N-ONE | One. |

## 1.15 Known non-findings

| ID | What |
| --- | --- |
| KNF-ONE | One. |

## 1.18 Open questions

- **Q1**: why?
"""

SIDECAR = {
    "schema": "threat-model-sidecar/v2",
    "prose_version": PROSE_VERSION,
    "x-modeled-commit": "abc",
    "x-model-date": "2026-10-17",
    "x-repository": "https://example.com/lens",
    "properties_claimed": [{"id": "P-ONE"}],
    "components": [],
    "entry_points": [],
    "adversaries": [],
    "downstream_responsibilities": [],
    "known_misuses": [],
    "dispositions": [],
    "known_non_findings": [{"id": "KNF-ONE"}],
    "properties_disclaimed": [{"id": "N-ONE"}],
}

EXPORT = {
    "spec_version": 1,
    "repository": "https://example.com/lens",
    "commit": "abc",
    "date": "2026-10-17",
    "properties_provided": [{"property": "P-ONE"}],
    "components": [],
    "entry_points": [],
    "adversaries": [],
    "downstream_responsibilities": [],
    "known_misuse": [],
    "dispositions": [],
    "known_non_findings": [{"claim": "one"}],
    "open_questions": [{"claim": "Q1: why?"}],
    "properties_not_provided": [{"property": "N-ONE"}],
}

GIT_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "test",
    "GIT_AUTHOR_EMAIL": "test@localhost",
    "GIT_COMMITTER_NAME": "test",
    "GIT_COMMITTER_EMAIL": "test@localhost",
}


class Model:
    """A threat model in a throwaway git repository."""

    def __init__(self, root: Path):
        self.root = root
        self.write(PROSE, SIDECAR, EXPORT)
        self.git("init", "-q", "-b", "main")
        self.commit("model")

    def write(self, prose=None, sidecar=None, export=None):
        if prose is not None:
            (self.root / "threat-model.md").write_text(prose)
        if sidecar is not None:
            (self.root / "threat-model.yaml").write_text(
                yaml.safe_dump(sidecar, sort_keys=False)
            )
        if export is not None:
            (self.root / "threat-model.json").write_text(
                json.dumps(export, indent=2) + "\n"
            )

    def read(self):
        return (
            (self.root / "threat-model.md").read_text(),
            yaml.safe_load((self.root / "threat-model.yaml").read_text()),
            json.loads((self.root / "threat-model.json").read_text()),
        )

    def git(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git", "-c", "commit.gpgsign=false", *args],
            cwd=self.root,
            env=GIT_ENV,
            capture_output=True,
            text=True,
        )

    def commit(self, message: str):
        self.git("add", "-A")
        done = self.git("commit", "-q", "-m", message)
        assert done.returncode == 0, done.stderr


def add_claimed(model: Model, pid: str):
    prose, sidecar, export = model.read()
    prose = prose.replace("| P-ONE | One. |\n", f"| P-ONE | One. |\n| {pid} | Two. |\n")
    sidecar["properties_claimed"].append({"id": pid})
    export["properties_provided"].append({"property": pid})
    model.write(prose, sidecar, export)


def add_disclaimed(model: Model, nid: str):
    prose, sidecar, export = model.read()
    prose = prose.replace("| N-ONE | One. |\n", f"| N-ONE | One. |\n| {nid} | Two. |\n")
    sidecar["properties_disclaimed"].append({"id": nid})
    export["properties_not_provided"].append({"property": nid})
    model.write(prose, sidecar, export)


class ValidateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.model = Model(Path(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def test_agreeing_model_passes(self):
        self.assertEqual(validate(self.model.root), [])
        self.assertEqual(changed_since(self.model.root, "HEAD"), [])

    def test_prose_hash_is_refused(self):
        self.model.write(
            sidecar={**SIDECAR, "prose_version": "threat-model.md@sha256:00"}
        )
        [error] = validate(self.model.root)
        self.assertIn(PROSE_VERSION, error)

    def test_id_only_in_prose_is_caught(self):
        prose, _, _ = self.model.read()
        self.model.write(
            prose.replace("| P-ONE | One. |\n", "| P-ONE | One. |\n| P-TWO | Two. |\n")
        )
        self.assertIn(
            "P-TWO is defined in threat-model.md but not in threat-model.yaml",
            validate(self.model.root),
        )

    def test_prose_change_without_companions_fails(self):
        self.model.write(PROSE.replace("why?", "why not?"))
        [error] = changed_since(self.model.root, "HEAD")
        self.assertIn("threat-model.md changed since HEAD", error)
        self.model.commit("prose only")
        self.assertEqual(len(changed_since(self.model.root, "HEAD~1")), 1)

    def test_prose_change_with_a_companion_passes(self):
        self.model.write(
            PROSE.replace("why?", "why not?"),
            export={**EXPORT, "open_questions": [{"claim": "Q1: why not?"}]},
        )
        self.assertEqual(changed_since(self.model.root, "HEAD"), [])
        self.model.commit("prose and export")
        self.assertEqual(changed_since(self.model.root, "HEAD~1"), [])

    def test_companions_alone_may_change(self):
        self.model.write(sidecar={**SIDECAR, "x-note": "tidied"})
        self.assertEqual(changed_since(self.model.root, "HEAD"), [])

    def test_unknown_base_is_an_error(self):
        [error] = changed_since(self.model.root, "no-such-ref")
        self.assertIn("can't compare with 'no-such-ref'", error)

    def test_cited_tests_must_exist(self):
        tests = self.model.root / "fastapi_backend" / "tests"
        tests.mkdir(parents=True)
        (tests / "test_x.py").write_text(
            "def test_a():\n    pass\n\n\nasync def test_b():\n    pass\n"
        )
        cited = "Tested by `fastapi_backend/tests/test_x.py` `test_a`, `test_b`."
        self.model.write(
            PROSE.replace("| P-ONE | One. |", f"| P-ONE | One. {cited} |"),
            {**SIDECAR, "x-tests": ["fastapi_backend/tests/test_x.py::test_a"]},
        )
        self.assertEqual(validate(self.model.root), [])
        self.model.write(
            PROSE.replace(
                "| P-ONE | One. |",
                "| P-ONE | One. Tested by `fastapi_backend/tests/test_x.py` `test_a`, `test_gone`. |",
            ),
            {**SIDECAR, "x-tests": ["fastapi_backend/tests/test_y.py::test_a"]},
        )
        errors = validate(self.model.root)
        self.assertEqual(len(errors), 2, errors)
        self.assertIn(
            "threat-model.md:7 cites test_gone in fastapi_backend/tests/test_x.py",
            errors[0],
        )
        self.assertIn(
            "cites fastapi_backend/tests/test_y.py, which doesn't exist", errors[1]
        )

    def test_two_changes_to_the_prose_merge_cleanly(self):
        # Issue #507: each branch changes the prose and the companions in its own place, so they merge without a
        # conflict and the merge still validates. A hash of the whole prose in the YAML made every such pair conflict.
        m = self.model
        m.git("checkout", "-q", "-b", "claimed")
        add_claimed(m, "P-TWO")
        m.commit("claim P-TWO")
        m.git("checkout", "-q", "main")
        add_disclaimed(m, "N-TWO")
        m.commit("disclaim N-TWO")
        merged = m.git("merge", "-q", "--no-edit", "claimed")
        self.assertEqual(merged.returncode, 0, merged.stdout + merged.stderr)
        self.assertEqual(validate(m.root), [])
        self.assertEqual(changed_since(m.root, "HEAD^1"), [])
        self.assertEqual(changed_since(m.root, "HEAD^2"), [])


if __name__ == "__main__":
    unittest.main()
