from __future__ import annotations

import json
import os
import time
from typing import Any

from src.observability import setup_logging
from src.schema import compact_schema_text
from src.types import SQLGenerationOutput, AnswerGenerationOutput

DEFAULT_MODEL = "openai/gpt-5-nano"
logger = setup_logging()
_SCHEMA_TEXT = compact_schema_text()


def _estimate_tokens(text: str) -> int:
    if not text:
        return 0
    return max(1, len(text) // 4)


class OpenRouterLLMClient:
    provider_name = "openrouter"

    def __init__(self, api_key, model=None, max_retries=2, retry_backoff_s=1.0, request_timeout_s=30.0):
        try:
            from openrouter import OpenRouter
        except ModuleNotFoundError as exc:
            raise RuntimeError("Missing dependency: install 'openrouter'.") from exc

        self.model = model or os.getenv("OPENROUTER_MODEL", DEFAULT_MODEL)
        self.max_retries = max_retries
        self.retry_backoff_s = retry_backoff_s
        self._client = OpenRouter(api_key=api_key, timeout_ms=int(request_timeout_s * 1000))
        self._stats = {"llm_calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

    def _chat(self, messages, temperature, max_tokens):
        last_exc = None
        for attempt in range(self.max_retries + 1):
            try:
                res = self._client.chat.send(
                    messages=messages,
                    model=self.model,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    reasoning_effort="minimal",
                    stream=False,
                )

                usage = getattr(res, "usage", None)
                prompt_tokens = getattr(usage, "prompt_tokens", None) if usage else None
                completion_tokens = getattr(usage, "completion_tokens", None) if usage else None
                total_tokens = getattr(usage, "total_tokens", None) if usage else None

                choices = getattr(res, "choices", None) or []
                if not choices:
                    raise RuntimeError("No choices in response.")
                content = getattr(getattr(choices[0], "message", None), "content", None)
                if not isinstance(content, str):
                    raise RuntimeError("Response content was not text.")

                if prompt_tokens is None:
                    prompt_tokens = _estimate_tokens(json.dumps(messages))
                if completion_tokens is None:
                    completion_tokens = _estimate_tokens(content)
                if total_tokens is None:
                    total_tokens = prompt_tokens + completion_tokens

                self._stats["llm_calls"] += 1
                self._stats["prompt_tokens"] += int(prompt_tokens)
                self._stats["completion_tokens"] += int(completion_tokens)
                self._stats["total_tokens"] += int(total_tokens)
                return content.strip()

            except Exception as exc:
                last_exc = exc
                if attempt < self.max_retries:
                    logger.warning("LLM attempt %d failed: %s. Retrying...", attempt + 1, exc)
                    time.sleep(self.retry_backoff_s * (attempt + 1))
                else:
                    logger.error("LLM failed after %d attempts: %s", attempt + 1, exc)

        raise last_exc

    @staticmethod
    def _extract_sql(text):
        maybe_json = text.strip()
        if maybe_json.startswith("```"):
            maybe_json = maybe_json.strip("`")
            if maybe_json.lower().startswith("json"):
                maybe_json = maybe_json[4:]
            maybe_json = maybe_json.strip()

        if maybe_json.startswith("{") and maybe_json.endswith("}"):
            try:
                parsed = json.loads(maybe_json)
                sql = parsed.get("sql")
                if isinstance(sql, str) and sql.strip():
                    return sql.strip().rstrip(";")
            except json.JSONDecodeError:
                pass

        lower = text.lower()
        idx = lower.find("select ")
        if idx >= 0:
            sql = text[idx:].strip().split("```")[0].strip()
            return sql.rstrip(";")
        return None

    def generate_sql(self, question, context):
        system_prompt = (
            "You are a SQL assistant for a SQLite analytics database. "
            "Generate a SQLite query that answers the user's question, using ONLY the table and columns listed below.\n\n"
            f"{_SCHEMA_TEXT}\n\n"
            "Rules:\n"
            "- If the question asks to read/aggregate data, write a SELECT query.\n"
            "- If the question asks to modify data (insert/update/delete/drop/etc.), "
            "write the literal SQL statement for that request as-is "
            "(a separate safety layer will reject it) - do not refuse and do not return null.\n"
            "- If the question cannot be answered with the columns above at all, respond with exactly: {\"sql\": null}\n"
            "- Only use the table and column names given above.\n"
            "- Use only standard SQLite functions (AVG, COUNT, SUM, MIN, MAX, ROUND, CASE). "
            "SQLite has no STDDEV/VARIANCE/PERCENTILE functions - do not use them.\n"
            "- Respond with a JSON object: {\"sql\": \"<query>\"}\n"
        )

        start = time.perf_counter()
        error = None
        sql = None
        try:
            text = self._chat(
                messages=[{"role": "system", "content": system_prompt}, {"role": "user", "content": f"Question: {question}"}],
                temperature=0.0,
                max_tokens=300,
            )
            sql = self._extract_sql(text)
        except Exception as exc:
            error = str(exc)
            logger.error("SQL generation failed: %s", exc)

        llm_stats = self.pop_stats()
        llm_stats["model"] = self.model
        return SQLGenerationOutput(sql=sql, timing_ms=(time.perf_counter() - start) * 1000, llm_stats=llm_stats, error=error)

    def generate_answer(self, question, sql, rows):
        if not sql:
            return AnswerGenerationOutput(
                answer="I cannot answer this with the available data. Please rephrase using known survey fields.",
                timing_ms=0.0,
                llm_stats={"llm_calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "model": self.model},
                error=None,
            )
        if not rows:
            return AnswerGenerationOutput(
                answer="Query executed but returned no rows.",
                timing_ms=0.0,
                llm_stats={"llm_calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "model": self.model},
                error=None,
            )

        sample = rows[:20]
        user_prompt = (
            f"Question:\n{question}\n\nSQL:\n{sql}\n\n"
            f"Rows (showing {len(sample)} of {len(rows)}):\n{json.dumps(sample, ensure_ascii=True)}\n\n"
            "Write a concise answer in plain English."
        )

        start = time.perf_counter()
        error = None
        answer = ""
        try:
            answer = self._chat(
                messages=[
                    {"role": "system", "content": "You are a concise analytics assistant. Use only the provided SQL results. Do not invent data."},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=0.2,
                max_tokens=300,
            )
        except Exception as exc:
            error = str(exc)
            answer = "Sorry, couldn't generate an answer right now. Please try again."
            logger.error("Answer generation failed: %s", exc)

        llm_stats = self.pop_stats()
        llm_stats["model"] = self.model
        return AnswerGenerationOutput(answer=answer, timing_ms=(time.perf_counter() - start) * 1000, llm_stats=llm_stats, error=error)

    def pop_stats(self):
        out = dict(self._stats)
        self._stats = {"llm_calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        return out


def build_default_llm_client():
    api_key = os.getenv("OPENROUTER_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("OPENROUTER_API_KEY is required.")
    return OpenRouterLLMClient(api_key=api_key)
