import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest

from app.asr import StreamingASR

pytestmark = pytest.mark.skipif(shutil.which("say") is None, reason="needs macOS `say`")


@pytest.fixture(scope="module")
def asr() -> StreamingASR:
    return StreamingASR()


def _clip(tmp: Path, sr: int) -> np.ndarray:
    aiff = tmp / "anna.aiff"
    subprocess.run(
        ["say", "-v", "Samantha", "-o", str(aiff), "Hello, I am Anna and we will discuss Kubernetes today"],
        check=True,
    )
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(aiff), "-ac", "1", "-ar", str(sr), "-f", "f32le", "-"],
        check=True,
        capture_output=True,
    ).stdout
    pcm = np.frombuffer(raw, dtype=np.float32)
    return np.pad(pcm, (0, max(0, 6 * sr - pcm.size)))[: 6 * sr]


def _run(asr: StreamingASR, pcm: np.ndarray) -> list[dict]:
    block = asr.sample_rate // 10
    segs: list[dict] = []
    for i in range(0, pcm.size, block):
        segs += asr.feed(pcm[i : i + block])
    return segs + asr.flush()


def test_stream_transcribes_name_and_reset_restarts_clock(asr: StreamingASR, tmp_path: Path) -> None:
    pcm = _clip(tmp_path, asr.sample_rate)
    asr.reset()
    asr.set_context(["Anna", "Kubernetes"])
    segs = _run(asr, pcm)
    text = " ".join(s["text"] for s in segs)
    assert text.strip()
    assert "anna" in text.lower()
    assert all(isinstance(s["speaker"], int) and s["end"] >= s["start"] for s in segs)

    asr.reset()
    asr.set_context(["Anna", "Kubernetes"])
    again = _run(asr, pcm)
    assert again
    assert again[0]["start"] < 1.0
