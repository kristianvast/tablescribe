"""Streaming speaker-attributed ASR: VibeVoice-ASR-Streaming-7B (MLX, int4).

The model emits plain text per 2.93 s chunk with inline speaker markers, e.g.
" \\n Speaker 0:So, uh, um, " ... "get? \\n Speaker 1:Um, I just got ...".
We keep the language-model KV cache across chunks (that is what keeps speaker
ids consistent), split the text on the markers and turn it into finalized
segments on the cumulative audio clock.

Memory bound: the KV cache is an mlx RotatingKVCache capped at MAX_KV_TOKENS
with `keep` = the prompt length, so the system prompt + hotwords stay pinned
and only the oldest audio/text context rolls off (~9 min of context kept).
RoPE positions keep growing, so once the absolute position nears the model's
trained limit we re-prefill the prompt (REANCHOR_TOKENS, ~2 h of speech);
speaker ids may restart at that point.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from collections import deque
from typing import Any

import mlx.core as mx
import numpy as np
from mlx_audio.stt.utils import load_model

MODEL_ID = "mlx-community/VibeVoice-ASR-Streaming-7B-4bit"
MAX_KV_TOKENS = 8192
REANCHOR_TOKENS = 120_000
MAX_NEW_TOKENS = 128
SEG_SOFT_S = 6.0
SEG_HARD_S = 25.0
RTF_WINDOW = 20

_MARKER = re.compile(r"\s*Speaker\s+(\d+)\s*:\s*")
_SENTENCE_END = re.compile(r"[.?!][\"')\]]*$")


class StreamingASR:
    sample_rate: int
    chunk_seconds: float
    lookahead_seconds: float
    last_rtf: float

    def __init__(self, model_id: str = MODEL_ID) -> None:
        self._model: Any = load_model(model_id)
        m = self._model
        if not m.is_streaming_model:
            raise ValueError(f"{model_id} is not a VibeVoice-ASR-Streaming checkpoint")
        self.sample_rate = int(m.sample_rate)
        self._chunk = int(m.streaming_chunk_samples)
        self._window = int(m.streaming_window_samples)
        self.chunk_seconds = self._chunk / self.sample_rate
        self.lookahead_seconds = (self._window - self._chunk) / self.sample_rate
        self._hotwords: list[str] = []
        self.chunk_ms: deque[float] = deque(maxlen=100_000)
        self.reset()

    def reset(self) -> None:
        """Drop all streaming state and restart the audio clock at 0 (weights stay loaded)."""
        self._buf = np.zeros(0, dtype=np.float32)
        self._buf_start = 0
        self._fed = 0
        self._seg: dict[str, Any] | None = None
        self._recent: deque[tuple[float, float]] = deque(maxlen=RTF_WINDOW)
        self.last_rtf = 0.0
        self.chunk_ms.clear()
        self._init_state()

    def set_context(self, hotwords: list[str]) -> None:
        """Participant names + jargon into the context prefix.

        Re-prefills the model prompt; call it right after reset(). Mid-stream it
        works too but the model forgets earlier speakers (ids may restart).
        """
        seen: dict[str, None] = {}
        for w in hotwords:
            w = w.strip()
            if w:
                seen.setdefault(w, None)
        self._hotwords = list(seen)
        self._init_state()

    def feed(self, pcm: np.ndarray) -> list[dict]:
        """Accumulate float32 mono 24 kHz audio; return newly finalized segments."""
        pcm = np.asarray(pcm, dtype=np.float32).reshape(-1)
        self._buf = np.concatenate([self._buf, pcm])
        self._fed += pcm.size
        out: list[dict] = []
        while self._buf.size >= self._window:
            out += self._step(self._buf[: self._window])
        return out

    def flush(self) -> list[dict]:
        """Decode the remaining tail (zero-padded) and finalize the open segment."""
        out: list[dict] = []
        while self._buf.size > 0:
            w = np.pad(self._buf[: self._window], (0, max(0, self._window - self._buf.size)))
            out += self._step(w)
        self._buf_start = self._fed
        end = self._fed / self.sample_rate
        seg = self._close(end)
        if seg:
            out.append(seg)
        return out

    def _init_state(self) -> None:
        ctx = ", ".join(self._hotwords) or None
        st = self._model.init_streaming_state(context_info=ctx, max_kv_size=MAX_KV_TOKENS)
        # mlx's helper pins only 4 tokens; pin the whole prompt (system text + hotwords).
        for c in st["cache"]:
            c.keep = st["prompt_tokens"]
        self._state: dict[str, Any] = st
        self._seg = None
        mx.clear_cache()

    def _step(self, window: np.ndarray) -> list[dict]:
        t0 = time.perf_counter()
        st = self._state
        if st["cache"][0].offset > REANCHOR_TOKENS:
            done = self._close(self._buf_start / self.sample_rate)
            self._init_state()
            st = self._state
        else:
            done = None
        feats = self._model.encode_speech(mx.array(window)[None])
        feats = feats.astype(st["speech_start_embed"].dtype)  # keep the KV cache in bf16
        text, _ = self._model.streaming_generate_step(feats, st, max_new_tokens=MAX_NEW_TOKENS)
        mx.clear_cache()
        c0 = self._buf_start / self.sample_rate
        self._buf = self._buf[self._chunk :]
        self._buf_start += self._chunk
        dt = time.perf_counter() - t0
        self.chunk_ms.append(dt * 1000)
        self._recent.append((dt, self.chunk_seconds))
        self.last_rtf = sum(a for a, _ in self._recent) / sum(b for _, b in self._recent)
        out = [done] if done else []
        out += self._ingest(text, c0, c0 + self.chunk_seconds)
        return out

    def _ingest(self, text: str, c0: float, c1: float) -> list[dict]:
        """Split chunk text on speaker markers; times interpolated by char position."""
        out: list[dict] = []
        n = max(len(text), 1)
        pos = 0
        for m in _MARKER.finditer(text):
            self._append(text[pos : m.start()], c0)
            t = c0 + (c1 - c0) * m.start() / n
            seg = self._close(t)
            if seg:
                out.append(seg)
            self._seg = {"speaker": int(m.group(1)), "text": "", "start": t}
            pos = m.end()
        self._append(text[pos:], c0)
        s = self._seg
        if s is not None and s["text"]:
            dur = c1 - s["start"]
            silent = not text.strip()
            if silent or dur >= SEG_HARD_S or (dur >= SEG_SOFT_S and _SENTENCE_END.search(s["text"])):
                seg = self._close(c0 if silent else c1)
                if seg:
                    out.append(seg)
                self._seg = {"speaker": seg["speaker"] if seg else 0, "text": "", "start": c1}
        return out

    def _append(self, piece: str, t: float) -> None:
        piece = " ".join(piece.split())
        if not piece:
            return
        if self._seg is None:
            self._seg = {"speaker": 0, "text": "", "start": t}
        s = self._seg
        if not s["text"] and t > s["start"]:
            s["start"] = t
        s["text"] = f"{s['text']} {piece}".strip()

    def _close(self, end: float) -> dict | None:
        s = self._seg
        self._seg = {"speaker": s["speaker"], "text": "", "start": end} if s else None
        if not s or not s["text"]:
            return None
        return {
            "speaker": s["speaker"],
            "text": s["text"],
            "start": round(s["start"], 2),
            "end": round(max(end, s["start"]), 2),
        }


def _decode(path: str, sr: int) -> np.ndarray:
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", path, "-ac", "1", "-ar", str(sr), "-f", "f32le", "-"],
        check=True,
        capture_output=True,
    ).stdout
    return np.frombuffer(raw, dtype=np.float32)


def _bench(argv: list[str]) -> None:
    ap = argparse.ArgumentParser(prog="python -m app.asr")
    ap.add_argument("--bench", required=True, metavar="WAV")
    ap.add_argument("--hotwords", default="")
    ap.add_argument("--model", default=MODEL_ID)
    a = ap.parse_args(argv)

    asr = StreamingASR(a.model)
    asr.reset()
    if a.hotwords:
        asr.set_context(a.hotwords.split(","))
    audio = _decode(a.bench, asr.sample_rate)
    block = asr.sample_rate // 10
    segs: list[dict] = []

    def show(new: list[dict]) -> None:
        for s in new:
            mm, ss = divmod(int(s["start"]), 60)
            print(f"[{mm:02d}:{ss:02d}] S{s['speaker']}: {s['text']}", flush=True)
        segs.extend(new)

    mx.reset_peak_memory()
    t0 = time.perf_counter()
    for i in range(0, audio.size, block):
        show(asr.feed(audio[i : i + block]))
    show(asr.flush())
    compute = time.perf_counter() - t0
    ms = np.array(asr.chunk_ms) if asr.chunk_ms else np.zeros(1)
    audio_s = audio.size / asr.sample_rate
    print(
        json.dumps(
            {
                "audio_s": round(audio_s, 2),
                "compute_s": round(compute, 2),
                "rtf": round(compute / audio_s, 3),
                "speakers": sorted({s["speaker"] for s in segs}),
                "n_segments": len(segs),
                "peak_mem_gb": round(mx.get_peak_memory() / 1e9, 2),
                "per_chunk_ms_p50": round(float(np.percentile(ms, 50)), 1),
                "per_chunk_ms_p95": round(float(np.percentile(ms, 95)), 1),
            }
        )
    )


if __name__ == "__main__":
    _bench(sys.argv[1:])
