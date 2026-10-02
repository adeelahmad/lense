#!/usr/bin/env python3
"""Lens releases from Conventional Commits. Used by .github/workflows/release.yml, pr-title.yml and the commit-msg
pre-commit hook; needs only Python and git.

    release.py cut auto|patch|minor|major|X.Y.Z [--date YYYY-MM-DD]
        Writes the new version's CHANGELOG.md section: the "## Unreleased" text, when there is any, then the changes
        merged since the last release, listed from their Conventional Commit titles. Sets the version in
        pyproject.toml, uv.lock, package.json and CloudronManifest.json (when present) and prints it. `auto` picks the
        bump from those titles: a breaking change is major (minor before 1.0), a feat minor, anything else patch.
    release.py notes X.Y.Z
        Prints that version's CHANGELOG.md section without its heading (nothing when there's none).
    release.py check-title TITLE
        Fails unless TITLE is a Conventional Commit title, the way PR titles become the changelog.
    release.py check-message FILE
        The same for a commit message file (commit-msg hook); merge, revert, fixup and squash messages pass too.
"""

import argparse
import datetime
import os
import re
import subprocess
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

TYPES = {
    "feat": "a feature",
    "fix": "a bug fix",
    "perf": "a speed-up",
    "revert": "undoes an earlier change",
    "docs": "documentation only",
    "refactor": "no behaviour change",
    "test": "tests only",
    "build": "build, packaging or dependencies",
    "ci": "CI workflows",
    "chore": "anything else that users don't see",
    "style": "formatting only",
}
CONVENTIONAL = re.compile(
    r"^(?P<type>[a-z]+)(?:\((?P<scope>[\w./-]+)\))?(?P<breaking>!)?: (?P<description>\S.*)$"
)
DEPENDABOT = re.compile(r"^Bump \S+ from \S+ to \S+")
# messages git and GitHub write themselves
GENERATED = re.compile(r'^(Merge |Revert "|fixup! |squash! |amend! )')

# changelog sections in order; other types are left out
SECTIONS = {
    "breaking": "Breaking changes",
    "feat": "Features",
    "fix": "Fixes",
    "perf": "Performance",
    "revert": "Reverts",
    "deps": "Dependencies",
    "other": "Other changes",
}


def fail(message: str) -> None:
    print(f"::error::{message}", file=sys.stderr)
    sys.exit(1)


def git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=ROOT, check=True, capture_output=True, text=True
    ).stdout


def title_problem(title: str) -> str | None:
    """Why TITLE isn't a Conventional Commit title, or None when it is."""
    if DEPENDABOT.match(title):
        return None
    match = CONVENTIONAL.match(title)
    if not match:
        return "it doesn't start with type: or type(scope):"
    if match["type"] not in TYPES:
        return f"{match['type']!r} isn't one of the types"
    return None


def explain(title: str, problem: str) -> str:
    types = "\n".join(f"  {name:9} {meaning}" for name, meaning in TYPES.items())
    return (
        f"{title!r} isn't a Conventional Commit title: {problem}.\n"
        "Write it as `type(optional scope): what changed`, with `!` before the colon for a breaking change, "
        "e.g. `feat(chat): answer from the selected recordings` or `fix!: drop the v1 API`.\n"
        f"Types:\n{types}"
    )


# -- versions --------------------------------------------------------------------------------------------------------


def current_version() -> str:
    text = (ROOT / "fastapi_backend/pyproject.toml").read_text()
    match = re.search(
        VERSION_FILES["fastapi_backend/pyproject.toml"], text, re.M | re.S
    )
    if not match:
        fail("No version in fastapi_backend/pyproject.toml")
    return match.group(2)


def _key(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version.split("."))


def next_version(current: str, bump: str) -> str:
    if SEMVER.match(bump):
        if SEMVER.match(current) and _key(bump) <= _key(current):
            fail(f"{bump} isn't newer than the current version {current}")
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
    fail(
        f"Unknown bump {bump!r}: use auto, patch, minor, major or a version like 1.2.3"
    )
    return ""


def auto_bump(current: str, changes: list[dict]) -> str:
    if any(change["breaking"] for change in changes):
        return "minor" if current.startswith("0.") else "major"
    if any(change["type"] == "feat" for change in changes):
        return "minor"
    return "patch"


# -- changes since the last release ----------------------------------------------------------------------------------


def last_release(current: str) -> str | None:
    """The last vX.Y.Z tag, else the oldest commit on the branch that already had the current version."""
    try:
        return git(
            "describe", "--tags", "--abbrev=0", "--match", "v[0-9]*", "HEAD"
        ).strip()
    except subprocess.CalledProcessError:
        pass
    base = None
    for commit in git("log", "--first-parent", "--format=%H", "HEAD").split():
        try:
            text = git("show", f"{commit}:fastapi_backend/pyproject.toml")
        except subprocess.CalledProcessError:
            break
        match = re.search(
            VERSION_FILES["fastapi_backend/pyproject.toml"], text, re.M | re.S
        )
        if not match or match.group(2) != current:
            break
        base = commit
    return base


def changes_since(base: str | None) -> list[dict]:
    """One entry per pull request (by its title, which GitHub puts in the merge commit) or direct commit on the
    branch since BASE."""
    log = git(
        "log",
        "--first-parent",
        "--format=%s%x1f%b%x1e",
        f"{base}..HEAD" if base else "HEAD",
    )
    changes = []
    for record in log.split("\x1e"):
        if not record.strip():
            continue
        subject, _, body = record.strip("\n").partition("\x1f")
        pr = None
        merged = re.match(r"^Merge pull request #(\d+) ", subject)
        if merged:
            pr = merged.group(1)
            subject = next(
                (line for line in body.splitlines() if line.strip()), subject
            )
        elif subject.startswith("Merge ") or subject.startswith("chore(release): "):
            continue
        else:
            squashed = re.search(r" \(#(\d+)\)$", subject)
            if squashed:
                pr, subject = squashed.group(1), subject[: squashed.start()]
        subject = subject.strip()
        match = CONVENTIONAL.match(subject)
        if match and match["type"] in TYPES:
            kind, scope, description = (
                match["type"],
                match["scope"],
                match["description"],
            )
            breaking = bool(match["breaking"]) or "BREAKING CHANGE:" in body
            if (
                kind == "build" and scope == "deps"
            ):  # Dependabot (.github/dependabot.yml)
                kind = "deps"
        else:
            kind = "deps" if DEPENDABOT.match(subject) else "other"
            scope, description, breaking = None, subject, False
        changes.append(
            {
                "type": kind,
                "scope": scope,
                "description": description,
                "breaking": breaking,
                "pr": pr,
            }
        )
    return list(reversed(changes))


def changes_markdown(changes: list[dict]) -> str:
    repo = os.environ.get("GITHUB_REPOSITORY", "adeelahmad/lense")
    server = os.environ.get("GITHUB_SERVER_URL", "https://github.com")
    grouped: dict[str, list[str]] = {}
    for change in changes:
        section = "breaking" if change["breaking"] else change["type"]
        if section not in SECTIONS:
            continue
        line = change["description"]
        if change["scope"] and change["type"] != "deps":
            line = f"**{change['scope']}:** {line}"
        if change["pr"]:
            line += f" ([#{change['pr']}]({server}/{repo}/pull/{change['pr']}))"
        grouped.setdefault(section, []).append(f"- {line}")
    return "\n\n".join(
        f"### {title}\n\n" + "\n".join(grouped[key])
        for key, title in SECTIONS.items()
        if key in grouped
    )


# -- CHANGELOG.md ----------------------------------------------------------------------------------------------------


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
    changes = changes_since(last_release(old))
    text = CHANGELOG.read_text()
    unreleased = sections(text).get("Unreleased", "").strip()
    if not changes and not unreleased:
        fail("Nothing to release: no changes since the last release")
    new = next_version(old, auto_bump(old, changes) if bump == "auto" else bump)
    if new in sections(text):
        fail(f"CHANGELOG.md already has a {new} section")

    listed = changes_markdown(changes)
    body = "\n\n".join(part for part in (unreleased, listed) if part) or (
        "Maintenance only: nothing users see changed."
    )
    day = f"{date:%B} {date.day}, {date.year}"
    section = (
        f'## Unreleased\n\n## {new} <small>{day}</small> {{id="{new}"}}\n\n{body}\n\n'
    )
    # the Unreleased text moves into the new section
    text = re.sub(
        r"^## Unreleased[ \t]*\n.*?(?=^## |\Z)",
        lambda _: section,
        text,
        count=1,
        flags=re.M | re.S,
    )
    CHANGELOG.write_text(text.rstrip("\n") + "\n")

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
    commands.add_parser("notes").add_argument("version")
    commands.add_parser("check-title").add_argument("title")
    commands.add_parser("check-message").add_argument("file")
    args = parser.parse_args()

    if args.command == "cut":
        print(cut(args.bump, args.date))
    elif args.command == "notes":
        print(sections(CHANGELOG.read_text()).get(args.version.removeprefix("v"), ""))
    else:
        if args.command == "check-title":
            title = args.title.strip()
        else:
            lines = Path(args.file).read_text().splitlines()
            title = next(
                (line for line in lines if line.strip() and not line.startswith("#")),
                "",
            ).strip()
            if GENERATED.match(title):
                return
        problem = title_problem(title)
        if problem:
            print(explain(title, problem), file=sys.stderr)
            sys.exit(1)


if __name__ == "__main__":
    main()
