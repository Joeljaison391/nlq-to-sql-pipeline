# Solution Notes

## What I changed and why

1. **Added the real table schema to the SQL prompt** (`src/schema.py`).
   The original code was passing an empty dict as context, so the model had
   no idea what columns existed and was just guessing names. This was the
   main reason queries were failing. I created a schema file with all 39
   column names and descriptions, and injected it into the system prompt.

2. **Fixed a bug where the model always returned nothing.** The model
   `openai/gpt-5-nano` uses reasoning tokens internally before writing the
   answer. The original `max_tokens` was too low (~200), so all the tokens
   got used up on internal thinking and the actual response came back empty.
   I added `reasoning_effort="minimal"` and raised `max_tokens` to 300 to
   fix this.

3. **Implemented real token counting** (`src/llm_client.py`). The original
   code had a TODO here. The API response already includes exact token counts
   in a `usage` field - I just read those instead of guessing.

4. **Built a real SQL validator** (`src/validation.py`). The original
   validator accepted everything without checking anything. I added checks
   for: SELECT-only queries, no dangerous keywords like DELETE/DROP, no
   stacked statements, must reference the right table, no made-up column
   names, and a SQLite EXPLAIN check for syntax errors. The database
   connection is also opened read-only as a second safety layer.

5. **Added logging, metrics, and tracing** (`src/observability.py`). There
   was no way to debug a failure before. Now every request logs each stage
   with timing, and all metrics are written to `logs/metrics.jsonl` so I
   can check what happened after the fact.

6. **Added retries** for LLM calls (`src/llm_client.py`). Free-tier models
   sometimes return timeout or rate-limit errors that go away if you just
   try again. I added up to 2 retries with a short wait between attempts.

7. **Centralized all config** (`src/config.py`). API key, model name, paths
   - all in one place loaded from a `.env` file instead of scattered
   `os.getenv()` calls across multiple files.

8. **Built a Streamlit UI** (`streamlit_app.py`). Makes the whole thing
   usable as an actual product - type a question, get an answer, expand
   sections to see the SQL and performance numbers.

9. **Fixed a crash in `scripts/benchmark.py`**. It was using
   `result["status"]` on a dataclass, which doesn't work like a dict.
   Changed to `result.status`.

10. **Added unit tests** for the new modules. All 30 run without any network
    access or API key in under a second.

11. **Added input guardrails** (`src/guardrails.py`). Rejects empty questions,
    questions that are too long, and anything that looks like a prompt
    injection attempt - before wasting an LLM call on it.

12. **Added a query execution timeout** (`src/pipeline.py`). Uses SQLite's
    built-in progress handler to stop any query that runs longer than 8
    seconds, so one slow query can't hang the whole app.

13. **Added a result cache** (`src/cache.py`). If someone asks the same
    question twice, the second time is served instantly from cache with
    zero LLM calls. This cut average latency from ~3.3s to ~1.4s in
    benchmarks where questions repeat.

14. **Made `pipeline.run()` crash-proof**. Wrapped the whole pipeline in a
    top-level try/except so it always returns a clean response object even
    if something unexpected breaks inside.

## Results

The baseline had a 0% success rate - it was intentionally left incomplete for this task.
After:

- All 5 original integration tests pass
- 30 new unit tests pass, all under 1 second with no network needed
- Benchmark (36 samples, no cache): ~92-97% success, avg latency ~3.3s
- Benchmark (with cache): 94.4% success, avg latency ~1.4s, avg tokens
  per request ~417 (down from ~1071 on cache misses - ~61% reduction)

## What I would improve with more time

- The column name check uses regex, not a real SQL parser. It works for
  normal queries but a proper parser like `sqlglot` would be more reliable.
- The cache is in-memory and only works in one process. For a multi-server
  deployment I'd move it to Redis.
- Multi-turn is implemented but implicit follow-ups ("same but for males") don't
  always work reliably. The model gets the last 3 turns as context but doesn't
  consistently use the prior SQL as a reference point for modifications.
- No rate limiting on the Streamlit app.
- The prompt injection check only catches a few obvious patterns - not a
  complete solution for a real production app.
