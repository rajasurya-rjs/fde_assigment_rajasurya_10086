"""End-to-end dependability: reruns, offline rebuilds, failure isolation, last-known-good protection."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from conftest import query
from wavpipe.config import Month
from wavpipe.pipeline import run


def snapshot(outputs: Path) -> dict[str, bytes]:
    files = (list((outputs / "metrics").glob("*.csv")) + list((outputs / "validation").rglob("*.csv"))
             + list((outputs / "profile").glob("*.csv")) + [outputs / "scorecard.md"])
    return {str(p.relative_to(outputs)): p.read_bytes() for p in sorted(files)}


class ExplodingSession:
    """Any network call fails the test: proves --offline really is offline."""

    def get(self, *a, **k):
        raise AssertionError("network used in offline mode")

    head = get


def test_rerun_is_idempotent(cfg, run_month):
    run_month()
    first = snapshot(cfg.path("outputs"))
    counts = query(cfg, "SELECT (SELECT count(*) FROM fct_wav_trip), (SELECT count(*) FROM agg_segment_month)")
    run_month()
    assert snapshot(cfg.path("outputs")) == first, "same inputs must give byte-identical outputs"
    assert query(cfg, "SELECT (SELECT count(*) FROM fct_wav_trip), (SELECT count(*) FROM agg_segment_month)") == counts
    assert query(cfg, "SELECT count(*), count(DISTINCT run_id) FROM pipeline_run") == [(2, 2)]


def test_offline_rebuild_from_preserved_raw_reproduces_outputs(cfg, run_month):
    run_month()
    first = snapshot(cfg.path("outputs"))
    cfg.path("warehouse").unlink()
    shutil.rmtree(cfg.path("outputs"))
    summary = run([Month.parse("2026-02")], cfg, offline=True, session=ExplodingSession())
    assert summary["status"] == "OK"
    assert snapshot(cfg.path("outputs")) == first


def test_failed_rerun_keeps_last_known_good_month(cfg, run_month):
    run_month()
    good = snapshot(cfg.path("outputs"))
    raw = cfg.path("raw") / "tlc_trips" / "fhvhv_tripdata_2026-02.parquet"
    with open(raw, "ab") as fh:
        fh.write(b"corruption")
    summary = run_month()
    assert summary["months"]["2026-02"]["error_type"] == "RawIntegrityError"
    assert query(cfg, "SELECT count(*) FROM month_status WHERE month = '2026-02'") == [(1,)]
    assert snapshot(cfg.path("outputs")) == good
    assert query(cfg, "SELECT status FROM pipeline_run ORDER BY rowid") == [("PUBLISHED",), ("FAILED",)]


def test_one_failing_month_does_not_block_another(cfg, session):
    session.status["https://d37ci6vzurychx.cloudfront.net/trip-data/fhvhv_tripdata_2026-03.parquet"] = 404
    summary = run([Month.parse("2026-02"), Month.parse("2026-03")], cfg, session=session)
    assert summary["status"] == "PARTIAL"
    assert summary["months"]["2026-02"]["status"] == "PUBLISHED"
    assert summary["months"]["2026-03"]["error_type"] == "RetrievalError"
    assert (cfg.path("outputs") / "scorecard.md").exists()


def test_refused_month_leaves_evidence_but_publishes_nothing(cfg, rows, session):
    from fixtures import base_aggregate_rows
    session.soda_rows = base_aggregate_rows(rows, uber_delta=+50)
    summary = run([Month.parse("2026-02")], cfg, session=session)
    assert summary["months"]["2026-02"]["error_type"] == "ReconciliationError"
    out = cfg.path("outputs") / "validation" / "2026-02"
    assert json.loads((out / "REFUSED.json").read_text())["stopped_by"] == "ReconciliationError"
    recon = (out / "reconciliation.csv").read_text()
    assert "MISMATCH" in recon and "HV0003" in recon
    assert (out / "daily_volume_by_licensee.csv").exists() and (out / "trips_profile.csv").exists()
    assert not (cfg.path("outputs") / "profile" / "2026-02_trips_profile.csv").exists()
    assert query(cfg, "SELECT count(*) FROM month_status") == [(0,)]
