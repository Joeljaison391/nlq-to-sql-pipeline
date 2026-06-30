from __future__ import annotations

import difflib
import json
from typing import Any, TypedDict

from langgraph.graph import StateGraph, START, END

from src.guardrails import GuardrailError, check_destructive_intent, enforce_row_limit, validate_question
from src.observability import setup_logging, trace_span
from src.validation import SQLValidator
from src.schema import ALLOWED_COLUMNS, COLUMNS, TABLE_NAME

logger = setup_logging()
_COLUMN_LIST = "\n".join(f"- {name}: {desc}" for name, (_, desc) in COLUMNS.items())


class PipelineState(TypedDict):
    question: str
    request_id: str
    conversation_history: list
    linked_schema: str | None
    linked_columns: list
    sql: str | None
    validation_error: str | None
    correction_attempted: bool
    judge_attempted: bool

    sql_gen_output: Any
    validation_output: Any
    execution_output: Any
    answer_output: Any

    status: str
    rejected: bool
    cache_hit: bool
    sql_cache_hit: bool
    cached_result: Any


def compress_schema(question: str) -> str:
    from src.schema import compact_schema_text
    q = question.lower()
    relevant = []
    for name, (sqltype, desc) in COLUMNS.items():
        words = desc.lower().split() + [name]
        if any(w in q for w in words):
            relevant.append(f"- {name} ({sqltype}): {desc}")
    if len(relevant) < 8:
        return compact_schema_text()
    return f"Table: {TABLE_NAME}\n" + "\n".join(relevant)


def fuzzy_suggest(unknown_col: str) -> str | None:
    matches = difflib.get_close_matches(unknown_col.lower(), ALLOWED_COLUMNS, n=1, cutoff=0.6)
    return matches[0] if matches else None


def build_graph(pipeline_self):
    llm = pipeline_self.llm
    executor = pipeline_self.executor
    cache = pipeline_self.cache
    sql_cache = pipeline_self.sql_cache

    def node_guardrail(state: PipelineState) -> dict:
        try:
            cleaned = validate_question(state["question"])
        except GuardrailError as e:
            return {"rejected": True, "status": "error", "answer_output": str(e)}

        if check_destructive_intent(cleaned):
            from src.types import SQLGenerationOutput, SQLValidationOutput
            fake_sql = "DELETE FROM gaming_mental_health"
            fake_gen = SQLGenerationOutput(sql=fake_sql, timing_ms=0.0,
                llm_stats={"llm_calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "model": "n/a"})
            fake_val = SQLValidationOutput(is_valid=False, validated_sql=None,
                error="Destructive operations are not permitted.", timing_ms=0.0)
            return {
                "question": cleaned, "rejected": False,
                "sql": fake_sql, "sql_gen_output": fake_gen, "validation_output": fake_val,
                "correction_attempted": True, "judge_attempted": True,
                "cache_hit": False, "execution_output": None,
            }
        return {"question": cleaned, "rejected": False}

    def node_cache_check(state: PipelineState) -> dict:
        if cache is None:
            return {"cache_hit": False, "cached_result": None}
        cached = cache.get(state["question"])
        if cached is not None:
            return {"cache_hit": True, "cached_result": cached}
        return {"cache_hit": False, "cached_result": None}

    def node_schema_link(state: PipelineState) -> dict:
        system = (
            "You are a schema linking assistant. Given a user question and a list of database columns, "
            "identify ONLY the columns needed to answer the question.\n\n"
            f"Available columns:\n{_COLUMN_LIST}\n\n"
            "Respond with JSON: {\"columns\": [\"col1\", \"col2\", ...]}\n"
            "If the question cannot be answered with any column, respond: {\"columns\": []}"
        )
        user = f"Question: {state['question']}"
        cols = []

        try:
            with trace_span(logger, state["request_id"], "schema_linking"):
                raw = llm._chat(
                    messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                    temperature=0.0, max_tokens=150,
                )
            parsed = json.loads(raw.strip().strip("`").lstrip("json").strip())
            cols = [c for c in parsed.get("columns", []) if c in ALLOWED_COLUMNS]
        except Exception as exc:
            logger.warning("schema linker failed, falling back to full schema: %s", exc)

        from src.schema import compact_schema_text
        if not cols:
            schema = compact_schema_text()
        else:
            lines = [f"Table: {TABLE_NAME}"]
            for name, (sqltype, desc) in COLUMNS.items():
                if name in cols:
                    lines.append(f"- {name} ({sqltype}): {desc}")
            schema = "\n".join(lines)
            logger.info("request=%s schema_link narrowed to %d cols", state["request_id"], len(cols))

        return {"linked_schema": schema, "linked_columns": cols}

    def node_generate_sql(state: PipelineState) -> dict:
        schema = state.get("linked_schema") or compress_schema(state["question"])
        history = state.get("conversation_history", [])
        with trace_span(logger, state["request_id"], "sql_generation"):
            out = llm.generate_sql_with_context(state["question"], schema, history)
        return {"sql": out.sql, "sql_gen_output": out, "correction_attempted": False, "judge_attempted": False}

    def node_validate_sql(state: PipelineState) -> dict:
        sql = state.get("sql")
        with trace_span(logger, state["request_id"], "sql_validation"):
            result = SQLValidator.validate(sql, db_path=executor.db_path)
        error = result.error
        if error and not result.is_valid:
            parts = error.split("unknown column(s):")
            if len(parts) == 2:
                unknown = parts[1].strip().split(",")[0].strip()
                suggestion = fuzzy_suggest(unknown)
                if suggestion:
                    error = f"{error} (did you mean '{suggestion}'?)"
        return {"validation_output": result, "validation_error": error}

    def node_judge_sql(state: PipelineState) -> dict:
        system = (
            "You are a SQL review assistant. Your job is to check if a SQL query correctly answers the user's question "
            "using the right columns from the schema.\n\n"
            f"Available columns:\n{_COLUMN_LIST}\n\n"
            "Respond with JSON: {\"correct\": true} if the SQL is semantically correct.\n"
            "Or: {\"correct\": false, \"issue\": \"<brief reason>\", \"fix\": \"<corrected SQL>\"} if not.\n"
            "Only flag semantic issues (wrong column chosen). Ignore style or performance."
        )
        user = f"Question: {state['question']}\n\nSQL:\n{state.get('sql')}"

        try:
            with trace_span(logger, state["request_id"], "sql_judge"):
                raw = llm._chat(
                    messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                    temperature=0.0, max_tokens=300,
                )
            text = raw.strip().strip("`").lstrip("json").strip()
            verdict = json.loads(text)
        except Exception as exc:
            logger.warning("judge couldnt parse response, keeping sql as-is: %s", exc)
            return {"judge_attempted": True}

        if verdict.get("correct") is False:
            logger.info("request=%s judge flagged issue: %s", state["request_id"], verdict.get("issue", ""))
            fixed_sql = (verdict.get("fix") or "").strip().rstrip(";")

            if fixed_sql:
                check = SQLValidator.validate(fixed_sql, db_path=executor.db_path)
                if check.is_valid:
                    from src.types import SQLGenerationOutput
                    out = SQLGenerationOutput(
                        sql=fixed_sql, timing_ms=0.0,
                        llm_stats={"llm_calls": 1, "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "model": "judge"},
                    )
                    logger.info("request=%s judge fix accepted", state["request_id"])
                    return {"sql": fixed_sql, "sql_gen_output": out, "judge_attempted": True}

            logger.info("request=%s judge rejected sql, no valid fix available", state["request_id"])
            from src.types import SQLValidationOutput
            bad_val = SQLValidationOutput(is_valid=False, validated_sql=None,
                error=f"Semantic check failed: {verdict.get('issue', 'wrong columns used')}",
                timing_ms=0.0)
            return {"sql": None, "validation_output": bad_val, "judge_attempted": True}

        return {"judge_attempted": True}

    def node_fix_sql(state: PipelineState) -> dict:
        with trace_span(logger, state["request_id"], "sql_correction"):
            fix = llm.fix_sql(state["question"], state["sql"], state["validation_error"])
        return {"sql": fix.sql, "sql_gen_output": fix, "correction_attempted": True}

    def node_execute_sql(state: PipelineState) -> dict:
        val = state["validation_output"]
        sql = enforce_row_limit(val.validated_sql, executor.max_rows) if val.validated_sql else None

        if sql and sql_cache is not None:
            cached_exec = sql_cache.get(sql)
            if cached_exec is not None:
                logger.info("request=%s sql_cache=hit", state["request_id"])
                return {"execution_output": cached_exec, "sql": sql, "sql_cache_hit": True}

        with trace_span(logger, state["request_id"], "sql_execution"):
            out = executor.run(sql)
        if sql and sql_cache is not None and not out.error:
            sql_cache.set(sql, out)
        if out.rows and all(v is None for row in out.rows for v in row.values()):
            return {"execution_output": out, "sql": None, "sql_cache_hit": False}
        return {"execution_output": out, "sql": sql, "sql_cache_hit": False}

    def node_generate_answer(state: PipelineState) -> dict:
        val = state.get("validation_output")
        sql = state.get("sql")
        if val and not val.is_valid:
            sql = None
        rows = state["execution_output"].rows if state.get("execution_output") else []
        with trace_span(logger, state["request_id"], "answer_generation"):
            out = llm.generate_answer(state["question"], sql, rows)
        return {"answer_output": out}

    def route_guardrail(state: PipelineState) -> str:
        if state.get("rejected"):
            return "rejected"
        if state.get("validation_output") is not None:
            return "destructive"
        return "ok"

    def route_cache(state: PipelineState) -> str:
        return "hit" if state.get("cache_hit") else "miss"

    def route_validation(state: PipelineState) -> str:
        val = state.get("validation_output")
        if val and val.is_valid:
            return "valid"
        if not state.get("correction_attempted") and state.get("sql"):
            return "fix"
        return "give_up"

    g = StateGraph(PipelineState)
    g.add_node("guardrail", node_guardrail)
    g.add_node("cache_check", node_cache_check)
    g.add_node("schema_link", node_schema_link)
    g.add_node("generate_sql", node_generate_sql)
    g.add_node("validate_sql", node_validate_sql)
    g.add_node("judge_sql", node_judge_sql)
    g.add_node("fix_sql", node_fix_sql)
    g.add_node("execute_sql", node_execute_sql)
    g.add_node("generate_answer", node_generate_answer)

    g.add_edge(START, "guardrail")
    g.add_conditional_edges("guardrail", route_guardrail, {"ok": "cache_check", "rejected": END, "destructive": "generate_answer"})
    g.add_conditional_edges("cache_check", route_cache, {"hit": END, "miss": "schema_link"})
    g.add_edge("schema_link", "generate_sql")
    g.add_edge("generate_sql", "validate_sql")
    g.add_conditional_edges("validate_sql", route_validation, {
        "valid": "judge_sql",
        "fix": "fix_sql",
        "give_up": "generate_answer",
    })
    g.add_edge("judge_sql", "execute_sql")
    g.add_edge("fix_sql", "validate_sql")
    g.add_edge("execute_sql", "generate_answer")
    g.add_edge("generate_answer", END)

    return g.compile()
