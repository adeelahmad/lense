#!/usr/bin/env python3
"""Check that the README's requirements still point at checks that exist.

The README lists what Lens does (built: "What you can do with it", "Install" and "Light
by default"), what is in a pull request (In progress) and what comes next (Planned).
docs/requirements.md backs each built item with the checks that fail when it stops being
true. This script fails when the two drift apart:

- a built item links no section of docs/requirements.md, or one that isn't there, and a
  section there that no README item links;
- a row that names no check, or a check that is gone: a file, a test function or class
  in a Python file, a quoted name (test title, journey id) in a script, a job or step
  name in a workflow, a journey in a clicks baseline;
- a measured number (`journey` <= N clicks) that the clicks baseline no longer meets, or
  a journey that fails there;
- a row with no check yet that links no issue;
- an In progress item that links no pull request, or one the changelog lists as released;
- a Planned item that links nowhere.

Usage: python3 ci/check_requirements.py [repo_root]. Exits 1 and lists every problem.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

REPO = "https://github.com/adeelahmad/lense/"
REQUIREMENTS = "docs/requirements.md"
BUILT = ("What you can do with it", "Install", "Light by default")

# A link to a file in the repository, as docs pages write them (mkdocs needs full URLs)
FILE_LINK = r"\[[^\]]*\]\(" + re.escape(REPO) + r"(?:blob|tree)/main/([^)#\s]+)[^)]*\)"
# Each piece of a row's checks, in order: a file, a name in it, a measured number
PIECE = re.compile(rf"{FILE_LINK}|`([^`]+)`|(?:≤|<=)\s*(\d+)\s+(click|key|swipe)s?\b")
ISSUE = re.compile(re.escape(REPO) + r"issues/\d+")
PULL = re.compile(re.escape(REPO) + r"pull/(\d+)")
SECTION_LINK = re.compile(re.escape(REQUIREMENTS) + r"#([a-z0-9-]+)")


def slug(title: str) -> str:
    """The anchor GitHub and mkdocs give a heading."""
    return re.sub(r"[\s]+", "-", re.sub(r"[^\w\s-]", "", title.strip().lower()))


def sections(text: str) -> dict[str, str]:
    """Each level-two heading's title and the text under it."""
    found: dict[str, str] = {}
    title = None
    for line in text.splitlines():
        if line.startswith("## "):
            title = line[3:].strip()
            found[title] = ""
        elif title is not None:
            found[title] += line + "\n"
    return found


def bullets(text: str) -> list[str]:
    """The top-level list items of a section, each with its continuation lines."""
    items: list[str] = []
    for line in text.splitlines():
        if line.startswith("- "):
            items.append(line[2:])
        elif items and line.startswith("  "):
            items[-1] += " " + line.strip()
        elif line.strip() and items:
            break
    return items


def rows(text: str) -> list[tuple[str, str]]:
    """Each table row's first cell (the requirement) and last cell (its checks)."""
    found = []
    for line in text.splitlines():
        if not line.startswith("|") or re.match(r"^\|[\s:-]+\|", line):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split(" | ")]
        if cells[0] in ("Requirement", "") or len(cells) < 2:
            continue
        found.append((cells[0], cells[-1]))
    return found


def has_name(path: Path, name: str) -> bool:
    """Whether the file still holds the named test, journey, job or step."""
    text = path.read_text(encoding="utf-8")
    word = re.escape(name)
    if path.suffix == ".py":
        return bool(
            re.search(rf"^\s*(?:async\s+)?(?:def|class)\s+{word}\b", text, re.M)
        )
    if path.suffix in (".yml", ".yaml"):
        step = rf"^\s*(?:-\s+)?name:\s*[\"']?{word}[\"']?\s*$"
        return bool(
            re.search(step, text, re.M) or re.search(rf"^  {word}:\s*$", text, re.M)
        )
    if path.suffix == ".json":
        return any(item.get("id") == name for item in journeys(path))
    if path.suffix in (".js", ".mjs", ".ts", ".tsx", ".jsx"):
        return any(f"{quote}{name}{quote}" in text for quote in "\"'`")
    return name in text


def journeys(path: Path) -> list[dict]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return []
    return data.get("journeys", []) if isinstance(data, dict) else []


def measured(path: Path, name: str, most: int, unit: str) -> str | None:
    """Why a journey in a clicks baseline doesn't meet its number, or None when it does."""
    journey = next((item for item in journeys(path) if item.get("id") == name), None)
    if journey is None:
        return f"`{name}` is not a journey in {path.name}"
    if not journey.get("ok", False):
        return f"`{name}` fails in {path.name}"
    if journey.get(unit, 0) > most:
        return f"`{name}` takes {journey.get(unit)} {unit} in {path.name}, not ≤ {most}"
    return None


def check_row(root: Path, where: str, checks: str) -> list[str]:
    problems = []
    if "no check yet" in checks.lower():
        if not ISSUE.search(checks):
            problems.append(f"{where}: 'no check yet' links no issue")
        return problems
    path: Path | None = None
    name: str | None = None
    for piece in PIECE.finditer(checks):
        link, token, most, unit = piece.groups()
        if link:
            path, name = root / link, None
            if not path.exists():
                problems.append(f"{where}: {link} is gone")
                path = None
        elif token and path is not None and path.is_file():
            name = token
            if not has_name(path, token):
                problems.append(
                    f"{where}: `{token}` is gone from {path.relative_to(root)}"
                )
        elif most:
            if path is None or path.suffix != ".json" or name is None:
                problems.append(
                    f"{where}: ≤ {most} {unit}s names no journey in a baseline"
                )
            elif why := measured(path, name, int(most), unit + "s"):
                problems.append(f"{where}: {why}")
    if not re.search(FILE_LINK, checks):
        problems.append(f"{where}: names no check (a file in the repository)")
    return list(dict.fromkeys(problems))  # a failing journey, once


def check(root: Path) -> list[str]:
    problems: list[str] = []
    readme = sections((root / "README.md").read_text(encoding="utf-8"))
    reqs_path = root / REQUIREMENTS
    if not reqs_path.exists():
        return [f"{REQUIREMENTS} is missing"]
    reqs = sections(reqs_path.read_text(encoding="utf-8"))
    backed = {slug(title): (title, text) for title, text in reqs.items() if rows(text)}

    linked: set[str] = set()
    for title in BUILT:
        if title not in readme:
            problems.append(f"README: no '## {title}' section")
            continue
        items = bullets(readme[title]) if title == BUILT[0] else [readme[title]]
        for item in items:
            label = re.match(r"\*\*([^*]+)\*\*", item)
            where = f"README › {title}" + (f" › {label.group(1)}" if label else "")
            anchors = SECTION_LINK.findall(item)
            if not anchors:
                problems.append(f"{where}: links no section of {REQUIREMENTS}")
            for anchor in anchors:
                linked.add(anchor)
                if anchor not in backed:
                    problems.append(f"{where}: {REQUIREMENTS}#{anchor} has no checks")

    for anchor, (title, text) in backed.items():
        if anchor not in linked:
            problems.append(
                f"{REQUIREMENTS} › {title}: no built item in the README links it"
            )
        for requirement, checks in rows(text):
            where = f"{REQUIREMENTS} › {title} › {requirement[:60]}"
            problems += check_row(root, where, checks)

    changelog = (root / "CHANGELOG.md").read_text(encoding="utf-8")
    for item in bullets(readme.get("In progress", "")):
        pulls = PULL.findall(item)
        if not pulls:
            problems.append(f"README › In progress: links no pull request: {item[:60]}")
        for number in pulls:
            if f"/pull/{number})" in changelog:
                problems.append(
                    f"README › In progress: #{number} is released (CHANGELOG.md); "
                    "list it as built, with its checks"
                )
    if "Planned" not in readme:
        problems.append("README: no '## Planned' section")
    for item in bullets(readme.get("Planned", "")):
        if "](" not in item:
            problems.append(f"README › Planned: links nowhere: {item[:60]}")
    return problems


def main() -> int:
    root = Path(
        sys.argv[1] if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent
    )
    problems = check(root)
    for problem in problems:
        print(problem)
    if problems:
        print(
            f"\n{len(problems)} problem(s): see {REQUIREMENTS}, 'How the checks are kept'"
        )
        return 1
    print("Every built requirement names checks that exist.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
