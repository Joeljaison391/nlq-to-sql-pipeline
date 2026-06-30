from __future__ import annotations

import time
import unittest

from src.cache import ResultCache, normalize_question


class NormalizeQuestionTests(unittest.TestCase):
    def test_collapses_whitespace_and_case(self) -> None:
        self.assertEqual(
            normalize_question("  How   Many Rows?  "),
            "how many rows?",
        )


class ResultCacheTests(unittest.TestCase):
    def test_miss_then_hit(self) -> None:
        cache = ResultCache(ttl_s=60)
        self.assertIsNone(cache.get("How many rows?"))
        cache.set("How many rows?", {"answer": 42})
        self.assertEqual(cache.get("how many rows?"), {"answer": 42})

    def test_expires_after_ttl(self) -> None:
        cache = ResultCache(ttl_s=0.01)
        cache.set("question", "value")
        time.sleep(0.05)
        self.assertIsNone(cache.get("question"))

    def test_evicts_oldest_when_full(self) -> None:
        cache = ResultCache(ttl_s=60, max_entries=2)
        cache.set("first", 1)
        cache.set("second", 2)
        cache.set("third", 3)
        self.assertEqual(len(cache), 2)
        self.assertIsNone(cache.get("first"))
        self.assertEqual(cache.get("third"), 3)


if __name__ == "__main__":
    unittest.main()
