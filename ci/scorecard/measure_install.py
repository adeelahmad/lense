#!/usr/bin/env python3
"""Measure an install for the scorecard: how long it took to answer, and the RAM and disk Lens takes when idle.

The Install workflow's arm64 job runs it after the one-line install, once Lens has had a while to settle, and keeps
the JSON it writes as the install-scorecard artifact, which ci/scorecard/scorecard.py reads from main.

Usage: measure_install.py --started EPOCH --answered EPOCH --dir LENS_DIR [--out install-scorecard.json]

- install_to_first_answer_s: from the install line starting to the setup page answering.
- idle_ram_mb: the memory the install's containers use (docker stats), added up.
- disk_mb: Docker's images, containers and volumes (docker system df), plus the install folder. The build cache is
  kept apart (build_cache_mb), as Docker can drop it.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import subprocess
import sys
from pathlib import Path

# docker stats prints binary units (MiB); docker system df decimal ones (MB)
UNITS = {
    "b": 1,
    "kb": 1e3,
    "mb": 1e6,
    "gb": 1e9,
    "tb": 1e12,
    "kib": 1024,
    "mib": 1024**2,
    "gib": 1024**3,
    "tib": 1024**4,
}


def to_mb(size: str) -> float:
    """'1.5GiB', '512MB' or '0B' in megabytes (10^6 bytes)."""
    m = re.fullmatch(r"\s*([\d.]+)\s*([a-zA-Z]+)\s*", size)
    if not m or m.group(2).lower() not in UNITS:
        raise ValueError(f"not a size: {size!r}")
    return float(m.group(1)) * UNITS[m.group(2).lower()] / 1e6


def ram_mb(stats: list[dict]) -> dict[str, float]:
    """Each container's memory from docker stats' JSON lines (MemUsage is 'used / limit')."""
    return {s["Name"]: round(to_mb(s["MemUsage"].split("/")[0]), 1) for s in stats}


def disk_mb(df: list[dict]) -> dict[str, float]:
    """Docker's disk by type, from docker system df's JSON lines."""
    return {d["Type"]: round(to_mb(d["Size"]), 1) for d in df}


def docker_json(*args: str) -> list[dict]:
    out = subprocess.run(
        ["docker", *args, "--format", "{{json .}}"],
        capture_output=True,
        text=True,
        check=True,
    )
    return [json.loads(line) for line in out.stdout.splitlines() if line.strip()]


def folder_mb(path: Path) -> float:
    total = 0
    for root, _, files in os.walk(path):
        for name in files:
            p = os.path.join(root, name)
            if not os.path.islink(p):
                total += os.lstat(p).st_size
    return round(total / 1e6, 1)


def memory_gb() -> float | None:
    try:
        kb = re.search(
            r"^MemTotal:\s+(\d+) kB", Path("/proc/meminfo").read_text(), re.MULTILINE
        )
    except OSError:
        return None
    return round(int(kb.group(1)) / 1024**2, 1) if kb else None


def target(lens_dir: Path) -> str | None:
    try:
        env = (lens_dir / ".env").read_text()
    except OSError:
        return None
    m = re.search(r"^LENS_TARGET=(\S+)$", env, re.MULTILINE)
    return m.group(1) if m else None


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument(
        "--started",
        type=int,
        required=True,
        help="when the install line started (seconds since 1970)",
    )
    p.add_argument(
        "--answered", type=int, required=True, help="when the setup page first answered"
    )
    p.add_argument(
        "--dir", type=Path, required=True, help="the install folder (LENS_DIR)"
    )
    p.add_argument("--out", type=Path, default=Path("install-scorecard.json"))
    args = p.parse_args(argv)

    ids = subprocess.run(
        ["docker", "compose", "-f", "docker-compose.prod.yml", "ps", "-q"],
        cwd=args.dir,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    containers = ram_mb(docker_json("stats", "--no-stream", *ids)) if ids else {}
    docker = disk_mb(docker_json("system", "df"))
    folder = folder_mb(args.dir)
    result = {
        "install_to_first_answer_s": args.answered - args.started,
        "idle_ram_mb": round(sum(containers.values()), 1),
        "disk_mb": round(
            sum(docker.get(k, 0) for k in ("Images", "Containers", "Local Volumes"))
            + folder,
            1,
        ),
        "build_cache_mb": docker.get("Build Cache", 0),
        "containers_mb": containers,
        "docker_disk_mb": docker,
        "install_folder_mb": folder,
        "target": target(args.dir),
        "machine": os.environ.get("LENS_MACHINE") or platform.node(),
        "arch": platform.machine(),
        "cores": os.cpu_count(),
        "memory_gb": memory_gb(),
        "commit": os.environ.get("GITHUB_SHA"),
    }
    args.out.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
