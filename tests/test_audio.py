import asyncio
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.audio import SAMPLE_RATE, Recorder, list_input_devices, open_source  # noqa: E402

CLIP_SECONDS = 3.0


@pytest.fixture(scope="module")
def clip3(tmp_path_factory) -> Path:
    d = tmp_path_factory.mktemp("clip")
    raw = d / "raw.wav"
    out = d / "clip3.wav"
    subprocess.run(
        ["say", "--file-format=WAVE", "--data-format=LEI16@24000", "-o", str(raw),
         "One two three, this is a test of live meeting audio."],
        check=True,
    )
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(raw),
         "-af", "apad", "-t", str(CLIP_SECONDS), "-ac", "1", "-ar", str(SAMPLE_RATE), str(out)],
        check=True,
    )
    return out


async def _drain(spec: str) -> tuple[int, float]:
    src = open_source(spec)
    total = 0
    t0 = time.monotonic()
    async for block in src.blocks():
        assert block.dtype == np.float32 and block.ndim == 1 and len(block) > 0
        total += len(block)
    wall = time.monotonic() - t0
    src.close()
    return total, wall


def test_file_source_yields_clip_in_real_time(clip3):
    total, wall = asyncio.run(_drain(f"file:{clip3}"))
    expected = CLIP_SECONDS * SAMPLE_RATE
    assert abs(total - expected) <= 0.02 * expected
    assert wall >= 0.9 * CLIP_SECONDS


def test_recorder_playable_mid_write_and_after_close(tmp_path, clip3):
    audio, sr = sf.read(clip3, dtype="float32")
    assert sr == SAMPLE_RATE
    path = tmp_path / "rec.wav"
    rec = Recorder(path)
    chunk = SAMPLE_RATE // 10
    for n, start in enumerate(range(0, len(audio), chunk), start=1):
        rec.write(audio[start:start + chunk])
        if n == 10:
            mid = sf.info(path)
            assert mid.frames == 10 * chunk
            assert mid.samplerate == SAMPLE_RATE and mid.channels == 1
    rec.close()
    done = sf.info(path)
    assert done.frames == len(audio)
    assert done.samplerate == SAMPLE_RATE and done.channels == 1


def test_list_input_devices_returns_list():
    devices = list_input_devices()
    assert isinstance(devices, list)
    for d in devices:
        assert set(d) == {"index", "name"}
        assert isinstance(d["index"], int) and isinstance(d["name"], str)
