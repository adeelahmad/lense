"""What this machine has to run models with: processors, memory, a GPU, disk. Read once per process (the disk each
time), to choose the engines and model sizes that suit it (components.py)."""

from __future__ import annotations

import functools
import os
import platform
import shutil
import subprocess
import threading
import time


def _memory_gb():
    try:
        with open("/proc/meminfo", encoding="ascii") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    total = int(line.split()[1]) / 1024 / 1024
                    break
            else:
                total = None
    except OSError:
        total = None
    if total is None:
        try:
            total = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 1024**3
        except (ValueError, OSError, AttributeError):
            return None
    # a container's memory limit, when it has one smaller than the machine's
    for path in ("/sys/fs/cgroup/memory.max", "/sys/fs/cgroup/memory/memory.limit_in_bytes"):
        try:
            raw = open(path, encoding="ascii").read().strip()
        except OSError:
            continue
        if raw.isdigit() and int(raw) < total * 1024**3:
            total = int(raw) / 1024**3
        break
    return round(total, 1)


def _nvidia():
    """The NVIDIA GPUs a CUDA build could use: [{name, memory_gb}]."""
    exe = shutil.which("nvidia-smi")
    if exe:
        try:
            out = subprocess.run(
                [exe, "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=5
            ).stdout
            gpus = []
            for line in out.strip().splitlines():
                name, _, mem = line.rpartition(",")
                gpus.append({"name": name.strip(), "memory_gb": round(int(mem.strip()) / 1024, 1) if mem.strip().isdigit() else None})
            if gpus:
                return gpus
        except (OSError, subprocess.SubprocessError):
            pass
    if os.path.isdir("/proc/driver/nvidia/gpus"):
        return [{"name": "NVIDIA GPU", "memory_gb": None} for _ in os.listdir("/proc/driver/nvidia/gpus")]
    try:
        import ctranslate2  # with faster-whisper: sees CUDA when the container was given the GPU

        n = ctranslate2.get_cuda_device_count()
        return [{"name": "NVIDIA GPU", "memory_gb": None} for _ in range(n)]
    except Exception:  # noqa: BLE001 - not installed, or no CUDA
        return []


@functools.lru_cache(maxsize=1)
def _fixed():
    system, arch = platform.system().lower(), platform.machine().lower()
    return {
        "os": system,
        "arch": "arm64" if arch in ("arm64", "aarch64") else arch,
        "cpus": os.cpu_count() or 1,
        "memory_gb": _memory_gb(),
        "gpus": _nvidia(),
        "apple_silicon": system == "darwin" and arch == "arm64",
        "container": os.path.exists("/.dockerenv") or os.path.exists("/run/.containerenv"),
        "python": f"{platform.python_version_tuple()[0]}.{platform.python_version_tuple()[1]}",
    }


def probe(data_dir=None):
    """The machine, with the free disk where models go."""
    out = dict(_fixed())
    try:
        out["disk_free_gb"] = round(shutil.disk_usage(data_dir or ".").free / 1024**3, 1)
    except OSError:
        out["disk_free_gb"] = None
    out["cuda"] = bool(out["gpus"])
    return out


# ---------- right now, for Admin → System health (history lives in Prometheus and Grafana: docs/prometheus.md) ----------
STARTED = time.time()  # this process's start, for its uptime
_cpu = {"last": None}
_cpu_lock = threading.Lock()


def _cpu_times():
    try:
        with open("/proc/stat", encoding="ascii") as f:
            parts = [int(x) for x in f.readline().split()[1:]]
    except (OSError, ValueError):
        return None
    return sum(parts), parts[3] + (parts[4] if len(parts) > 4 else 0)  # all, idle + iowait


def cpu_pct():
    """Busy share of all CPUs since the last call (over a moment the first time), 0-100; None without /proc/stat."""
    now = _cpu_times()
    if now is None:
        return None
    with _cpu_lock:
        last, _cpu["last"] = _cpu["last"], now
    if last is None:
        time.sleep(0.2)
        last, now = now, _cpu_times() or now
        with _cpu_lock:
            _cpu["last"] = now
    total, idle = now[0] - last[0], now[1] - last[1]
    return round(100.0 * (total - idle) / total, 1) if total > 0 else 0.0


def _meminfo():
    info = {}
    try:
        with open("/proc/meminfo", encoding="ascii") as f:
            for line in f:
                k, _, v = line.partition(":")
                info[k] = int(v.split()[0]) * 1024
    except (OSError, ValueError, IndexError):
        return None, None
    total, avail = info.get("MemTotal"), info.get("MemAvailable", info.get("MemFree"))
    return (total - avail, total) if total and avail is not None else (None, None)


def _first_number(path, prefix=None, scale=1):
    try:
        with open(path, encoding="ascii") as f:
            for line in f:
                if prefix is None or line.startswith(prefix):
                    return int(float(line.split()[1 if prefix else 0]) * scale)
    except (OSError, ValueError, IndexError):
        pass
    return None


def live():
    """The machine and this process now: CPU, memory, load, the API's memory and uptimes. Fields a system doesn't
    report (no /proc outside Linux and Docker) are None."""
    used, total = _meminfo()
    try:
        load = [round(x, 2) for x in os.getloadavg()]
    except (AttributeError, OSError):
        load = [None, None, None]
    return {
        "cpu_pct": cpu_pct(),
        "cpus": os.cpu_count(),
        "mem_used": used,
        "mem_total": total,
        "load1": load[0],
        "load5": load[1],
        "load15": load[2],
        "rss": _first_number("/proc/self/status", "VmRSS:", 1024),
        "uptime": int(time.time() - STARTED),
        "host_uptime": _first_number("/proc/uptime"),
    }
