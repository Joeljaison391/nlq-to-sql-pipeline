from __future__ import annotations

import sqlite3
import time
from dataclasses import replace
from pathlib import Path

from src.cache import ResultCache
from src.graph import build_graph
from src.llm_client import build_default_llm_client
from src.observability import log_metric, new_request_id, setup_logging
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
            logger.error("sql execution failed: %s", exc)

        return SQLExecutionOutput(rows=rows, row_count=len(rows),
            timing_ms=(time.perf_counter() - start) * 1000, error=error)


def _empty_stage_outputs():
    empty_stats = {"llm_calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "model": "n/a"}
    return (
        SQLGenerationOutput(sql=None, timing_ms=0.0, llm_stats=empty_stats),
        SQLValidationOutput(is_valid=False, validated_sql=None, error=None, timing_ms=0.0),
        SQLExecutionOutput(rows=[], row_count=0, timing_ms=0.0),
        AnswerGenerationOutput(answer="", timing_ms=0.0, llm_stats=empty_stats),
    )


def _merge_stats(*dicts):
    out = {"llm_calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "model": "unknown"}
    for d in dicts:
        out["llm_calls"] += d.get("llm_calls", 0)
        out["prompt_tokens"] += d.get("prompt_tokens", 0)
        out["completion_tokens"] += d.get("completion_tokens", 0)
        out["total_tokens"] += d.get("total_tokens", 0)
        if d.get("model") and d["model"] not in ("unknown", "n/a"):
            out["model"] = d["model"]
    return out


class AnalyticsPipeline:
    def __init__(self, db_path=DEFAULT_DB_PATH, llm_client=None, use_cache=True):
        self.db_path = Path(db_path)
        self.llm = llm_client or build_default_llm_client()
        self.executor = SQLiteExecutor(self.db_path)
        self.cache = ResultCache() if use_cache else None
        self.sql_cache = ResultCache(ttl_s=120, max_entries=128) if use_cache else None
        self._graph = build_graph(self)

    def run(self, question, request_id=None, conversation_history=None):
        request_id = request_id or new_request_id()
        try:
            return self._run(question, request_id, conversation_history or [])
        except Exception as exc:
            logger.exception("unexpected failure request=%s", request_id)
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

    def _run(self, question, request_id, conversation_history):
        start = time.perf_counter()
        log_metric("request_start", request_id=request_id, question=question)

        initial_state = {
            "question": question,
            "request_id": request_id,
            "conversation_history": conversation_history,
            "linked_schema": None,
            "linked_columns": [],
            "sql": None,
            "validation_error": None,
            "correction_attempted": False,
            "judge_attempted": False,
            "sql_gen_output": None,
            "validation_output": None,
            "execution_output": None,
            "answer_output": None,
            "status": "success",
            "rejected": False,
            "cache_hit": False,
            "sql_cache_hit": False,
            "cached_result": None,
        }

        final = self._graph.invoke(initial_state)

        if final.get("cache_hit") and final.get("cached_result") is not None:
            cached = final["cached_result"]
            total_ms = (time.perf_counter() - start) * 1000
            result = replace(cached, request_id=request_id,
                timings={**cached.timings, "total_ms": total_ms},
                total_llm_stats={**cached.total_llm_stats, "llm_calls": 0, "prompt_tokens": 0,
                    "completion_tokens": 0, "total_tokens": 0})
            log_metric("request_end", request_id=request_id, status=result.status,
                cache_hit=True, cache_level="question")
            return result

        if final.get("rejected"):
            sql_gen, sql_val, sql_exec, answer_out = _empty_stage_outputs()
            msg = final.get("answer_output") or "Question was rejected by guardrails."
            answer_out.answer = f"I can't process this question: {msg}"
            total_ms = (time.perf_counter() - start) * 1000
            timings = {"sql_generation_ms": 0.0, "sql_validation_ms": 0.0,
                "sql_execution_ms": 0.0, "answer_generation_ms": 0.0, "total_ms": total_ms}
            empty_s = {"llm_calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "model": "n/a"}
            log_metric("request_end", request_id=request_id, status="error", reason="guardrail")
            return PipelineOutput(
                status="error", question=question, request_id=request_id,
                sql_generation=sql_gen, sql_validation=sql_val, sql_execution=sql_exec, answer_generation=answer_out,
                sql=None, rows=[], answer=answer_out.answer, timings=timings, total_llm_stats=empty_s,
            )

        sql_gen_out = final.get("sql_gen_output")
        val_out = final.get("validation_output")
        exec_out = final.get("execution_output")
        ans_out = final.get("answer_output")

        sql_gen, sql_val, sql_exec, answer_fallback = _empty_stage_outputs()
        if sql_gen_out is None: sql_gen_out = sql_gen
        if val_out is None: val_out = sql_val
        if exec_out is None: exec_out = sql_exec
        if ans_out is None: ans_out = answer_fallback

        sql = final.get("sql")
        rows = exec_out.rows if exec_out else []
        all_null_rows = bool(rows and all(v is None for row in rows for v in row.values()))

        status = "success"
        if sql_gen_out.sql is None:
            status = "unanswerable"
        elif val_out and not val_out.is_valid:
            status = "invalid_sql"
        elif exec_out and exec_out.error:
            status = "error"
        elif sql is None or all_null_rows:
            status = "unanswerable"

        total_ms = (time.perf_counter() - start) * 1000
        timings = {
            "sql_generation_ms": sql_gen_out.timing_ms,
            "sql_validation_ms": val_out.timing_ms if val_out else 0.0,
            "sql_execution_ms": exec_out.timing_ms if exec_out else 0.0,
            "answer_generation_ms": ans_out.timing_ms,
            "total_ms": total_ms,
        }
        total_llm_stats = _merge_stats(sql_gen_out.llm_stats, ans_out.llm_stats)

        log_metric("request_end", request_id=request_id, status=status, timings=timings,
                   llm_stats=total_llm_stats, sql_cache_hit=final.get("sql_cache_hit", False))

        result = PipelineOutput(
            status=status, question=question, request_id=request_id,
            sql_generation=sql_gen_out, sql_validation=val_out,
            sql_execution=exec_out, answer_generation=ans_out,
            sql=sql, rows=rows, answer=ans_out.answer,
            timings=timings, total_llm_stats=total_llm_stats,
        )

        if self.cache is not None and status == "success":
            self.cache.set(question, result)

        return result
