from __future__ import annotations

import sqlite3
import time
from dataclasses import replace
from pathlib import Path

from src.cache import ResultCache
from src.guardrails import GuardrailError, enforce_row_limit, validate_question
from src.llm_client import OpenRouterLLMClient, build_default_llm_client
from src.observability import log_metric, new_request_id, setup_logging, trace_span
from src.validation import SQLValidator
from src.types import (
    AnswerGenerationOutput, SQLExecutionOutput, SQLGenerationOutput,
    SQLValidationOutput, PipelineOutput,
)

BASE_DIR = Path(__file__).resolve().parents[1]
DEFAULT_DB_PATH = BASE_DIR / "data" / "gaming_mental_health.sqlite"
QUERY_TIMEOUT_S = 8.0
logger = setup_logging()


class SQLiteExecutor:
    def __init__(self, db_path=DEFAULT_DB_PATH, max_rows=100, timeout_s=QUERY_TIMEOUT_S):
        self.db_path = Path(db_path)
        self.max_rows = max_rows
        self.timeout_s = timeout_s

    def run(self, sql):
        start = time.perf_counter()

        if sql is None:
            return SQLExecutionOutput(rows=[], row_count=0, timing_ms=0.0, error=None)

        rows = []
        error = None
        try:
            uri = f"file:{self.db_path.as_posix()}?mode=ro"
            with sqlite3.connect(uri, uri=True) as conn:
                conn.row_factory = sqlite3.Row
                deadline = time.perf_counter() + self.timeout_s
                conn.set_progress_handler(lambda: time.perf_counter() > deadline, 1000)
                cur = conn.cursor()
                cur.execute(sql)
                rows = [dict(r) for r in cur.fetchmany(self.max_rows)]
        except Exception as exc:
            error = str(exc)
            rows = []
            logger.error("SQL execution failed: %s", exc)

        return SQLExecutionOutput(rows=rows, row_count=len(rows), timing_ms=(time.perf_counter() - start) * 1000, error=error)


def _empty_stage_outputs():
    empty_stats = {"llm_calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "model": "n/a"}
    return (
        SQLGenerationOutput(sql=None, timing_ms=0.0, llm_stats=empty_stats),
        SQLValidationOutput(is_valid=False, validated_sql=None, error=None, timing_ms=0.0),
        SQLExecutionOutput(rows=[], row_count=0, timing_ms=0.0),
        AnswerGenerationOutput(answer="", timing_ms=0.0, llm_stats=empty_stats),
    )


def _merge_llm_stats(*stats_dicts):
    merged = {"llm_calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "model": "unknown"}
    for s in stats_dicts:
        merged["llm_calls"] += s.get("llm_calls", 0)
        merged["prompt_tokens"] += s.get("prompt_tokens", 0)
        merged["completion_tokens"] += s.get("completion_tokens", 0)
        merged["total_tokens"] += s.get("total_tokens", 0)
        if s.get("model") and s["model"] not in ("unknown", "n/a"):
            merged["model"] = s["model"]
    return merged


class AnalyticsPipeline:
    def __init__(self, db_path=DEFAULT_DB_PATH, llm_client=None, use_cache=True):
        self.db_path = Path(db_path)
        self.llm = llm_client or build_default_llm_client()
        self.executor = SQLiteExecutor(self.db_path)
        self.cache = ResultCache() if use_cache else None
        # SQL-level cache: same SQL query from different questions hits DB only once
        self.sql_cache = ResultCache(ttl_s=120, max_entries=128) if use_cache else None

    def run(self, question, request_id=None):
        request_id = request_id or new_request_id()
        try:
            return self._run(question, request_id)
        except Exception as exc:
            logger.exception("Unexpected pipeline failure request=%s", request_id)
            sql_gen, sql_val, sql_exec, answer = _empty_stage_outputs()
            answer.answer = "Something went wrong while processing your question. Please try again."
            answer.error = str(exc)
            return PipelineOutput(
                status="error", question=question, request_id=request_id,
                sql_generation=sql_gen, sql_validation=sql_val,
                sql_execution=sql_exec, answer_generation=answer,
                sql=None, rows=[], answer=answer.answer,
                timings={"sql_generation_ms": 0.0, "sql_validation_ms": 0.0, "sql_execution_ms": 0.0, "answer_generation_ms": 0.0, "total_ms": 0.0},
                total_llm_stats={"llm_calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "model": "n/a"},
            )

    def _run(self, question, request_id):
        start = time.perf_counter()
        log_metric("request_start", request_id=request_id, question=question)

        try:
            question = validate_question(question)
        except GuardrailError as exc:
            sql_gen, sql_val, sql_exec, answer = _empty_stage_outputs()
            answer.answer = f"I can't process this question: {exc}"
            total_ms = (time.perf_counter() - start) * 1000
            timings = {"sql_generation_ms": 0.0, "sql_validation_ms": 0.0, "sql_execution_ms": 0.0, "answer_generation_ms": 0.0, "total_ms": total_ms}
            empty_stats = {"llm_calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "model": "n/a"}
            log_metric("request_end", request_id=request_id, status="rejected", reason=str(exc))
            return PipelineOutput(
                status="error", question=question, request_id=request_id,
                sql_generation=sql_gen, sql_validation=sql_val, sql_execution=sql_exec, answer_generation=answer,
                sql=None, rows=[], answer=answer.answer, timings=timings, total_llm_stats=empty_stats,
            )

        # Question-level cache
        if self.cache is not None:
            cached = self.cache.get(question)
            if cached is not None:
                cache_timings = {"sql_generation_ms": 0.0, "sql_validation_ms": 0.0, "sql_execution_ms": 0.0, "answer_generation_ms": 0.0, "total_ms": (time.perf_counter() - start) * 1000}
                cache_stats = {"llm_calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "model": cached.total_llm_stats.get("model", "unknown")}
                result = replace(cached, request_id=request_id, timings=cache_timings, total_llm_stats=cache_stats)
                logger.info("request=%s cache=hit level=question", request_id)
                log_metric("request_end", request_id=request_id, status=result.status, cache_hit=True, cache_level="question")
                return result

        # Stage 1: generate SQL
        with trace_span(logger, request_id, "sql_generation"):
            sql_gen_output = self.llm.generate_sql(question, {})
        sql = sql_gen_output.sql

        # Stage 2: validate — if invalid, try once to self-correct
        with trace_span(logger, request_id, "sql_validation"):
            validation_output = SQLValidator.validate(sql, db_path=self.db_path)

        fix_output = None
        if not validation_output.is_valid and sql is not None:
            logger.info("request=%s validation failed, attempting self-correction", request_id)
            with trace_span(logger, request_id, "sql_correction"):
                fix_output = self.llm.fix_sql(question, sql, validation_output.error)
            if fix_output.sql:
                corrected_validation = SQLValidator.validate(fix_output.sql, db_path=self.db_path)
                if corrected_validation.is_valid:
                    logger.info("request=%s self-correction succeeded", request_id)
                    sql_gen_output = fix_output
                    validation_output = corrected_validation
                    sql = validation_output.validated_sql
                else:
                    logger.info("request=%s self-correction did not help", request_id)
                    sql = None
            else:
                sql = None

        if not validation_output.is_valid:
            sql = None
        elif validation_output.validated_sql and sql is not None:
            sql = enforce_row_limit(validation_output.validated_sql, self.executor.max_rows)

        # Stage 3: execute — check SQL-level cache first
        sql_cache_hit = False
        if sql is not None and self.sql_cache is not None:
            cached_exec = self.sql_cache.get(sql)
            if cached_exec is not None:
                execution_output = cached_exec
                sql_cache_hit = True
                logger.info("request=%s cache=hit level=sql", request_id)
            else:
                with trace_span(logger, request_id, "sql_execution"):
                    execution_output = self.executor.run(sql)
                if not execution_output.error:
                    self.sql_cache.set(sql, execution_output)
        else:
            with trace_span(logger, request_id, "sql_execution"):
                execution_output = self.executor.run(sql)

        # Stage 4: generate answer
        with trace_span(logger, request_id, "answer_generation"):
            answer_output = self.llm.generate_answer(question, sql, execution_output.rows)

        status = "success"
        if sql_gen_output.sql is None:
            status = "unanswerable"
        elif not validation_output.is_valid:
            status = "invalid_sql"
        elif execution_output.error:
            status = "error"
        elif sql is None:
            status = "unanswerable"

        timings = {
            "sql_generation_ms": sql_gen_output.timing_ms,
            "sql_validation_ms": validation_output.timing_ms,
            "sql_execution_ms": execution_output.timing_ms,
            "answer_generation_ms": answer_output.timing_ms,
            "total_ms": (time.perf_counter() - start) * 1000,
        }

        all_stats = [sql_gen_output.llm_stats, answer_output.llm_stats]
        if fix_output:
            all_stats.append(fix_output.llm_stats)
        total_llm_stats = _merge_llm_stats(*all_stats)

        log_metric("request_end", request_id=request_id, status=status, timings=timings,
                   llm_stats=total_llm_stats, sql_cache_hit=sql_cache_hit)

        result = PipelineOutput(
            status=status, question=question, request_id=request_id,
            sql_generation=sql_gen_output, sql_validation=validation_output,
            sql_execution=execution_output, answer_generation=answer_output,
            sql=sql, rows=execution_output.rows, answer=answer_output.answer,
            timings=timings, total_llm_stats=total_llm_stats,
        )

        if self.cache is not None and status == "success":
            self.cache.set(question, result)

        return result
