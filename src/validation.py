from __future__ import annotations

import re
import sqlite3
import time
from pathlib import Path

from src.schema import ALLOWED_COLUMNS, COLUMNS, TABLE_NAME
from src.types import SQLValidationOutput

_FORBIDDEN_KEYWORDS = [
    "insert", "update", "delete", "drop", "alter", "create", "truncate",
    "replace", "attach", "detach", "pragma", "vacuum", "reindex", "grant", "revoke",
]
_FORBIDDEN_PATTERN = re.compile(r"\b(" + "|".join(_FORBIDDEN_KEYWORDS) + r")\b", re.IGNORECASE)
_MULTI_STATEMENT_PATTERN = re.compile(r";\s*\S")


class SQLValidator:

    @classmethod
    def validate(cls, sql, db_path=None):
        start = time.perf_counter()

        if sql is None or not sql.strip():
            return cls._fail("No SQL provided", start)

        cleaned = sql.strip().rstrip(";")

        if not cleaned.lower().lstrip().startswith("select"):
            return cls._fail("Only SELECT statements are allowed", start)

        if _FORBIDDEN_PATTERN.search(cleaned):
            return cls._fail("Query contains a forbidden keyword", start)

        if _MULTI_STATEMENT_PATTERN.search(cleaned):
            return cls._fail("Multiple SQL statements are not allowed", start)

        if TABLE_NAME.lower() not in cleaned.lower():
            return cls._fail(f"Query must reference the '{TABLE_NAME}' table", start)

        unknown = cls._find_unknown_columns(cleaned)
        if unknown:
            return cls._fail(f"Query references unknown column(s): {', '.join(sorted(unknown))}", start)

        syntax_error = cls._check_syntax(cleaned, db_path)
        if syntax_error:
            return cls._fail(f"SQL syntax error: {syntax_error}", start)

        return SQLValidationOutput(
            is_valid=True,
            validated_sql=cleaned,
            error=None,
            timing_ms=(time.perf_counter() - start) * 1000,
        )

    @staticmethod
    def _fail(message, start):
        return SQLValidationOutput(
            is_valid=False,
            validated_sql=None,
            error=message,
            timing_ms=(time.perf_counter() - start) * 1000,
        )

    @staticmethod
    def _find_unknown_columns(sql):
        keywords = {
            "select", "from", "where", "group", "by", "order", "limit", "as",
            "and", "or", "not", "in", "is", "null", "asc", "desc", "having",
            "distinct", "between", "like", "case", "when", "then", "else", "end",
            "join", "on", "inner", "left", "right", "outer", "count", "avg",
            "sum", "min", "max", "round", "cast", "real", "integer", "text",
            "all", "exists", "union", "offset",
        }
        aliases = {m.group(1).lower() for m in re.finditer(r"\bAS\s+([A-Za-z_][A-Za-z0-9_]*)", sql, re.IGNORECASE)}

        unknown = set()
        for match in re.finditer(r"[A-Za-z_][A-Za-z0-9_]*", sql):
            tok = match.group(0)
            low = tok.lower()
            if low in keywords or low == TABLE_NAME.lower() or low in ALLOWED_COLUMNS or low in aliases:
                continue
            if sql[match.end():].lstrip().startswith("("):
                continue
            unknown.add(tok)
        return unknown

    @staticmethod
    def _check_syntax(sql, db_path):
        try:
            if db_path:
                uri = f"file:{Path(db_path).as_posix()}?mode=ro"
                conn = sqlite3.connect(uri, uri=True)
            else:
                conn = sqlite3.connect(":memory:")
                cols = ", ".join(f'"{n}" {t}' for n, (t, _) in COLUMNS.items())
                conn.execute(f'CREATE TABLE "{TABLE_NAME}" ({cols})')
            try:
                conn.execute(f"EXPLAIN {sql}")
            finally:
                conn.close()
        except sqlite3.Error as e:
            return str(e)
        return None
