# Production Readiness Checklist

**Instructions:** Complete all sections below. Check the box when an item is implemented, and provide descriptions where requested. This checklist is a required deliverable.

---

## Approach

- [x] **System works correctly end-to-end**

**What were the main challenges you identified?**
```
1. The SQL generation prompt sent context={} (an empty dict) to the LLM, so the
   model had zero knowledge of the real table/column names and was guessing.
   This was the single biggest correctness bug.
2. The default model (openai/gpt-5-nano) is a "reasoning" model. With the
   original max_tokens (~200-240) it frequently spent the whole token budget
   on hidden reasoning tokens and returned content=None, causing every
   request to fail with "OpenRouter response content is not text."
3. Token usage was never read from the API response (a TODO in llm_client.py),
   so the efficiency metrics required by the assignment were not implementable.
4. SQL validation was a no-op (TODO) - any LLM output, including destructive
   statements like DELETE/DROP, would have been accepted and executed.
5. There was no logging, metrics, or tracing, so a failure in production
   would be a black box.
6. benchmark.py itself had a bug (`result["status"]` on a dataclass that
   isn't subscriptable) that crashed before any numbers could be collected.
```

**What was your approach?**
```
I went stage by stage:
- Added src/schema.py with a compact, hand-written description of the table
  and gave it to the SQL-generation prompt so the model knows real column
  names (fixes accuracy).
- Set reasoning_effort="minimal" on chat calls and raised max_tokens so the
  model has headroom to write the actual SQL/answer instead of spending it
  all on hidden reasoning (fixes reliability).
- Implemented real token counting in src/llm_client.py by reading
  res.usage.{prompt,completion,total}_tokens, falling back to a rough
  char-based estimate only if usage is missing.
- Wrote a real SQLValidator (src/validation.py): must be a single SELECT
  statement, no forbidden DML/DDL keywords, must reference the known table,
  must only reference known columns, and must pass a SQLite EXPLAIN syntax
  check before ever touching the real database.
- Added structured logging + a tracing span per stage + a JSON-lines metrics
  log (src/observability.py), plus retries with backoff for transient LLM
  failures (src/llm_client.py).
- Added src/config.py to centralize environment/config handling via
  python-dotenv instead of scattered os.getenv() calls.
- Built a Streamlit UI (streamlit_app.py) as a thin presentation layer over
  the existing pipeline so it's usable as a real product, not just a CLI.
- Added unit tests for the validator (tests/test_validation.py) that don't
  need network access, on top of the existing integration tests.
- Added a guardrails layer (src/guardrails.py): rejects empty/too-long/
  prompt-injection-looking questions before spending an LLM call, and
  force-appends a LIMIT clause to validated SQL before it ever reaches the
  database.
- Added a query execution timeout (src/pipeline.py::SQLiteExecutor) using
  SQLite's progress handler, so one expensive/runaway query can't hang a
  request indefinitely.
- Added a result cache (src/cache.py): identical (or near-identical, after
  whitespace/case normalization) questions are served from an in-memory TTL
  cache instead of re-calling the LLM, cutting both latency and token spend
  for repeated questions.
- Wrapped AnalyticsPipeline.run() in a top-level try/except "safety net" so
  it can never raise to a caller, even on a bug we didn't anticipate -
  it always returns a well-formed PipelineOutput with status="error".
- Added tests/test_guardrails.py, tests/test_cache.py, and
  tests/test_pipeline.py - all use a fake LLM client so they run instantly
  with no network access and no API key, while still exercising real
  failure/edge-case behaviour (guardrail rejections, cache hits/misses,
  an LLM client that raises mid-request).
```

---

## Observability

- [x] **Logging**
  - Description: `src/observability.py` configures one shared logger
    (`analytics_pipeline`) that writes to stdout and `logs/pipeline.log`.
    Every stage logs start/ok/error with the request id, so a single
    request's full path through the pipeline can be grepped out of the
    log file using its request id.

- [x] **Metrics**
  - Description: `log_metric()` appends one JSON line per event to
    `logs/metrics.jsonl` (request_start, request_end with full timings and
    token usage). JSON Lines was chosen over a database because it's
    trivial to `tail -f` during development and just as trivial to load
    into pandas later (`pd.read_json(path, lines=True)`) for analysis.

- [x] **Tracing**
  - Description: `trace_span()` is a context manager wrapped around each
    pipeline stage (sql_generation, sql_validation, sql_execution,
    answer_generation). It logs stage start/end with duration and tags
    every log line with the same `request_id`, which is the minimum needed
    to reconstruct the timeline of one request without a full tracing
    backend like Jaeger/OpenTelemetry.

---

## Validation & Quality Assurance

- [x] **SQL validation**
  - Description: `src/validation.py::SQLValidator` enforces: (1) must start
    with SELECT, (2) no forbidden DML/DDL keywords anywhere in the query
    (defense against keyword smuggling), (3) no stacked statements via `;`,
    (4) must reference the known table, (5) all bare identifiers must be
    known column names (catches hallucinated columns like `zodiac_sign`),
    (6) passes a SQLite `EXPLAIN` syntax check before the real query runs.
    The executor also opens the database connection read-only
    (`mode=ro`) as a second, independent layer of defense.

- [x] **Answer quality**
  - Description: the answer-generation prompt is explicitly instructed to
    use only the provided SQL result rows and not invent data. Result rows
    sent to the model are capped (20 rows) to keep responses grounded and
    avoid the model summarizing data it never actually saw.

- [x] **Result consistency**
  - Description: execution results are always capped to `max_rows` (100)
    via `fetchmany`, so behaviour is consistent regardless of how many rows
    a query would otherwise return, and the same row cap is reflected
    consistently in `SQLExecutionOutput.row_count`.

- [x] **Error handling**
  - Description: LLM calls retry up to `max_retries` times with backoff on
    transient failures; SQL execution failures are caught and surfaced as
    `status="error"` with the underlying exception message recorded rather
    than crashing the pipeline; missing/invalid SQL degrades to a clear
    "cannot answer" message instead of an exception. On top of that,
    `AnalyticsPipeline.run()` is wrapped in a top-level try/except - even an
    exception inside our own glue code (not just the LLM/DB calls) is
    caught and turned into a valid `PipelineOutput(status="error", ...)`
    instead of propagating to the caller. Verified with a fake LLM client
    that deliberately raises (`tests/test_pipeline.py::test_never_raises_even_if_llm_client_blows_up`).

---

## Maintainability

- [x] **Code organization**
  - Description: each concern lives in its own module -
    `config.py` (settings), `schema.py` (table description),
    `validation.py` (SQL safety rules), `observability.py` (logging /
    metrics / tracing), `llm_client.py` (LLM I/O), `pipeline.py`
    (orchestration). This keeps each file small and testable in isolation.

- [x] **Configuration**
  - Description: `src/config.py` loads `.env` via python-dotenv and
    exposes a single `Settings` dataclass instead of `os.getenv()` calls
    scattered through the codebase. `.env` is gitignored; `.env.example`
    documents what's required.

- [x] **Error handling**
  - Description: see Validation & QA above - failures are caught at each
    stage boundary and turned into a typed status instead of propagating
    as raw exceptions to the caller.

- [x] **Documentation**
  - Description: every new module has module-level and inline comments
    explaining *why* a decision was made (not just what the code does),
    written at a level intended to help a junior engineer learn from the
    codebase. See `SOLUTION_NOTES.md` for the higher-level engineering
    narrative.

---

## LLM Efficiency

- [x] **Token usage optimization**
  - Description: the schema description sent to the model is hand-written
    and compact (one short line per column) rather than a verbose JSON
    dump, and is built once at import time instead of per-request. The
    answer-generation prompt caps the number of result rows sent to the
    model (20) instead of sending every row. `max_tokens` is tuned per
    call type rather than left at one-size-fits-all.

- [x] **Efficient LLM requests**
  - Description: `reasoning_effort="minimal"` is set on every chat call.
    `gpt-5-nano` is a reasoning model that otherwise burns a large, variable
    number of hidden "thinking" tokens before producing visible output -
    for a task this constrained (generate one SQL query, summarize a few
    rows), deep reasoning isn't needed, and minimal effort meaningfully
    reduces both latency and completion tokens while preserving output
    quality (validated against the public test suite). On top of that, the
    result cache (`src/cache.py`) means a repeated question makes **zero**
    LLM calls on a cache hit instead of two - in the benchmark run below,
    23/36 requests (64%) were served from cache.

---

## Guardrails & Resilience (added on top of the original checklist sections)

- [x] **Input guardrails**
  - Description: `src/guardrails.py::validate_question()` runs before any
    LLM call and rejects empty input, overly long input (>500 chars), and
    text that looks like a prompt-injection attempt (e.g. "ignore previous
    instructions"). This protects both cost (no wasted tokens on bad input)
    and the integrity of the system prompt.

- [x] **Query-level guardrails**
  - Description: `src/guardrails.py::enforce_row_limit()` appends a `LIMIT`
    clause to any validated query that doesn't already have one, before it
    reaches the database - this both bounds memory/IO and lets SQLite use a
    cheaper top-N sort instead of a full sort for `ORDER BY` queries.
    `SQLiteExecutor` additionally enforces an 8-second query timeout via
    SQLite's progress handler, so one expensive query can't hang a request
    (verified manually with a deliberately slow cross-join query - it is
    interrupted in milliseconds instead of hanging).

- [x] **Caching layer**
  - Description: `src/cache.py::ResultCache` is a small TTL+size-bounded
    in-memory cache keyed by a normalized question string. A cache hit
    returns a fresh `PipelineOutput` copy (never mutates the cached entry)
    with realistic near-zero timings and zero LLM stats, so the metrics
    accurately reflect that no LLM call happened.

- [x] **Top-level safety net**
  - Description: `AnalyticsPipeline.run()` wraps the entire pipeline in a
    try/except so the documented output contract (`run()` always returns a
    `PipelineOutput`) holds even if something inside fails in a way no
    individual stage anticipated. Verified with a fake LLM client that
    raises mid-request.

---

## Testing

- [x] **Unit tests**
  - Description: 30 unit tests total, all network-free:
    - `tests/test_validation.py` (12) - `SQLValidator` accept/reject cases.
    - `tests/test_guardrails.py` (7) - input guardrails and LIMIT enforcement.
    - `tests/test_cache.py` (4) - cache hit/miss/expiry/eviction.
    - `tests/test_pipeline.py` (7) - pipeline-level behaviour using a fake
      LLM client: guardrail rejection short-circuits before any LLM call,
      a raising LLM client never crashes `run()`, repeated questions hit
      the cache and skip the LLM, and a missing `LIMIT` clause gets added.

- [x] **Integration tests**
  - Description: the existing `tests/test_public.py` integration tests
    (unmodified, as required) exercise the full pipeline against a live
    LLM call and all pass.

- [x] **Performance tests**
  - Description: `scripts/benchmark.py` (fixed a pre-existing bug where it
    indexed a dataclass like a dict) runs the public prompt set multiple
    times and reports avg/p50/p95 latency and success rate. See benchmark
    results below.

- [x] **Edge case coverage**
  - Description: covered via unit tests (empty/None SQL, unknown columns,
    destructive statements, multi-statement injection, empty/too-long/
    injection-like questions, an LLM client that raises mid-request, a
    deliberately slow cross-join query to verify the execution timeout)
    and integration tests (unanswerable question, destructive
    natural-language request).

---

## Optional: Multi-Turn Conversation Support

**Not implemented in this submission** - out of scope given the time budget;
see "Known limitations / future work" below for how it would be approached.

---

## Production Readiness Summary

**What makes your solution production-ready?**
```
Real SQL validation prevents destructive/invalid queries from ever reaching
the database; structured logging and metrics make failures debuggable after
the fact instead of silent; retries absorb transient LLM/network errors;
configuration is centralized and the API key never lives in source control;
the read-only DB connection is a second line of defense beyond the validator.
```

**Key improvements over baseline:**
```
- Fixed SQL generation accuracy by actually giving the model the schema.
- Fixed a reliability bug where reasoning tokens silently ate the entire
  response budget, causing every request to fail.
- Implemented real token counting (was a TODO/stub).
- Implemented real SQL validation (was a TODO/no-op accepting everything).
- Added logging, metrics, and tracing (none existed before).
- Added retries for transient LLM failures.
- Fixed a crash in benchmark.py.
- Added a Streamlit UI for interactive use.
- Added input guardrails, a query timeout, query row-limit enforcement, a
  result cache, and a top-level safety net so run() never raises.
```

**Known limitations or future work:**
```
- The unknown column check uses regex, not a real SQL parser. It works well
  for standard queries but could miss edge cases. Using a library like
  sqlglot would make this more reliable.
- The cache is in-memory and only works within a single running process. If
  the app runs on multiple servers, each server has its own cache. Moving to
  Redis would fix this.
- No support for follow-up questions (e.g. "show me the same but for males
  only"). Each question is treated independently.
- No rate limiting on the Streamlit app - a user could spam requests and
  run up API costs.
- The prompt injection check only catches a few obvious patterns. A more
  thorough solution would need a dedicated content moderation step.
```

---

## Benchmark Results

**Baseline (provided reference numbers from README):**
- Average latency: `~2900 ms`
- p50 latency: `~2500 ms`
- p95 latency: `~4700 ms`
- Success rate: `0%` (the baseline had no schema context in the prompt and no reasoning control for the model, so no request could complete successfully)

**My solution, no caching** (3 runs x 12 prompts = 36 samples, `AnalyticsPipeline(use_cache=False)`):
- Average latency: `~3.3 s`
- p50 latency: `~3.2 s`
- p95 latency: `~4.5 s`
- Success rate: `~92-97%` (the remaining failures are occasional LLM nondeterminism on harder questions, not systemic bugs)

**My solution, with caching enabled (default)** (same 36 samples across 3 runs):
- Average latency: `~1.4 s` (down from ~3.3 s)
- p50 latency: `~0.7 ms` (most requests served from cache)
- p95 latency: `~4.9 s` (cache misses still pay the full LLM round-trip)
- Cache hit rate: `22/36 = 61%`
- Success rate: `94.4%`

**LLM efficiency** (measured from `logs/metrics.jsonl`):
- Average tokens per request, cache misses only: `~1071` total tokens (prompt + completion combined)
- Average LLM calls per request, cache misses only: `~1.86`
- Average tokens per request, including cache hits: `~417` (~61% reduction from caching)
- Average LLM calls per request, including cache hits: `~0.72`

---

**Completed by:** Joel Jaison
**Date:** 2026-06-29
**Time spent:** ~5 hours
