"""Run-scoped logging: human-readable console output plus a persistent per-run log file."""

from __future__ import annotations

import logging
import sys
from datetime import datetime, timezone
from pathlib import Path


def new_run_id() -> str:
    now = datetime.now(timezone.utc)
    return f"{now:%Y%m%dT%H%M%S}{now.microsecond // 1000:03d}Z"


def setup_logging(log_dir: Path, run_id: str) -> Path:
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / f"run_{run_id}.log"
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s | %(message)s", "%Y-%m-%d %H:%M:%S")
    root = logging.getLogger("wavpipe")
    root.setLevel(logging.INFO)
    root.handlers.clear()
    for handler in (logging.StreamHandler(sys.stderr), logging.FileHandler(log_file)):
        handler.setFormatter(fmt)
        root.addHandler(handler)
    root.propagate = False
    return log_file
