#!/usr/bin/env python3
"""Check that threat-model.md, threat-model.yaml and threat-model.json agree.

The prose (threat-model.md) is canonical; the YAML and JSON are derived from it. This script is the repo's own
stand-in for the threat-model plugin's validator, which lives outside the repo and so can't run in CI. It checks:

- the YAML's prose_version is the sha256 of threat-model.md;
- both companions parse and carry their required top-level keys;
- the YAML and JSON name the same repository, commit and date;
- the claimed (P-), disclaimed (N-) and known-non-finding (KNF-) ids are the same set in all three files, and the
  matrix-derived (MX-) ids are the same in both companions;
- every P-, N-, KNF- or MX- id mentioned anywhere is one of those defined ids;
- the open questions are numbered Q1..Qn in the JSON, and every Qn cited anywhere is one of them.

Usage: python ci/validate_threat_model.py [repo_root]. Exits 1 and lists every problem when anything disagrees.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

import yaml

ID_RE = re.compile(
    r"(?<![A-Za-z0-9_-])(P|N|KNF|MX)-[A-Z][A-Z0-9]*(?:-[A-Z0-9]+)*(?![A-Za-z0-9_-])"
)
Q_RE = re.compile(r"(?<![A-Za-z0-9_-])Q(\d+)(?![0-9])")
ROW_RE = re.compile(r"^\|\s*((?:P|N|KNF)-[A-Z0-9-]+)\s*\|", re.MULTILINE)

YAML_KEYS = (
    "schema prose_version components entry_points adversaries properties_claimed properties_disclaimed "
    "downstream_responsibilities known_misuses known_non_findings dispositions"
).split()
JSON_KEYS = (
    "spec_version repository commit date components entry_points adversaries properties_provided "
    "properties_not_provided downstream_responsibilities known_misuse known_non_findings dispositions open_questions"
).split()


def by_kind(ids: set[str]) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {"P": set(), "N": set(), "KNF": set(), "MX": set()}
    for i in ids:
        out[i.split("-", 1)[0]].add(i)
    return out


def validate(root: Path) -> list[str]:
    errors: list[str] = []
    prose_raw = (root / "threat-model.md").read_bytes()
    prose = prose_raw.decode()
    yaml_text = (root / "threat-model.yaml").read_text()
    json_text = (root / "threat-model.json").read_text()
    try:
        y = yaml.safe_load(yaml_text)
    except yaml.YAMLError as e:
        return [f"threat-model.yaml does not parse: {e}"]
    try:
        j = json.loads(json_text)
    except json.JSONDecodeError as e:
        return [f"threat-model.json does not parse: {e}"]

    # Prose hash.
    want = "threat-model.md@sha256:" + hashlib.sha256(prose_raw).hexdigest()
    if y.get("prose_version") != want:
        errors.append(
            f"threat-model.yaml prose_version is {y.get('prose_version')!r}; the prose hashes to {want!r}"
        )

    # Shape.
    if y.get("schema") != "threat-model-sidecar/v2":
        errors.append(
            f"threat-model.yaml schema is {y.get('schema')!r}, expected 'threat-model-sidecar/v2'"
        )
    if j.get("spec_version") != 1:
        errors.append(
            f"threat-model.json spec_version is {j.get('spec_version')!r}, expected 1"
        )
    errors += [f"threat-model.yaml is missing {k}" for k in YAML_KEYS if k not in y]
    errors += [f"threat-model.json is missing {k}" for k in JSON_KEYS if k not in j]
    if errors:
        return errors

    # Same snapshot.
    for jk, yk in (
        ("repository", "x-repository"),
        ("commit", "x-modeled-commit"),
        ("date", "x-model-date"),
    ):
        if str(j[jk]) != str(y.get(yk)):
            errors.append(
                f"threat-model.json {jk} {j[jk]!r} differs from threat-model.yaml {yk} {y.get(yk)!r}"
            )

    # Defined ids, per file.
    defined = {
        "threat-model.md": by_kind(set(ROW_RE.findall(prose))),
        "threat-model.yaml": by_kind(
            {p["id"] for p in y["properties_claimed"]}
            | {p["id"] for p in y["properties_disclaimed"]}
            | {k["id"] for k in y["known_non_findings"]}
        ),
        "threat-model.json": by_kind(
            {p["property"] for p in j["properties_provided"]}
            | {p["property"] for p in j["properties_not_provided"]}
        ),
    }
    canon = defined["threat-model.md"]
    for name, ids in defined.items():
        for kind in ("P", "N", "KNF"):
            if name == "threat-model.json" and kind == "KNF":
                continue  # JSON known_non_findings carry no ids
            for missing in sorted(canon[kind] - ids[kind]):
                errors.append(
                    f"{missing} is defined in threat-model.md but not in {name}"
                )
            for extra in sorted(ids[kind] - canon[kind]):
                errors.append(
                    f"{extra} is defined in {name} but not in threat-model.md"
                )
    # MX- records come from the §1.7 contract-dimension matrix, so only the companions define them.
    if defined["threat-model.yaml"]["MX"] != defined["threat-model.json"]["MX"]:
        errors.append(
            f"MX- ids differ: threat-model.yaml has {sorted(defined['threat-model.yaml']['MX'])}, "
            f"threat-model.json has {sorted(defined['threat-model.json']['MX'])}"
        )
    if len(j["known_non_findings"]) != len(canon["KNF"]):
        errors.append(
            f"threat-model.json has {len(j['known_non_findings'])} known_non_findings; "
            f"threat-model.md defines {len(canon['KNF'])}"
        )

    # Every id mentioned is defined.
    all_defined = set().union(*canon.values(), defined["threat-model.yaml"]["MX"])
    for name, text in (
        ("threat-model.md", prose),
        ("threat-model.yaml", yaml_text),
        ("threat-model.json", json_text),
    ):
        for undefined in sorted(
            {m.group(0) for m in ID_RE.finditer(text)} - all_defined
        ):
            line = text[: text.index(undefined)].count("\n") + 1
            errors.append(
                f"{name}:{line} mentions {undefined}, which no table in threat-model.md defines"
            )

    # Open questions.
    claims = [q.get("claim", "") for q in j["open_questions"]]
    for n, claim in enumerate(claims, 1):
        if not claim.startswith(f"Q{n}:"):
            errors.append(
                f"threat-model.json open_questions[{n - 1}] should start with 'Q{n}:'"
            )
    count = len(claims)
    for name, text in (
        ("threat-model.md", prose),
        ("threat-model.yaml", yaml_text),
        ("threat-model.json", json_text),
    ):
        for m in Q_RE.finditer(text):
            if not 1 <= int(m.group(1)) <= count:
                line = text[: m.start()].count("\n") + 1
                errors.append(
                    f"{name}:{line} cites {m.group(0)}; there are {count} open questions"
                )
    return errors


def main() -> int:
    root = Path(
        sys.argv[1] if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent
    )
    errors = validate(root)
    for e in errors:
        print(f"error: {e}")
    if errors:
        print(
            f"\n{len(errors)} problem(s). The prose is canonical: fix the YAML and JSON to match it."
        )
        return 1
    print("threat model: prose, YAML and JSON agree")
    return 0


if __name__ == "__main__":
    sys.exit(main())
