from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import duckdb
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fixtures import MONTH, build_rows, pad_to_multiple_of_days, standard_session  # noqa: E402
from wavpipe.config import Config, Month, load_config  # noqa: E402
from wavpipe.pipeline import run  # noqa: E402


@pytest.fixture
def cfg(tmp_path: Path) -> Config:
    # Same production config, isolated paths; tiny cells so the 20-row synthetic WAV month has judged cells;
    # zero backoff so retry tests run instantly.
    return load_config(root=tmp_path, overrides={"kpi.min_cell_requests": 5, "http.backoff_s": 0})


@pytest.fixture
def rows() -> list[dict[str, Any]]:
    return pad_to_multiple_of_days(build_rows())


@pytest.fixture
def session(rows: list[dict[str, Any]], tmp_path: Path):
    return standard_session(rows, tmp_path)


@pytest.fixture
def run_month(cfg: Config, session):
    def _run(**kw: Any) -> dict[str, Any]:
        kw.setdefault("session", session)
        return run([Month.parse(MONTH)], cfg, **kw)
    return _run


def query(cfg: Config, sql: str, params: list[Any] | None = None) -> list[tuple]:
    con = duckdb.connect(str(cfg.path("warehouse")), read_only=True)
    try:
        return con.execute(sql, params or []).fetchall()
    finally:
        con.close()
