from __future__ import annotations

import asyncio
import re
import struct
import subprocess
import time
from pathlib import Path
from typing import AsyncIterator, Protocol

import numpy as np

SAMPLE_RATE = 24000
BLOCK_SAMPLES = SAMPLE_RATE // 10
_FFMPEG = "ffmpeg"
_OUT_ARGS = ["-ac", "1", "-ar", str(SAMPLE_RATE), "-f", "f32le", "-"]
_DEVICE_LINE = re.compile(r"\[(\d+)\] (.+?)\s*$")


class AudioSource(Protocol):
    def blocks(self) -> AsyncIterator[np.ndarray]: ...
    def close(self) -> None: ...


class _FFmpegSource:

    def __init__(self, input_args: list[str]) -> None:
        self._input_args = input_args
        self._proc: asyncio.subprocess.Process | None = None
        self._closed = False

    async def blocks(self) -> AsyncIterator[np.ndarray]:
        proc = await asyncio.create_subprocess_exec(
            _FFMPEG, "-hide_banner", "-loglevel", "error", *self._input_args, *_OUT_ARGS,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        self._proc = proc
        assert proc.stdout is not None and proc.stderr is not None
        nbytes = BLOCK_SAMPLES * 4
        try:
            while True:
                try:
                    raw = await proc.stdout.readexactly(nbytes)
                except asyncio.IncompleteReadError as e:
                    tail = e.partial[: len(e.partial) - len(e.partial) % 4]
                    if tail:
                        yield np.frombuffer(tail, dtype="<f4").copy()
                    break
                yield np.frombuffer(raw, dtype="<f4").copy()
        finally:
            if proc.returncode is None:
                proc.terminate()
        rc = await proc.wait()
        if rc != 0 and not self._closed:
            err = (await proc.stderr.read()).decode(errors="replace").strip()
            raise RuntimeError(f"ffmpeg exited {rc}: {err}")

    def close(self) -> None:
        self._closed = True
        if self._proc is not None and self._proc.returncode is None:
            self._proc.terminate()


class MicSource(_FFmpegSource):
    def __init__(self, device: str) -> None:
        super().__init__(["-f", "avfoundation", "-i", f":{device}"])


class FileSource(_FFmpegSource):
    def __init__(self, path: Path) -> None:
        super().__init__(["-i", str(path)])

    async def blocks(self) -> AsyncIterator[np.ndarray]:
        start = time.monotonic()
        emitted = 0
        async for block in super().blocks():
            delay = start + emitted / SAMPLE_RATE - time.monotonic()
            if delay > 0:
                await asyncio.sleep(delay)
            yield block
            emitted += len(block)


def list_input_devices() -> list[dict]:
    proc = subprocess.run(
        [_FFMPEG, "-hide_banner", "-f", "avfoundation", "-list_devices", "true", "-i", ""],
        capture_output=True,
        text=True,
    )
    devices: list[dict] = []
    in_audio = False
    for line in (proc.stdout + proc.stderr).splitlines():
        if "AVFoundation audio devices" in line:
            in_audio = True
        elif "AVFoundation video devices" in line:
            in_audio = False
        elif in_audio:
            m = _DEVICE_LINE.search(line)
            if m:
                devices.append({"index": int(m[1]), "name": m[2]})
    return devices


def _resolve_device(arg: str) -> str:
    if arg.isdigit():
        return arg
    matches = [d for d in list_input_devices() if arg.lower() in d["name"].lower()]
    if not matches:
        raise ValueError(f"no audio input device matches {arg!r}")
    return str(matches[0]["index"])


def _system_default_input() -> str | None:
    proc = subprocess.run(
        ["system_profiler", "SPAudioDataType"], capture_output=True, text=True
    )
    name = None
    for line in proc.stdout.splitlines():
        stripped = line.strip()
        if stripped.endswith(":") and not stripped.startswith(("Audio", "Devices")):
            name = stripped[:-1]
        elif stripped == "Default Input Device: Yes":
            return name
    return None


def _default_device() -> str:
    # avfoundation index 0 is often an iPhone Continuity mic, so follow the macOS default input.
    devices = list_input_devices()
    default = _system_default_input()
    for d in devices:
        if default and d["name"] == default:
            return str(d["index"])
    for d in devices:
        if "iphone" not in d["name"].lower():
            return str(d["index"])
    return "0"


def open_source(spec: str) -> AudioSource:
    kind, _, arg = spec.partition(":")
    if kind == "mic":
        return MicSource(_resolve_device(arg) if arg else _default_device())
    if kind == "file":
        path = Path(arg)
        if not path.is_file():
            raise FileNotFoundError(path)
        return FileSource(path)
    raise ValueError(f"unknown audio source {spec!r}; use mic, mic:<device>, or file:<path>")


def _wav_header(frames: int) -> bytes:
    data_bytes = frames * 2
    return struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF", 36 + data_bytes, b"WAVE",
        b"fmt ", 16, 1, 1, SAMPLE_RATE, SAMPLE_RATE * 2, 2, 16,
        b"data", data_bytes,
    )


class Recorder:

    def __init__(self, path: Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._f = path.open("wb")
        self._frames = 0
        self._f.write(_wav_header(0))
        self._f.flush()

    def write(self, pcm: np.ndarray) -> None:
        samples = (np.clip(pcm, -1.0, 1.0) * 32767).astype("<i2")
        self._f.write(samples.tobytes())
        self._frames += len(samples)
        self._f.seek(0)
        self._f.write(_wav_header(self._frames))
        self._f.seek(0, 2)
        self._f.flush()

    def close(self) -> None:
        if not self._f.closed:
            self._f.close()
