from __future__ import annotations

import re

MAX_QUESTION_LENGTH = 500

_INJECTION_PATTERNS = [
    re.compile(r"ignore (all|the|any) (previous|prior|above) instructions", re.IGNORECASE),
    re.compile(r"system prompt", re.IGNORECASE),
    re.compile(r"you are now", re.IGNORECASE),
    re.compile(r"disregard (your|the) (rules|instructions)", re.IGNORECASE),
]

_DESTRUCTIVE_PATTERNS = [
    re.compile(r"\b(delete|drop|truncate|update|insert|alter)\b.*(table|row|rows|record|data|database)", re.IGNORECASE),
    re.compile(r"\b(remove all|erase all|wipe all)\b", re.IGNORECASE),
]


def check_destructive_intent(question):
    for p in _DESTRUCTIVE_PATTERNS:
        if p.search(question):
            return True
    return False


class GuardrailError(Exception):
    pass


def validate_question(question):
    if question is None or not question.strip():
        raise GuardrailError("Question cannot be empty.")

    cleaned = question.strip()

    if len(cleaned) > MAX_QUESTION_LENGTH:
        raise GuardrailError(f"Question too long ({len(cleaned)} chars, max {MAX_QUESTION_LENGTH}).")

    for pattern in _INJECTION_PATTERNS:
        if pattern.search(cleaned):
            raise GuardrailError("Question looks like a prompt injection attempt and was rejected.")

    return cleaned


def enforce_row_limit(sql, max_rows):
    if re.search(r"\blimit\s+\d+", sql, re.IGNORECASE):
        return sql
    return f"{sql} LIMIT {max_rows}"
