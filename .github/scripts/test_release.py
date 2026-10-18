#!/usr/bin/env python3
"""Tests for release.py's changelog cut: python3 .github/scripts/test_release.py"""

from __future__ import annotations

import datetime
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import release  # noqa: E402

CHANGELOG = """# Changelog

## Unreleased

## 0.1.0 <small>October 1, 2026</small> {id="0.1.0"}

- Old.
"""
PYPROJECT = '[project]\nname = "lens"\nversion = "0.1.0"\n'


class Repo:
    """A throwaway git repository release.py works in."""

    def __init__(self, root: Path):
        self.root = root
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.email", "t@example.com")
        self.git("config", "user.name", "t")
        self.git("config", "commit.gpgsign", "false")
        (root / "fastapi_backend").mkdir()
        (root / "fastapi_backend/pyproject.toml").write_text(PYPROJECT)
        (root / "CHANGELOG.md").write_text(CHANGELOG)
        (root / "changelog.d").mkdir()
        (root / "changelog.d/README.md").write_text("# Fragments\n")
        self.commit("chore: start")
        self.git("tag", "v0.1.0")
        release.ROOT = root
        release.CHANGELOG = root / "CHANGELOG.md"
        release.FRAGMENTS = root / "changelog.d"

    def git(self, *args: str) -> str:
        env = {
            **os.environ,
            "GIT_AUTHOR_DATE": self.when(),
            "GIT_COMMITTER_DATE": self.when(),
        }
        return subprocess.run(
            ["git", *args],
            cwd=self.root,
            check=True,
            capture_output=True,
            text=True,
            env=env,
        ).stdout

    clock = 1_700_000_000

    def when(self) -> str:
        Repo.clock += 60  # each commit a minute later, so "oldest first" is testable
        return f"{Repo.clock} +0000"

    def commit(self, title: str, **files: str) -> None:
        for name, text in files.items():
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
        self.git("add", "-A")
        self.git("commit", "-q", "--allow-empty", "-m", title)

    def cut(self) -> str:
        release.cut("auto", datetime.date(2026, 10, 18))
        return (self.root / "CHANGELOG.md").read_text()


class CutTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Repo(Path(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def test_fragments_fold_in_commit_order_and_go(self):
        self.repo.commit(
            "feat(feeds): a Feeds page (#555)",
            **{"changelog.d/feeds.md": "**Feeds.** A page.\n"},
        )
        self.repo.commit("fix(chat): keep the cursor (#561)")
        self.repo.commit(
            "feat(voice): read aloud (#538)",
            **{"changelog.d/aloud.md": "**Aloud.** Kokoro.\n"},
        )
        text = self.repo.cut()
        section = release.sections(text)["0.2.0"]
        self.assertEqual(
            section.split("\n\n"),
            [
                "**Feeds.** A page.",
                "**Aloud.** Kokoro.",
                "### Fixes",
                "- **chat:** keep the cursor ([#561](https://github.com/adeelahmad/lense/pull/561))",
            ],
        )
        self.assertEqual(release.sections(text)["Unreleased"], "")
        self.assertEqual(
            sorted(p.name for p in (self.repo.root / "changelog.d").iterdir()),
            ["README.md"],
        )
        self.assertIn(
            'version = "0.2.0"',
            (self.repo.root / "fastapi_backend/pyproject.toml").read_text(),
        )

    def test_unreleased_text_comes_first_and_both_are_not_listed_again(self):
        self.repo.commit(
            "feat(a): one (#1)",
            **{
                "CHANGELOG.md": CHANGELOG.replace(
                    "## Unreleased\n", "## Unreleased\n\n**One.** Written in place.\n"
                )
            },
        )
        self.repo.commit(
            "feat(b): two (#2)", **{"changelog.d/two.md": "**Two.** A fragment.\n"}
        )
        section = release.sections(self.repo.cut())["0.2.0"]
        self.assertEqual(section, "**One.** Written in place.\n\n**Two.** A fragment.")

    def test_a_fragment_alone_is_a_release(self):
        self.repo.commit(
            "docs: explain (#3)", **{"changelog.d/explain.md": "**Explained.**\n"}
        )
        self.assertEqual(release.sections(self.repo.cut())["0.1.1"], "**Explained.**")

    def test_readme_and_dotfiles_stay(self):
        self.repo.commit("feat: thing (#4)", **{"changelog.d/.gitkeep": ""})
        text = self.repo.cut()
        self.assertIn("- thing ([#4]", release.sections(text)["0.2.0"])
        self.assertTrue((self.repo.root / "changelog.d/README.md").exists())
        self.assertTrue((self.repo.root / "changelog.d/.gitkeep").exists())


if __name__ == "__main__":
    unittest.main()
