from __future__ import annotations

import re
import time
from typing import Any

DEFAULT_TTL_S = 300.0
DEFAULT_MAX_ENTRIES = 256


def normalize_question(question: str) -> str:
    return re.sub(r"\s+", " ", question.strip().lower())


class ResultCache:
    def __init__(self, ttl_s=DEFAULT_TTL_S, max_entries=DEFAULT_MAX_ENTRIES):
        self.ttl_s = ttl_s
        self.max_entries = max_entries
        self._store: dict[str, tuple[float, Any]] = {}

    def get(self, question: str):
        key = normalize_question(question)
        entry = self._store.get(key)
        if entry is None:
            return None
        expires_at, value = entry
        if time.time() > expires_at:
            del self._store[key]
            return None
        return value

    def set(self, question: str, value: Any):
        key = normalize_question(question)
        if len(self._store) >= self.max_entries and key not in self._store:
            oldest = min(self._store, key=lambda k: self._store[k][0])
            del self._store[oldest]
        self._store[key] = (time.time() + self.ttl_s, value)

    def clear(self):
        self._store.clear()

    def __len__(self):
        return len(self._store)
