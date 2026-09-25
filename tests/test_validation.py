"""Validation: every deliberately bad record is caught by the rule designed for it, and file-level
contract/completeness/reconciliation failures stop the month instead of publishing wrong numbers."""

from __future__ import annotations

from pathlib import Path

import duckdb
import pytest

from conftest import query
from fixtures import (MONTH, TRIP_URL, base_aggregate_rows, standard_session, write_parquet)
from wavpipe.config import ROOT, Month, render_sql, sql_literal
from wavpipe.pipeline import stage_sql


@pytest.fixture(scope="module")
def staged(tmp_path_factory):
    """Run the staging + rule SQL on the synthetic month once; return the in-memory connection."""
    from fixtures import ZONES_CSV, build_rows, pad_to_multiple_of_days
    from wavpipe.config import load_config
    tmp_path = tmp_path_factory.mktemp("staged")
    cfg = load_config(root=tmp_path)
    rows = pad_to_multiple_of_days(build_rows())
    path = tmp_path / "trips.parquet"
    write_parquet(rows, path)
    zones = tmp_path / "zones.csv"
    zones.write_text(ZONES_CSV)
    con = duckdb.connect()
    con.execute(render_sql("00_schema.sql"))
    con.execute(render_sql("10_reference.sql", zones_path=sql_literal(zones),
                           licensees_path=sql_literal(ROOT / "config" / "licensees.csv")))
    columns = [r[0] for r in con.execute(f"DESCRIBE SELECT * FROM read_parquet('{path}')").fetchall()]
    con.execute(stage_sql(cfg, Month.parse(MONTH), path, columns))
    return con


EXPECTED = {  # marker -> (ERROR rules failed, WARN rules triggered)
    "X_TMP01": ("TMP01", ""),
    "X_REF01": ("REF01", ""),
    "X_REF02": ("REF02", "REF03"),      # an unknown zone is also, necessarily, not in a borough
    "X_REF03": ("", "REF03"),
    "X_SCH01": ("SCH01", ""),           # missing on-scene: attributed once, to its root cause
    "X_SCH02": ("SCH02", ""),
    "X_TMP03": ("TMP03", ""),
    "X_TMP04": ("TMP04", ""),
    "X_DOM01": ("", "DOM01"),
    "X_DOM02": ("", "DOM02"),
    "X_DOM03": ("", "DOM03"),
    "X_EXACT600": ("", ""),
}


@pytest.mark.parametrize("marker", sorted(EXPECTED))
def test_each_bad_record_is_caught_by_its_rule(staged, marker):
    failed, warned, clean = staged.execute(
        "SELECT failed_rules, warned_rules, is_clean FROM stg_final WHERE dispatching_base_num = ?",
        [marker]).fetchone()
    assert (failed, warned) == EXPECTED[marker]
    assert clean == (EXPECTED[marker][0] == "")


def test_negative_response_times_are_quarantined(staged):
    rows = staged.execute("""SELECT hvfhs_license_num, failed_rules, is_clean FROM stg_final
                             WHERE dispatching_base_num = 'X_TMP02' ORDER BY 1""").fetchall()
    assert len(rows) == 7 and all(r[1] == "TMP02" and not r[2] for r in rows)


def test_exact_duplicate_keeps_first_copy_only(staged):
    rows = staged.execute("""SELECT source_row_id, failed_rules FROM stg_final
                             WHERE dispatching_base_num = 'X_DOM04' ORDER BY source_row_id""").fetchall()
    assert [r[1] for r in rows] == ["", "DOM04"]


def test_background_rows_are_all_clean(staged, rows):
    n, clean = staged.execute("""SELECT count(*), count(*) FILTER (WHERE is_clean) FROM stg_final
                                 WHERE dispatching_base_num IN ('B03404', 'B03406')""").fetchone()
    assert n == clean


def test_left_joins_never_drop_or_multiply_trips(staged, rows):
    assert staged.execute("SELECT count(*) FROM stg_final").fetchone()[0] == len(rows)


# ---------------------------------------------------------------- file-level failures stop the month
def _month_result(summary):
    return summary["months"][MONTH]


def test_schema_drift_stops_the_month(cfg, rows, tmp_path, run_month):
    session = standard_session(rows, tmp_path, drop=("on_scene_datetime",))
    res = _month_result(run_month(session=session))
    assert res["status"] == "FAILED" and res["error_type"] == "SchemaContractError"
    assert "on_scene_datetime" in res["error"]
    assert query(cfg, "SELECT count(*) FROM fct_wav_trip")[0][0] == 0


def test_new_uncontracted_column_only_warns(cfg, rows, tmp_path, run_month):
    session = standard_session(rows, tmp_path, extra={"new_fee": "DOUBLE"})
    res = _month_result(run_month(session=session))
    assert res["status"] == "PUBLISHED"
    fil01 = next(c for c in res["checks"] if c["check_id"] == "FIL01")
    assert "new_fee" in fil01["detail"]


def test_missing_day_is_detected_as_incomplete_file(cfg, rows, tmp_path, run_month):
    thinned = [r for r in rows if r["pickup_datetime"].day != 14]
    session = standard_session(thinned, tmp_path)
    res = _month_result(run_month(session=session))
    assert res["error_type"] == "CompletenessError" and "[14]" in res["error"]


def test_control_total_mismatch_stops_publication(cfg, rows, session, run_month):
    session.soda_rows = base_aggregate_rows(rows, uber_delta=+50)   # file is "missing" 50 Uber trips
    res = _month_result(run_month())
    assert res["error_type"] == "ReconciliationError" and "HV0003" in res["error"]
    assert query(cfg, "SELECT count(*) FROM month_status")[0][0] == 0


def test_mismatch_can_be_published_as_provisional_when_explicitly_allowed(cfg, rows, session, run_month):
    session.soda_rows = base_aggregate_rows(rows, uber_delta=+50)
    res = _month_result(run_month(allow_unreconciled=True))
    assert (res["status"], res["retrieval_status"], res["metric_status"]) == ("PUBLISHED", "UNRECONCILED", "PROVISIONAL")


def test_unpublished_primary_control_falls_back_and_marks_provisional(cfg, session, run_month):
    session.soda_rows = []                                          # Open Data has not published the month
    res = _month_result(run_month())
    assert (res["retrieval_status"], res["metric_status"]) == ("RECONCILED_FALLBACK", "PROVISIONAL")


def test_fully_reconciled_month_is_final(cfg, run_month):
    res = _month_result(run_month())
    assert (res["retrieval_status"], res["metric_status"]) == ("RECONCILED", "FINAL")
    statuses = {c["check_id"]: c["status"] for c in res["checks"]}
    assert statuses["REC01-HV0003"] == statuses["REC01-HV0005"] == statuses["REC02-ALL"] == "PASS"
    assert statuses["FIL02"] == statuses["FIL03"] == statuses["FIL05"] == statuses["FIL06"] == "PASS"


def test_every_emitted_file_check_is_documented(run_month):
    from wavpipe.validate import FILE_CHECKS
    documented = {c[0] for c in FILE_CHECKS}
    emitted = {c["check_id"].split("-")[0] for c in run_month()["months"][MONTH]["checks"]}
    assert emitted <= documented


def test_offline_rerun_keeps_not_published_status(cfg, session, run_month):
    session.soda_rows = []
    run_month()
    res = _month_result(run_month(offline=True))
    statuses = {r["scope"]: r["status"] for r in res["reconciliation"] if r["control_source"] == "opendata_base_aggregate"}
    assert statuses == {"HV0003": "NOT_PUBLISHED", "HV0005": "NOT_PUBLISHED"}
    assert res["metric_status"] == "PROVISIONAL"
