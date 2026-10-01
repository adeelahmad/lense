"""A generated test video and a fake face engine (ported from the prototype's suite)."""

from __future__ import annotations

import shutil
import subprocess

import numpy as np
import pytest

needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg is not installed")
has_tesseract = bool(shutil.which("tesseract"))
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"


def make_video(path):
    """Three 3-second scenes (dark blue, yellow, dark blue) with a line of text each, over a tone."""

    def scene(c, t, fg):
        return f"color=c={c}:s=640x360:d=3,drawtext=fontfile={FONT}:text='{t}':fontcolor={fg}:fontsize=44:x=(w-text_w)/2:y=(h-text_h)/2"

    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y"]
    cmd += ["-f", "lavfi", "-i", scene("0x10204a", "INVOICE 2026", "white")]
    cmd += ["-f", "lavfi", "-i", scene("0xf2d64b", "PROJECT ATLAS", "black")]
    cmd += ["-f", "lavfi", "-i", scene("0x10204a", "THANK YOU", "white")]
    cmd += ["-f", "lavfi", "-i", "sine=frequency=440:duration=9"]
    cmd += ["-filter_complex", "[0:v][1:v][2:v]concat=n=3:v=1:a=0[v]", "-map", "[v]", "-map", "3:a"]
    cmd += ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(path)]
    for attempt in range(3):
        done = subprocess.run(cmd, capture_output=True)
        # ffmpeg itself has died by a signal (a heap corruption in lavfi's drawtext, seen on CI under a parallel
        # run): the command isn't at fault, so it is given another go; a plain failure is reported at once.
        if done.returncode >= 0 or attempt == 2:
            done.check_returncode()
            return


class FakeFaces:
    """One face per frame; the frame's background colour decides who it is."""

    name = "fake"

    def faces(self, path):
        from PIL import Image

        rgb = Image.open(path).convert("RGB").getpixel((2, 2))
        e = np.zeros(16)
        e[int(np.argmax(rgb))], e[5] = 1.0, 0.1
        return [{"box": [0.4, 0.3, 0.2, 0.3], "score": 0.98, "embedding": e / np.linalg.norm(e)}]


def sse(text):
    """Parse a text/event-stream body into {event name: [data, ...]}."""
    import json

    out = {}
    for block in text.strip().split("\n\n"):
        name = data = None
        for line in block.splitlines():
            if line.startswith("event: "):
                name = line[7:]
            elif line.startswith("data: "):
                data = json.loads(line[6:])
        if name:
            out.setdefault(name, []).append(data)
    return out
