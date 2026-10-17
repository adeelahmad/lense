#!/usr/bin/env python3
"""Tests for check_requirements.py: python3 ci/test_check_requirements.py"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from check_requirements import check

BLOB = "https://github.com/adeelahmad/lense/blob/main/"
PULL = "https://github.com/adeelahmad/lense/pull/"
ISSUE = "https://github.com/adeelahmad/lense/issues/9"

README = """# Lens

## Install

One line. ([checks](docs/requirements.md#install))

## What you can do with it

- **Search.** Find words. ([search](docs/search.md),
  [checks](docs/requirements.md#search))

## Light by default

Small. ([checks](docs/requirements.md#light-by-default))

## In progress

- **Feeds** ([#12](PULL12)).

## Planned

- **Studio** ([automations](docs/automations.md)).
"""

REQUIREMENTS = """# Requirements

## Install

| Requirement | Checks |
|---|---|
| One line | [install.yml](BLOBci/install.yml) `Install` |

## Search

| Requirement | Checks |
|---|---|
| Finds words | [test_search.py](BLOBtests/test_search.py) `test_finds`, `Words` |
| In clicks | [journeys](BLOBci/journeys.mjs) `search-word`; [baseline](BLOBci/baseline.json) `search-word` ≤ 2 clicks, ≤ 9 keys |
| By meaning | No check yet ([#9](ISSUE)) |

## Light by default

| Requirement | Checks |
|---|---|
| Small | [components](BLOBdocs/components.md) |
"""


class CheckRequirements(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.write("README.md", README.replace("PULL", PULL))
        self.write(
            "docs/requirements.md",
            REQUIREMENTS.replace("BLOB", BLOB).replace("ISSUE", ISSUE),
        )
        self.write("docs/components.md", "# Components\n")
        self.write(
            "ci/install.yml", "jobs:\n  install:\n    steps:\n      - name: Install\n"
        )
        self.write(
            "tests/test_search.py",
            "def test_finds():\n    pass\n\n\nclass Words:\n    pass\n",
        )
        self.write("ci/journeys.mjs", 'export default [{ id: "search-word" }];\n')
        self.baseline(clicks=2)
        self.write("CHANGELOG.md", f"# Changelog\n\n- feeds ([#11]({PULL}11))\n")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def write(self, name: str, text: str) -> None:
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def edit(self, name: str, old: str, new: str) -> None:
        path = self.root / name
        text = path.read_text(encoding="utf-8")
        self.assertIn(old, text)
        path.write_text(text.replace(old, new), encoding="utf-8")

    def baseline(self, clicks: int, ok: bool = True, keys: int = 9) -> None:
        journey = {"id": "search-word", "clicks": clicks, "keys": keys, "ok": ok}
        self.write("ci/baseline.json", json.dumps({"journeys": [journey]}))

    def problems(self) -> list[str]:
        return check(self.root)

    def test_clean(self) -> None:
        self.assertEqual(self.problems(), [])

    def test_a_test_that_is_gone(self) -> None:
        self.edit("tests/test_search.py", "def test_finds", "def test_found")
        [problem] = self.problems()
        self.assertIn("`test_finds` is gone from tests/test_search.py", problem)

    def test_a_file_that_is_gone(self) -> None:
        (self.root / "tests/test_search.py").unlink()
        [problem] = self.problems()
        self.assertIn("tests/test_search.py is gone", problem)

    def test_a_journey_and_a_workflow_step_that_are_gone(self) -> None:
        self.edit("ci/journeys.mjs", '"search-word"', '"find-word"')
        self.edit("ci/install.yml", "name: Install", "name: Set up")
        problems = "\n".join(self.problems())
        self.assertIn("`search-word` is gone from ci/journeys.mjs", problems)
        self.assertIn("`Install` is gone from ci/install.yml", problems)

    def test_a_measured_number_not_met(self) -> None:
        self.baseline(clicks=3)
        [problem] = self.problems()
        self.assertIn("takes 3 clicks in baseline.json, not ≤ 2", problem)
        self.baseline(clicks=2, keys=10)
        [problem] = self.problems()
        self.assertIn("takes 10 keys in baseline.json, not ≤ 9", problem)
        self.baseline(clicks=1, ok=False)
        [problem] = self.problems()
        self.assertIn("`search-word` fails in baseline.json", problem)

    def test_no_check_yet_needs_an_issue(self) -> None:
        self.edit("docs/requirements.md", f"([#9]({ISSUE}))", "")
        [problem] = self.problems()
        self.assertIn("'no check yet' links no issue", problem)

    def test_a_row_without_a_check(self) -> None:
        self.edit(
            "docs/requirements.md",
            f"[components]({BLOB}docs/components.md)",
            "the docs",
        )
        [problem] = self.problems()
        self.assertIn("names no check", problem)

    def test_a_built_item_without_checks(self) -> None:
        self.edit("README.md", ",\n  [checks](docs/requirements.md#search)", "")
        problems = "\n".join(self.problems())
        self.assertIn(
            "README › What you can do with it › Search.: links no section", problems
        )
        self.assertIn(
            "requirements.md › Search: no built item in the README links it", problems
        )

    def test_a_link_to_a_missing_section(self) -> None:
        self.edit("README.md", "requirements.md#search", "requirements.md#find")
        problems = "\n".join(self.problems())
        self.assertIn("docs/requirements.md#find has no checks", problems)

    def test_in_progress_already_released(self) -> None:
        self.edit("README.md", f"[#12]({PULL}12)", f"[#11]({PULL}11)")
        [problem] = self.problems()
        self.assertIn("#11 is released", problem)

    def test_in_progress_without_a_pull_request(self) -> None:
        self.edit("README.md", f" ([#12]({PULL}12))", "")
        [problem] = self.problems()
        self.assertIn("In progress: links no pull request", problem)

    def test_planned_links_somewhere(self) -> None:
        self.edit("README.md", " ([automations](docs/automations.md))", "")
        [problem] = self.problems()
        self.assertIn("Planned: links nowhere", problem)


if __name__ == "__main__":
    unittest.main()
