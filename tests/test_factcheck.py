import asyncio
import hashlib
import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import factcheck  # noqa: E402


def test_brave_search_real_call():
    if not os.environ.get("BRAVE_API_KEY"):
        pytest.skip("BRAVE_API_KEY not set")
    results = asyncio.run(factcheck.brave_search("Eiffel Tower height metres"))
    assert results
    assert any(r["url"].startswith("http") for r in results)
    assert all({"title", "url", "snippet"} <= r.keys() for r in results)


def test_check_claim_parses_verdict_and_selects_sources(monkeypatch):
    monkeypatch.setenv("BRAVE_API_KEY", "test-key")
    fake_results = [
        {"title": f"T{i}", "url": f"https://example.com/{i}", "snippet": f"S{i}"} for i in range(6)
    ]

    async def fake_search(query, count=5):
        return fake_results

    monkeypatch.setattr(factcheck, "brave_search", fake_search)

    async def complete(messages, *, json_schema=None, stream=False):
        assert json_schema is factcheck.VERDICT_SCHEMA
        return json.dumps({"verdict": "supported", "summary": "Confirmed.", "sources": [1, 4]})

    claim = {"text": "The Eiffel Tower is 330 metres tall", "speaker": "Ana", "at": 12.5}
    out = asyncio.run(factcheck.check_claim(claim, complete))
    assert out["verdict"] == "supported"
    assert out["summary"] == "Confirmed."
    assert out["id"] == hashlib.sha1(claim["text"].encode()).hexdigest()[:10]
    assert out["speaker"] == "Ana" and out["at"] == 12.5
    assert out["sources"] == [
        {"title": "T1", "url": "https://example.com/1"},
        {"title": "T4", "url": "https://example.com/4"},
    ]


def test_check_claim_invalid_json_is_unclear(monkeypatch):
    monkeypatch.setenv("BRAVE_API_KEY", "test-key")

    async def fake_search(query, count=5):
        return [{"title": "T0", "url": "https://example.com/0", "snippet": "S0"}]

    monkeypatch.setattr(factcheck, "brave_search", fake_search)

    async def complete(messages, *, json_schema=None, stream=False):
        return "not json {"

    out = asyncio.run(factcheck.check_claim({"text": "x", "speaker": "Ben", "at": 1.0}, complete))
    assert out["verdict"] == "unclear"
    assert out["sources"] == [{"title": "T0", "url": "https://example.com/0"}]
