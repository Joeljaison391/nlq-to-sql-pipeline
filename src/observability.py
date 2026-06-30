from __future__ import annotations

import json
import logging
import sys
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

PROJECT_ROOT = Path(__file__).resolve().parents[1]
LOG_DIR = PROJECT_ROOT / "logs"
METRICS_PATH = LOG_DIR / "metrics.jsonl"

_configured = False


def setup_logging(level="INFO"):
    global _configured
    logger = logging.getLogger("analytics_pipeline")
    if _configured:
        return logger

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    logger.setLevel(level)
    fmt = logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s")

    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    logger.addHandler(sh)

    fh = logging.FileHandler(LOG_DIR / "pipeline.log", encoding="utf-8")
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    _configured = True
    return logger


def new_request_id():
    return uuid.uuid4().hex[:12]


def log_metric(event: str, **fields: Any):
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    record = {"event": event, "ts": time.time(), **fields}
    with open(METRICS_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, default=str) + "\n")


@contextmanager
def trace_span(logger, request_id: str, stage: str) -> Iterator[None]:
    start = time.perf_counter()
    logger.info("request=%s stage=%s status=start", request_id, stage)
    try:
        yield
    except Exception:
        ms = (time.perf_counter() - start) * 1000
        logger.exception("request=%s stage=%s status=error duration_ms=%.1f", request_id, stage, ms)
        raise
    else:
        ms = (time.perf_counter() - start) * 1000
        logger.info("request=%s stage=%s status=ok duration_ms=%.1f", request_id, stage, ms)
