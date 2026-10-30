"""HTTP access with an on-disk cache, offline mode and a pluggable transport.

Inside QGIS the plugin installs a fetcher based on ``QgsBlockingNetworkRequest`` so requests honour
the user's proxy and authentication settings; outside QGIS ``urllib`` is used. Responses are cached
in SQLite keyed by URL so a project can be re-run offline (and reviewed later with exactly the data
used at the time — the cache key and retrieval time are recorded in provenance).
"""

from __future__ import annotations

import hashlib
import sqlite3
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path

from ..core.errors import ConnectorError

USER_AGENT = "AGSGeoModel-QGIS/0.1 (+https://github.com/YOUR-ORG/AGSGeoModel-QGIS)"
Fetcher = Callable[[str, dict], bytes]

_FETCHER: Fetcher | None = None


def set_fetcher(f: Fetcher | None) -> None:
    global _FETCHER
    _FETCHER = f


def _urllib_fetch(url: str, headers: dict, timeout: float = 60.0) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **headers})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read()
    except urllib.error.HTTPError as exc:
        raise ConnectorError(f"HTTP {exc.code} for {url}") from exc
    except Exception as exc:
        raise ConnectorError(f"request failed for {url}: {exc}") from exc


class HttpCache:
    def __init__(
        self, path: str | Path | None = None, ttl_s: float = 30 * 24 * 3600, offline: bool = False, retries: int = 2
    ):
        self.path = Path(path) if path else Path.home() / ".agsgeomodel" / "http_cache.sqlite"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.ttl = ttl_s
        self.offline = offline
        self.retries = retries
        # usable from QGIS worker threads (tasks / Processing): one connection guarded by a lock
        self.conn = sqlite3.connect(str(self.path), check_same_thread=False, timeout=30)
        self._lock = threading.RLock()
        with self._lock:
            self.conn.execute(
                "CREATE TABLE IF NOT EXISTS cache (key TEXT PRIMARY KEY, url TEXT, fetched REAL, body BLOB)"
            )
        self.log: list[dict] = []

    @staticmethod
    def key(url: str, headers: dict | None = None) -> str:
        return hashlib.sha256((url + repr(sorted((headers or {}).items()))).encode()).hexdigest()

    def get(self, url: str, headers: dict | None = None, use_cache: bool = True) -> bytes:
        headers = headers or {}
        k = self.key(url, headers)
        with self._lock:
            row = self.conn.execute("SELECT fetched, body FROM cache WHERE key=?", (k,)).fetchone()
        if row and use_cache and (self.offline or time.time() - row[0] < self.ttl):
            self.log.append({"url": url, "from_cache": True, "fetched": row[0]})
            return row[1]
        if self.offline:
            raise ConnectorError(f"offline mode and no cached response for {url}")
        last: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                body = (_FETCHER or _urllib_fetch)(url, headers)
                with self._lock:
                    self.conn.execute("INSERT OR REPLACE INTO cache VALUES (?,?,?,?)", (k, url, time.time(), body))
                    self.conn.commit()
                self.log.append({"url": url, "from_cache": False, "fetched": time.time()})
                return body
            except ConnectorError as exc:
                last = exc
                time.sleep(min(2**attempt, 5))
        if row:  # stale cache is better than nothing; flagged in the log
            self.log.append({"url": url, "from_cache": True, "stale": True, "fetched": row[0]})
            return row[1]
        raise ConnectorError(str(last))

    def put(self, url: str, body: bytes, headers: dict | None = None) -> None:
        """Seed the cache (used by tests and for importing recorded responses)."""
        with self._lock:
            self.conn.execute(
                "INSERT OR REPLACE INTO cache VALUES (?,?,?,?)", (self.key(url, headers), url, time.time(), body)
            )
            self.conn.commit()
