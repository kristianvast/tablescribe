from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import time
from typing import Callable

import httpx

BRAVE_URL = "https://api.search.brave.com/res/v1/web/search"
_TAG = re.compile(r"<[^>]+>")

VERDICT_SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["supported", "disputed", "unclear"]},
        "summary": {"type": "string"},
        "sources": {"type": "array", "items": {"type": "integer"}},
    },
    "required": ["verdict", "summary", "sources"],
}

SYSTEM_PROMPT = (
    "You fact-check one claim from a meeting using ONLY the numbered search snippets provided. "
    "Do not use outside knowledge. Reply with JSON: "
    'verdict = exactly one of "supported" (snippets confirm the claim), "disputed" (snippets '
    'contradict it), "unclear" (snippets do not settle it); summary = one sentence; '
    "sources = the indices of the snippets you relied on."
)


def _strip_tags(text: str) -> str:
    return _TAG.sub("", text).strip()


def _query(text: str) -> str:
    return " ".join(text.replace("?", " ").replace("!", " ").split())[:150]


# Brave's free tier allows about 1 request/s; fact-checks run concurrently, so space every request.
MIN_INTERVAL = 1.1
_gate: tuple[asyncio.AbstractEventLoop, asyncio.Lock] | None = None
_last_request = 0.0


def _lock() -> asyncio.Lock:
    global _gate
    loop = asyncio.get_running_loop()
    if _gate is None or _gate[0] is not loop:
        _gate = (loop, asyncio.Lock())
    return _gate[1]


async def _spaced_get(client: httpx.AsyncClient, headers: dict, params: dict) -> httpx.Response:
    global _last_request
    async with _lock():
        wait = _last_request + MIN_INTERVAL - time.monotonic()
        if wait > 0:
            await asyncio.sleep(wait)
        try:
            return await client.get(BRAVE_URL, headers=headers, params=params)
        finally:
            _last_request = time.monotonic()


async def brave_search(query: str, count: int = 5) -> list[dict]:
    headers = {"X-Subscription-Token": os.environ["BRAVE_API_KEY"], "Accept": "application/json"}
    params = {"q": query, "count": count}
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await _spaced_get(client, headers, params)
        if resp.status_code == 429:
            resp = await _spaced_get(client, headers, params)
        resp.raise_for_status()
        results = resp.json().get("web", {}).get("results", [])
    return [
        {
            "title": _strip_tags(r.get("title", "")),
            "url": r.get("url", ""),
            "snippet": _strip_tags(r.get("description", "")),
        }
        for r in results
    ]


def _pick_sources(results: list[dict], indices: list) -> list[dict]:
    valid = [i for i in indices if isinstance(i, int) and not isinstance(i, bool) and 0 <= i < len(results)]
    chosen = [results[i] for i in dict.fromkeys(valid)] or results[:3]
    return [{"title": r["title"], "url": r["url"]} for r in chosen]


async def check_claim(claim: dict, complete: Callable) -> dict:
    text = claim["text"]
    base = {
        "id": hashlib.sha1(text.encode()).hexdigest()[:10],
        "claim": text,
        "speaker": claim.get("speaker", ""),
        "at": claim.get("at", 0.0),
    }
    if not os.environ.get("BRAVE_API_KEY"):
        return {**base, "verdict": "unclear",
                "summary": "Fact-check unavailable: BRAVE_API_KEY not set", "sources": []}

    results = await brave_search(_query(text))
    if not results:
        return {**base, "verdict": "unclear", "summary": "No search results found", "sources": []}

    snippets = "\n".join(f"[{i}] {r['title']} ({r['url']}): {r['snippet']}" for i, r in enumerate(results))
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"Claim: {text}\n\nSnippets:\n{snippets}"},
    ]
    raw = await complete(messages, json_schema=VERDICT_SCHEMA)
    try:
        data = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        data = None
    if not isinstance(data, dict):
        return {**base, "verdict": "unclear", "summary": "Could not parse fact-check result",
                "sources": _pick_sources(results, [])}

    verdict = data.get("verdict")
    if verdict not in ("supported", "disputed", "unclear"):
        verdict = "unclear"
    raw_sources = data.get("sources")
    indices = raw_sources if isinstance(raw_sources, list) else []
    return {**base, "verdict": verdict, "summary": str(data.get("summary", "")),
            "sources": _pick_sources(results, indices)}
