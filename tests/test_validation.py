from __future__ import annotations

import unittest

from src.validation import SQLValidator


class SQLValidatorTests(unittest.TestCase):
    def test_accepts_simple_select(self) -> None:
        result = SQLValidator.validate(
            "SELECT gender, AVG(addiction_level) FROM gaming_mental_health GROUP BY gender"
        )
        self.assertTrue(result.is_valid)
        self.assertIsNone(result.error)

    def test_rejects_none(self) -> None:
        result = SQLValidator.validate(None)
        self.assertFalse(result.is_valid)
        self.assertIsNotNone(result.error)

    def test_rejects_empty_string(self) -> None:
        result = SQLValidator.validate("   ")
        self.assertFalse(result.is_valid)

    def test_rejects_delete(self) -> None:
        result = SQLValidator.validate("DELETE FROM gaming_mental_health")
        self.assertFalse(result.is_valid)
        self.assertIsNotNone(result.error)

    def test_rejects_drop_table(self) -> None:
        result = SQLValidator.validate("DROP TABLE gaming_mental_health")
        self.assertFalse(result.is_valid)

    def test_rejects_update(self) -> None:
        result = SQLValidator.validate(
            "UPDATE gaming_mental_health SET addiction_level = 0"
        )
        self.assertFalse(result.is_valid)

    def test_rejects_non_select_statement(self) -> None:
        result = SQLValidator.validate("PRAGMA table_info(gaming_mental_health)")
        self.assertFalse(result.is_valid)

    def test_rejects_stacked_statements(self) -> None:
        result = SQLValidator.validate(
            "SELECT * FROM gaming_mental_health; DELETE FROM gaming_mental_health"
        )
        self.assertFalse(result.is_valid)

    def test_rejects_query_on_unknown_table(self) -> None:
        result = SQLValidator.validate("SELECT * FROM some_other_table")
        self.assertFalse(result.is_valid)

    def test_rejects_unknown_column(self) -> None:
        result = SQLValidator.validate(
            "SELECT zodiac_sign FROM gaming_mental_health"
        )
        self.assertFalse(result.is_valid)
        self.assertIn("zodiac_sign", result.error)

    def test_allows_aggregate_alias(self) -> None:
        result = SQLValidator.validate(
            "SELECT gender, AVG(addiction_level) AS avg_addiction "
            "FROM gaming_mental_health GROUP BY gender ORDER BY avg_addiction DESC"
        )
        self.assertTrue(result.is_valid, msg=result.error)

    def test_rejects_bad_syntax(self) -> None:
        result = SQLValidator.validate("SELECT FROM gaming_mental_health WHERE")
        self.assertFalse(result.is_valid)


if __name__ == "__main__":
    unittest.main()
