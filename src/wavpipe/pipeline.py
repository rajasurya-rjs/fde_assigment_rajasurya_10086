"""Orchestration: INGEST -> PROFILE -> VALIDATE -> MODEL -> METRICS -> OUTPUT, one month at a time.

Dependability properties (each one is tested in tests/):
* Idempotent: a month is published by DELETE+INSERT inside one transaction, and the fact table has a
  PRIMARY KEY on (month, source_row_id); running the same month twice yields identical outputs.
* Fail-closed: every FATAL check runs BEFORE the publish transaction. A failed month is rolled back and
  the previously published version of that month (if any) stays untouched ("last known good").
* Isolated: one month failing does not stop other months; the exit code reports it.
* Auditable: every attempt is appended to pipeline_run; a JSON run manifest records inputs (SHA-256),
  checks, reconciliation and statuses.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import duckdb

from . import report
from .config import ROOT, Config, Month, render_sql, sql_literal
from .errors import PipelineError, RetrievalError
from .ingest import FetchResult, RawStore, write_json_atomic
from .logging_setup import new_run_id, setup_logging
from .rules import ERROR_RULES, RULES, WARN_RULES, failed_list_sql, is_clean_sql, pass_columns_sql
from .validate import (Check, check_day_coverage, check_licensee_days, check_scan_thresholds, check_schema,
                       check_zone_file, monthly_report_trips, parquet_footer_rows, reconcile)

log = logging.getLogger("wavpipe.pipeline")


@dataclass
class MonthResult:
    month: str
    status: str = "FAILED"                 # PUBLISHED | FAILED
    error_type: str | None = None
    error: str | None = None
    retrieval_status: str | None = None
    metric_status: str | None = None
    seconds: float = 0.0
    inputs: dict[str, Any] = field(default_factory=dict)
    checks: list[dict[str, str]] = field(default_factory=list)
    reconciliation: list[dict[str, Any]] = field(default_factory=list)


def _git_commit() -> str | None:
    try:
        out = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True,
                             text=True, timeout=5)
        return out.stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def connect(cfg: Config) -> duckdb.DuckDBPyConnection:
    wh = cfg.path("warehouse")
    wh.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(wh))
    tmp = wh.parent / "tmp"
    tmp.mkdir(exist_ok=True)
    con.execute(f"SET temp_directory = {sql_literal(tmp)}")
    con.execute("SET memory_limit = '4GB'")
    con.execute("SET preserve_insertion_order = false")
    con.execute(render_sql("00_schema.sql"))
    return con


def hour_band_case(bands: list[list[Any]]) -> str:
    whens = " ".join(
        f"WHEN hour(s.request_datetime) >= {int(lo)} AND hour(s.request_datetime) < {int(hi)} THEN {sql_literal(label)}"
        for lo, hi, label in bands)
    return f"CASE {whens} END"


def stage_sql(cfg: Config, month: Month, trips_path: Path, columns: list[str]) -> str:
    params = {
        "month_start": f"TIMESTAMP '{month.start} 00:00:00'",
        "next_month_start": f"TIMESTAMP '{month.next_start} 00:00:00'",
        "long_response_s": str(int(cfg.validation["long_response_warn_seconds"])),
    }
    quoted = ", ".join('"' + c.replace('"', '""') + '"' for c in columns)
    return render_sql(
        "20_stage_trips.sql",
        month=sql_literal(month),
        trips_path=sql_literal(trips_path),
        all_columns=quoted,
        hour_band_case=hour_band_case(cfg.kpi["hour_bands"]),
        rule_pass_columns=pass_columns_sql(params),
        is_clean=is_clean_sql(),
        failed_rules=failed_list_sql(ERROR_RULES),
        warned_rules=failed_list_sql(WARN_RULES),
        fail_counts=",\n        ".join(
            f"count(*) FILTER (WHERE NOT pass_{r.rule_id}) AS f_{r.rule_id}" for r in RULES),
    )


def metric_views_sql(cfg: Config) -> str:
    k = cfg.kpi
    return render_sql("40_metric_views.sql", gate=repr(float(k["measurability_gate"])),
                      target=repr(float(k["target_rate"])), min_cell=str(int(k["min_cell_requests"])))


def fetch_monthly_report(store: RawStore, cfg: Config, month: Month) -> tuple[Path | None, str]:
    src = cfg.sources["monthly_reports"]
    dest = cfg.path("raw") / "tlc_monthly_reports" / "data_reports_monthly.csv"
    try:
        res = store.fetch_file("monthly_reports", src["url"], dest)
        if monthly_report_trips(res.path, month, src["license_class"]) is None and not store.offline:
            # The cached report predates this month: re-check upstream (old version is preserved).
            res = store.fetch_file("monthly_reports", src["url"], dest, force_check=True)
        return res.path, res.status
    except RetrievalError as exc:
        log.warning("[monthly_reports] unavailable (%s); fallback control total skipped", exc)
        return (dest if dest.exists() else None), "unavailable"


def run_month(con: duckdb.DuckDBPyConnection, cfg: Config, store: RawStore, month: Month, run_id: str,
              allow_unreconciled: bool) -> MonthResult:
    result = MonthResult(month=str(month))
    checks: list[Check] = []
    recons: list[Any] = []
    daily: list[tuple[str, int, int]] = []
    started = datetime.now(timezone.utc)
    t0 = time.monotonic()
    in_tx = False
    # The raw profile is computed first (before any cleaning) but staged, and only moved into outputs/
    # when the month is published: a refused rerun can never overwrite a good month's evidence.
    staged_profile = cfg.path("warehouse").parent / "tmp" / f"{month}_trips_profile.csv"
    staged_profile.unlink(missing_ok=True)
    log.info("==== %s: start", month)
    try:
        # ---------------- INGEST (raw preserved + manifests)
        trips_url = cfg.sources["trips"]["url_template"].format(month=month)
        trips_path = cfg.path("raw") / "tlc_trips" / f"fhvhv_tripdata_{month}.parquet"
        trips: FetchResult = store.fetch_file("trips", trips_url, trips_path)
        base_agg = store.fetch_base_aggregate(month)
        report_path, report_status = fetch_monthly_report(store, cfg, month)
        result.inputs = {
            "trips": {"status": trips.status, **trips.manifest},
            "base_aggregate": {"status": base_agg.status,
                               **{k: v for k, v in base_agg.manifest.items() if k != "pages"}},
            "monthly_report": {"status": report_status, "path": str(report_path) if report_path else None},
        }

        # ---------------- PROFILE (before any cleaning; staged until publication)
        report.write_profile(con, trips_path, staged_profile)

        # ---------------- VALIDATE: contract + completeness (FATAL checks)
        rel = f"read_parquet({sql_literal(trips_path)})"
        columns = check_schema(con, rel, cfg.schema_contract["trips"], "FIL01", "Trip file", checks)
        footer_rows = parquet_footer_rows(con, trips_path)
        check_day_coverage(con, trips_path, month, cfg, checks)
        daily = check_licensee_days(con, trips_path, month, cfg, checks)

        # ---------------- VALIDATE: business rules (row level, one SQL pass) + scan thresholds
        con.execute(stage_sql(cfg, month, trips_path, columns))
        check_scan_thresholds(con, footer_rows, cfg, checks)

        # ---------------- VALIDATE: reconciliation against independent control totals
        file_counts = dict(con.execute("""
            SELECT hvfhs_license_num, sum(n_checked) FROM tmp_rule_counts
            WHERE rule_id = 'f_ANY_ERROR' GROUP BY 1""").fetchall())
        recons, retrieval_status = reconcile(month, cfg, {k: int(v) for k, v in file_counts.items()},
                                             base_agg, report_path, allow_unreconciled, checks, recons)
        metric_status = "FINAL" if retrieval_status == "RECONCILED" else "PROVISIONAL"

        # ---------------- MODEL + publish atomically
        con.begin()
        in_tx = True
        con.execute(render_sql("30_model_month.sql", month=sql_literal(month),
                               threshold_s=str(int(cfg.kpi["threshold_seconds"]))))
        con.execute("DELETE FROM file_check WHERE month = ?", [str(month)])
        con.executemany("INSERT INTO file_check VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        [[str(month), c.check_id, c.name, c.severity, c.status, c.observed, c.expected, c.detail]
                         for c in checks])
        con.execute("DELETE FROM reconciliation WHERE month = ?", [str(month)])
        con.executemany("INSERT INTO reconciliation VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        [[r.month, r.control_source, r.scope, r.file_trips, r.control_trips, r.diff,
                          r.diff_pct, r.status] for r in recons])
        con.execute("DELETE FROM month_status WHERE month = ?", [str(month)])
        con.execute("INSERT INTO month_status VALUES (?, ?, ?, ?, ?, ?, now())",
                    [str(month), retrieval_status, metric_status, trips.manifest["sha256"], footer_rows, run_id])
        con.commit()
        in_tx = False

        result.status = "PUBLISHED"
        result.retrieval_status = retrieval_status
        result.metric_status = metric_status
        result.reconciliation = [r.__dict__ for r in recons]
        log.info("==== %s: PUBLISHED (%s, metrics %s)", month, retrieval_status, metric_status)
    except Exception as exc:  # PipelineError = expected failure mode; anything else = defect, still isolated
        if in_tx:
            con.rollback()
        result.error_type = type(exc).__name__
        result.error = str(exc)
        result.reconciliation = [r.__dict__ for r in recons]
        if not isinstance(exc, PipelineError):
            log.exception("unexpected error in %s", month)
        log.error("==== %s: FAILED [%s] %s - previously published data for this month (if any) is unchanged",
                  month, result.error_type, exc)
    finally:
        for obj in ("VIEW stg_final", "VIEW stg_checked", "VIEW stg_joined", "VIEW stg_trips",
                    "TABLE tmp_rule_counts", "TABLE dup_copy"):
            kind, name = obj.split()
            con.execute(f"DROP {kind} IF EXISTS {name}")
        result.checks = [c.as_row() for c in checks]
        result.seconds = round(time.monotonic() - t0, 1)
        con.execute("INSERT INTO pipeline_run VALUES (?, ?, ?, now(), ?, ?, ?)",
                    [run_id, str(month), started.replace(tzinfo=None), result.status, result.error_type, result.error])
    out_dir = cfg.path("outputs") / "validation" / str(month)
    if result.status == "PUBLISHED":
        report.write_month_validation(con, month, out_dir)
        report.write_daily_volume(daily, out_dir / "daily_volume_by_licensee.csv")
        (cfg.path("outputs") / "profile").mkdir(parents=True, exist_ok=True)
        os.replace(staged_profile, cfg.path("outputs") / "profile" / f"{month}_trips_profile.csv")
    elif not con.execute("SELECT count(*) FROM month_status WHERE month = ?", [str(month)]).fetchone()[0]:
        # Never published: keep the evidence of WHY it was refused, next to where its outputs would be.
        report.write_refused_month(out_dir, result, checks, recons, daily, staged_profile)
    staged_profile.unlink(missing_ok=True)
    return result


def run(months: list[Month], cfg: Config, offline: bool = False, refresh: bool = False,
        allow_unreconciled: bool = False, session: Any | None = None, run_id: str | None = None) -> dict[str, Any]:
    run_id = run_id or new_run_id()
    log_file = setup_logging(cfg.path("logs"), run_id)
    log.info("run %s: months=%s offline=%s refresh=%s allow_unreconciled=%s",
             run_id, [str(m) for m in months], offline, refresh, allow_unreconciled)
    started = datetime.now(timezone.utc)
    store = RawStore(cfg, session=session, offline=offline, refresh=refresh)
    con = connect(cfg)
    summary: dict[str, Any] = {
        "run_id": run_id, "git_commit": _git_commit(), "started_at": started.isoformat(timespec="seconds"),
        "args": {"months": [str(m) for m in months], "offline": offline, "refresh": refresh,
                 "allow_unreconciled": allow_unreconciled},
        "kpi_config": cfg.kpi, "months": {}, "log_file": str(log_file.relative_to(cfg.root))
        if log_file.is_relative_to(cfg.root) else str(log_file),
    }
    try:
        # Shared reference inputs: failure here stops the whole run (nothing can be joined).
        checks: list[Check] = []
        zones_path = cfg.path("raw") / "tlc_zones" / "taxi_zone_lookup.csv"
        zones = store.fetch_file("zones", cfg.sources["zones"]["url"], zones_path)
        check_zone_file(con, zones.path, cfg, checks)
        con.execute(render_sql("10_reference.sql", zones_path=sql_literal(zones.path),
                               licensees_path=sql_literal(ROOT / "config" / "licensees.csv")))
        summary["reference"] = {"zones": {"status": zones.status, **zones.manifest},
                                "checks": [c.as_row() for c in checks]}
    except PipelineError as exc:
        log.error("run %s aborted: reference data unavailable [%s] %s", run_id, type(exc).__name__, exc)
        summary.update(status="FAILED", error=f"{type(exc).__name__}: {exc}")
        _finish(con, cfg, summary, started, publish_outputs=False)
        return summary

    for month in months:
        summary["months"][str(month)] = run_month(con, cfg, store, month, run_id, allow_unreconciled).__dict__

    failed = [m for m, r in summary["months"].items() if r["status"] != "PUBLISHED"]
    summary["status"] = "OK" if not failed else "PARTIAL" if len(failed) < len(months) else "FAILED"
    _finish(con, cfg, summary, started, publish_outputs=True)
    return summary


def _finish(con: duckdb.DuckDBPyConnection, cfg: Config, summary: dict[str, Any], started: datetime,
            publish_outputs: bool) -> None:
    if publish_outputs:
        con.execute(metric_views_sql(cfg))
        report.write_metrics(con, cfg.path("outputs") / "metrics")
        report.write_scorecard(con, cfg, cfg.path("outputs") / "scorecard.md", summary)
    summary["finished_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    summary["seconds"] = round((datetime.now(timezone.utc) - started).total_seconds(), 1)
    con.close()
    log_dir = cfg.path("logs")
    write_json_atomic(log_dir / f"run_{summary['run_id']}.json", summary)
    cfg.path("outputs").mkdir(parents=True, exist_ok=True)
    report.write_rules_doc(cfg, cfg.path("outputs") / "validation_rules.md")
    write_json_atomic(cfg.path("outputs") / "last_run.json", summary)
    log.info("run %s finished: %s in %.1fs", summary["run_id"], summary.get("status"), summary["seconds"])
