"""Bound decoding before invoking a model, even for tiny compressed long recordings."""

import asyncio
import subprocess
import threading
from types import SimpleNamespace

import pytest
from starlette.concurrency import run_in_threadpool

from app.domain import ingest, voice


def test_decoder_bounds_duration_and_wall_time(monkeypatch):
    def run(args, **kwargs):
        assert args[args.index("-t") + 1] == "301"
        assert args[args.index("-protocol_whitelist") + 1] == "file,pipe"
        assert kwargs["timeout"] == 30
        return SimpleNamespace(returncode=0, stdout=b"\0" * (301 * ingest.SR * 4))

    monkeypatch.setattr(voice.subprocess, "run", run)
    with pytest.raises(voice.ClipTooLong):
        voice.decode_clip("upload.clip")


def test_decoder_timeout_is_safe_error(monkeypatch):
    def run(*args, **kwargs):
        raise subprocess.TimeoutExpired("ffmpeg", 30)

    monkeypatch.setattr(voice.subprocess, "run", run)
    with pytest.raises(voice.InvalidClip, match="timed out"):
        voice.decode_clip("upload.clip")


def test_invalid_clip_and_missing_decoder(monkeypatch):
    monkeypatch.setattr(voice.subprocess, "run", lambda *a, **kw: SimpleNamespace(returncode=1, stdout=b"", stderr=b"secret"))
    with pytest.raises(voice.InvalidClip, match="could not decode"):
        voice.decode_clip("upload.clip")

    def missing(*args, **kwargs):
        raise FileNotFoundError("ffmpeg")

    monkeypatch.setattr(voice.subprocess, "run", missing)
    with pytest.raises(RuntimeError, match="unavailable"):
        voice.decode_clip("upload.clip")


def test_failed_admission_releases_slot():
    voice._PUBLIC_JOBS.clear()
    try:
        for _ in range(voice.PUBLIC_PER_MINUTE):
            with voice.admission(anonymous=True):
                pass
        with pytest.raises(voice.Busy), voice.admission(anonymous=True):
            pass
        with voice.admission(), voice.admission():
            with pytest.raises(voice.Busy), voice.admission():
                pass
    finally:
        voice._PUBLIC_JOBS.clear()


def test_cancelled_waiters_do_not_release_running_worker_slots():
    async def scenario():
        started = [threading.Event(), threading.Event()]
        release = threading.Event()
        finished = [threading.Event(), threading.Event()]

        def work(index):
            started[index].set()
            assert release.wait(5), "test did not release provider work"

        def admitted_work(index):
            try:
                voice.run_admitted(work, index)
            finally:
                finished[index].set()

        tasks = [asyncio.create_task(run_in_threadpool(admitted_work, index)) for index in range(2)]
        try:
            for _ in range(100):
                if all(event.is_set() for event in started):
                    break
                await asyncio.sleep(0.01)
            assert all(event.is_set() for event in started)
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            with pytest.raises(voice.Busy), voice.admission():
                pass
        finally:
            release.set()
            for _ in range(100):
                if all(event.is_set() for event in finished):
                    break
                await asyncio.sleep(0.01)
            await asyncio.gather(*tasks, return_exceptions=True)
        assert all(event.is_set() for event in finished)
        with voice.admission(), voice.admission():
            pass

    asyncio.run(scenario())
