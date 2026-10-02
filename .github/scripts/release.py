#!/usr/bin/env python3
"""Cut a Lens release, and read its notes back out of CHANGELOG.md. Used by .github/workflows/release.yml.

release.py cut patch|minor|major|X.Y.Z [--date YYYY-MM-DD]
    Moves the "## Unreleased" entries under a new version heading and sets that version in pyproject.toml,
    uv.lock, package.json and CloudronManifest.json (when present). Prints the new version.
release.py notes X.Y.Z
    Prints that version's CHANGELOG.md section without its heading (nothing when there's none).
"""

import argparse
import datetime
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CHANGELOG = ROOT / "CHANGELOG.md"
SEMVER = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")
HEADING = re.compile(r"^## (\S+)", re.M)

# file -> pattern whose first match holds the version (group 2), between groups 1 and 3
VERSION_FILES = {
    "fastapi_backend/pyproject.toml": r'(^\[project\][^\[]*?^version = ")([^"]+)(")',
    "fastapi_backend/uv.lock": r'(^name = "lens"\nversion = ")([^"]+)(")',
    "nextjs-frontend/package.json": r'(^  "version": ")([^"]+)(")',
    "CloudronManifest.json": r'(^  "version": ")([^"]+)(")',
}


def fail(message: str) -> None:
    print(f"::error::{message}", file=sys.stderr)
    sys.exit(1)


def current_version() -> str:
    text = (ROOT / "fastapi_backend/pyproject.toml").read_text()
    match = re.search(
        VERSION_FILES["fastapi_backend/pyproject.toml"], text, re.M | re.S
    )
    if not match:
        fail("No version in fastapi_backend/pyproject.toml")
    return match.group(2)


def next_version(current: str, bump: str) -> str:
    if SEMVER.match(bump):
        return bump
    match = SEMVER.match(current)
    if not match:
        fail(
            f"The current version {current!r} isn't X.Y.Z; give the new version instead of {bump!r}"
        )
    major, minor, patch = (int(part) for part in match.groups())
    if bump == "major":
        return f"{major + 1}.0.0"
    if bump == "minor":
        return f"{major}.{minor + 1}.0"
    if bump == "patch":
        return f"{major}.{minor}.{patch + 1}"
    fail(f"Unknown bump {bump!r}: use patch, minor, major or a version like 1.2.3")
    return ""


def sections(text: str) -> dict[str, str]:
    """Each "## " heading's first word -> the section's body, without the heading line."""
    found = {}
    matches = list(HEADING.finditer(text))
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        line_end = text.find("\n", match.start(), end)
        found[match.group(1)] = (
            text[line_end + 1 : end].strip("\n") if line_end != -1 else ""
        )
    return found


def cut(bump: str, date: datetime.date) -> str:
    old = current_version()
    new = next_version(old, bump)
    text = CHANGELOG.read_text()
    found = sections(text)
    if new in found:
        fail(f"CHANGELOG.md already has a {new} section")
    if not found.get("Unreleased", "").strip():
        fail("Nothing under '## Unreleased' in CHANGELOG.md to release")

    day = f"{date:%B} {date.day}, {date.year}"
    heading = f'## Unreleased\n\n## {new} <small>{day}</small> {{id="{new}"}}'
    CHANGELOG.write_text(
        re.sub(r"^## Unreleased[ \t]*$", lambda _: heading, text, count=1, flags=re.M)
    )

    for name, pattern in VERSION_FILES.items():
        path = ROOT / name
        if not path.exists():
            continue
        content, count = re.subn(
            pattern,
            lambda m: m.group(1) + new + m.group(3),
            path.read_text(),
            count=1,
            flags=re.M | re.S,
        )
        if not count:
            fail(f"No version found in {name}")
        path.write_text(content)
    return new


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    commands = parser.add_subparsers(dest="command", required=True)
    cut_parser = commands.add_parser("cut")
    cut_parser.add_argument("bump")
    cut_parser.add_argument(
        "--date", type=datetime.date.fromisoformat, default=datetime.date.today()
    )
    notes_parser = commands.add_parser("notes")
    notes_parser.add_argument("version")
    args = parser.parse_args()

    if args.command == "cut":
        print(cut(args.bump, args.date))
    else:
        print(sections(CHANGELOG.read_text()).get(args.version.removeprefix("v"), ""))


if __name__ == "__main__":
    main()
