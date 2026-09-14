"""Disk cache for downloaded Nichibo pages and JSON, so re-runs and rule changes do not
re-download what was fetched recently. Keyed by URL, expires after `max_age_hours`."""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path


class PageCache:
    def __init__(self, root: Path, max_age_hours: float = 12.0, enabled: bool = True):
        self.root = root
        self.max_age = max_age_hours * 3600.0
        self.enabled = enabled
        self.hits = 0
        self.misses = 0
        if enabled:
            root.mkdir(parents=True, exist_ok=True)

    def _paths(self, key: str) -> tuple[Path, Path]:
        h = hashlib.sha1(key.encode("utf-8")).hexdigest()
        return self.root / f"{h}.body", self.root / f"{h}.json"

    def get(self, key: str) -> str | None:
        if not self.enabled:
            return None
        body, meta = self._paths(key)
        if not body.exists() or not meta.exists():
            self.misses += 1
            return None
        try:
            info = json.loads(meta.read_text(encoding="utf-8"))
            if time.time() - float(info.get("fetched_at", 0)) > self.max_age:
                self.misses += 1
                return None
            self.hits += 1
            return body.read_text(encoding="utf-8")
        except (OSError, ValueError):
            self.misses += 1
            return None

    def put(self, key: str, text: str) -> None:
        if not self.enabled:
            return
        body, meta = self._paths(key)
        body.write_text(text, encoding="utf-8")
        meta.write_text(json.dumps({"key": key, "fetched_at": time.time()}), encoding="utf-8")

    def get_json(self, key: str):
        text = self.get(key)
        if text is None:
            return None
        try:
            return json.loads(text)
        except ValueError:
            return None

    def put_json(self, key: str, data) -> None:
        self.put(key, json.dumps(data, ensure_ascii=False))


class NoCache(PageCache):
    def __init__(self):
        super().__init__(Path("."), 0, enabled=False)
