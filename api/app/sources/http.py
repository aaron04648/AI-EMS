"""HTTP-Zugriff mit einfachem In-Memory-Cache (TTL) und Wiederholung bei 429/5xx."""

from __future__ import annotations

import threading
import time
from typing import Any

import httpx

_client = httpx.Client(
    timeout=httpx.Timeout(60.0, connect=10.0),
    headers={"user-agent": "AI-EMS backend (Studienprojekt)", "accept": "application/json"},
    follow_redirects=True,
)
_cache: dict[str, tuple[float, Any]] = {}
_lock = threading.Lock()


class SourceError(RuntimeError):
    """Eine externe Datenquelle hat nicht (oder fehlerhaft) geantwortet."""


def get_json(url: str, params: dict[str, Any] | None = None, ttl: float = 3600, retries: int = 4) -> Any:
    key = url + "?" + "&".join(f"{k}={v}" for k, v in sorted((params or {}).items()))
    now = time.time()
    with _lock:
        hit = _cache.get(key)
        if hit and hit[0] > now:
            return hit[1]

    last_error: Exception | None = None
    for attempt in range(retries):
        try:
            res = _client.get(url, params=params)
            if res.status_code == 200:
                data = res.json()
                with _lock:
                    _cache[key] = (now + ttl, data)
                    if len(_cache) > 256:  # Speicher begrenzen: älteste Einträge verwerfen
                        for old in sorted(_cache, key=lambda k: _cache[k][0])[:64]:
                            _cache.pop(old, None)
                return data
            if res.status_code != 429 and res.status_code < 500:
                raise SourceError(f"{url}: HTTP {res.status_code} – {res.text[:200]}")
            last_error = SourceError(f"{url}: HTTP {res.status_code}")
        except httpx.HTTPError as err:
            last_error = err
        time.sleep(min(30, 3 * 2**attempt))  # 3, 6, 12, 24 s – energy-charts drosselt hart
    raise SourceError(f"{url}: keine Antwort ({last_error})")


def get_text(url: str, ttl: float = 3600, encoding: str | None = None) -> str:
    """Wie get_json, aber für Textdateien (z. B. CSV von MeteoSwiss)."""
    now = time.time()
    with _lock:
        hit = _cache.get(url)
        if hit and hit[0] > now:
            return hit[1]
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            res = _client.get(url)
            if res.status_code == 200:
                if encoding:
                    res.encoding = encoding
                with _lock:
                    _cache[url] = (now + ttl, res.text)
                return res.text
            last_error = SourceError(f"{url}: HTTP {res.status_code}")
            if res.status_code < 500 and res.status_code != 429:
                break
        except httpx.HTTPError as err:
            last_error = err
        time.sleep(2 * (attempt + 1))
    raise SourceError(f"{url}: keine Antwort ({last_error})")
