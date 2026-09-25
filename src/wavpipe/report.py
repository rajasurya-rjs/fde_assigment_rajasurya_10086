"""Output layer: deterministic CSV exports and the Markdown evidence scorecard.

All files are written to <name>.tmp and renamed into place, so a crash mid-write never leaves a
half-written output that looks complete. Every export has an explicit ORDER BY (byte-stable reruns).
"""

from __future__ import annotations

import csv
import json
import os
from pathlib import Path
from typing import Any

import duckdb

from .config import Config, Month, sql_literal
from .rules import RULES
from .validate import FILE_CHECKS

BOROUGHS = ("Manhattan", "Brooklyn", "Queens", "Bronx", "Staten Island")


def _copy(con: duckdb.DuckDBPyConnection, select_sql: str, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    con.execute(f"COPY ({select_sql}) TO {sql_literal(tmp)} (HEADER, DELIMITER ',')")
    os.replace(tmp, path)


def _rounded(con: duckdb.DuckDBPyConnection, relation: str, order_by: str, digits: int = 4) -> str:
    cols = con.execute(f"DESCRIBE SELECT * FROM {relation}").fetchall()
    select = ", ".join(f"round({c[0]}, {digits}) AS {c[0]}" if c[1] in ("DOUBLE", "FLOAT") else c[0] for c in cols)
    return f"SELECT {select} FROM {relation} ORDER BY {order_by}"


def _register_rules(con: duckdb.DuckDBPyConnection) -> None:
    con.execute("""CREATE OR REPLACE TEMP TABLE rule_registry (
        rule_id VARCHAR, category VARCHAR, severity VARCHAR, rule VARCHAR, why VARCHAR, action VARCHAR)""")
    con.executemany("INSERT INTO rule_registry VALUES (?, ?, ?, ?, ?, ?)",
                    [[r.rule_id, r.category, r.severity, r.rule, r.why, r.action] for r in RULES]
                    + [["ANY_ERROR", "summary", "ERROR", "Row fails at least one ERROR rule (quarantined)",
                        "Total quarantined rows", "Excluded from all metrics"]])


# ---------------------------------------------------------------------------- per month
def write_profile(con: duckdb.DuckDBPyConnection, trips_path: Path, out: Path) -> None:
    """Column profile of the RAW file, before any cleaning, in one pass: rows, nulls, EXACT distinct
    counts, min, max, mean. Exact aggregates only, so reruns are byte-identical. The response-time
    distribution per licensee x WAV segment (before validation) is in dq_segment_profile."""
    rel = f"read_parquet({sql_literal(trips_path)})"
    cols = con.execute(f"DESCRIBE SELECT * FROM {rel}").fetchall()
    numeric = ("DOUBLE", "BIGINT", "INTEGER", "FLOAT")
    aggs = []
    for name, typ, *_ in cols:
        q = '"' + name.replace('"', '""') + '"'
        aggs += [f"count({q})", f"count(DISTINCT {q})", f"min({q})::VARCHAR", f"max({q})::VARCHAR",
                 f"round(avg({q}), 3)" if typ in numeric else "NULL"]
    row = con.execute(f"SELECT count(*), {', '.join(aggs)} FROM {rel}").fetchone()
    total = row[0]
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.name + ".tmp")
    with open(tmp, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["column_name", "column_type", "rows", "non_null", "null_pct", "distinct", "min", "max", "mean"])
        for i, (name, typ, *_) in enumerate(cols):
            non_null, distinct, lo, hi, mean = row[1 + 5 * i: 6 + 5 * i]
            w.writerow([name, typ, total, non_null, f"{100 * (total - non_null) / total:.4f}" if total else "",
                        distinct, lo, hi, "" if mean is None else mean])
    os.replace(tmp, out)


def write_daily_volume(daily: list[tuple[str, int, int]], out: Path) -> None:
    """Trips per licensee per pickup day (the series behind FIL07)."""
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.name + ".tmp")
    with open(tmp, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["hvfhs_license_num", "day", "trips"])
        w.writerows(sorted(daily))
    os.replace(tmp, out)


def write_refused_month(out_dir: Path, result: Any, checks: list[Any], recons: list[Any],
                        daily: list[tuple[str, int, int]], staged_profile: Path) -> None:
    """Evidence for a month that was never published: why it was refused, and the data behind it."""
    out_dir.mkdir(parents=True, exist_ok=True)
    status = {"month": result.month, "status": "REFUSED", "stopped_by": result.error_type, "reason": result.error,
              "note": "Nothing from this month was published. Fix the cause (or wait for the control total) and rerun."}
    (out_dir / "REFUSED.json").write_text(json.dumps(status, indent=2) + "\n")
    with open(out_dir / "file_checks.csv", "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["check_id", "name", "severity", "status", "observed", "expected", "detail"])
        w.writerows([[c.check_id, c.name, c.severity, c.status, c.observed, c.expected, c.detail]
                     for c in sorted(checks, key=lambda c: c.check_id)])
    with open(out_dir / "reconciliation.csv", "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["control_source", "scope", "file_trips", "control_trips", "diff", "diff_pct", "status"])
        w.writerows([[r.control_source, r.scope, r.file_trips, r.control_trips, r.diff,
                      "" if r.diff_pct is None else round(r.diff_pct, 5), r.status] for r in recons])
    if daily:
        write_daily_volume(daily, out_dir / "daily_volume_by_licensee.csv")
    if staged_profile.exists():
        os.replace(staged_profile, out_dir / "trips_profile.csv")


def write_month_validation(con: duckdb.DuckDBPyConnection, month: Month, out_dir: Path) -> None:
    m = sql_literal(month)
    _register_rules(con)
    _copy(con, f"""
        SELECT v.rule_id, r.category, r.severity, r.rule, v.hvfhs_license_num, l.brand,
               v.wav_requested, v.n_checked, v.n_failed,
               round(100.0 * v.n_failed / v.n_checked, 4) AS pct_failed, r.why, r.action
        FROM validation_rule_result v
        JOIN rule_registry r ON r.rule_id = v.rule_id
        LEFT JOIN ref_licensee l ON l.hvfhs_license_num = v.hvfhs_license_num
        WHERE v.month = {m}
        ORDER BY v.rule_id, v.hvfhs_license_num, v.wav_requested""", out_dir / "rule_results.csv")
    _copy(con, f"SELECT * EXCLUDE (month) FROM file_check WHERE month = {m} ORDER BY check_id",
          out_dir / "file_checks.csv")
    _copy(con, f"""SELECT * EXCLUDE (month), round(diff_pct, 5) AS diff_pct_rounded
                   FROM reconciliation WHERE month = {m} ORDER BY control_source, scope""",
          out_dir / "reconciliation.csv")
    _copy(con, f"""
        SELECT hvfhs_license_num, failed_rules, count(*) AS n_rows,
               min(response_s) AS min_response_s, max(response_s) AS max_response_s,
               count(DISTINCT dispatching_base_num) AS n_bases, any_value(dispatching_base_num) AS a_base
        FROM fct_wav_trip WHERE month = {m} AND NOT is_clean
        GROUP BY ALL ORDER BY hvfhs_license_num, n_rows DESC, failed_rules""",
          out_dir / "quarantine_wav_summary.csv")
    # Deterministic sample: first 25 quarantined rows per licensee x failure reason, by raw row position.
    _copy(con, f"""
        SELECT * EXCLUDE (rn) FROM (
            SELECT source_row_id, hvfhs_license_num, dispatching_base_num, request_datetime, on_scene_datetime,
                   pickup_datetime, dropoff_datetime, pu_location_id, pu_borough, response_s, curbside_s,
                   failed_rules, warned_rules,
                   row_number() OVER (PARTITION BY hvfhs_license_num, failed_rules ORDER BY source_row_id) AS rn
            FROM fct_wav_trip WHERE month = {m} AND NOT is_clean)
        WHERE rn <= 25 ORDER BY hvfhs_license_num, failed_rules, source_row_id""",
          out_dir / "quarantine_wav_sample.csv")


# ---------------------------------------------------------------------------- metrics
METRIC_EXPORTS = {
    "m1_m2_kpi_dispatcher_month": "month, hvfhs_license_num",
    "m1_m2_kpi_ytd": "year, hvfhs_license_num",
    "m3_response_gap": "month, hvfhs_license_num",
    "m4_stage_decomposition": "month, hvfhs_license_num",
    "m5_service_gap_summary": "month, hvfhs_license_num",
    "m5_service_gap_cells": "month, hvfhs_license_num, pu_borough, hour_band",
    "dq_negative_response_signature": "month, hvfhs_license_num, wav_requested",
    "dq_segment_profile": "month, hvfhs_license_num, wav_requested",
}


def write_metrics(con: duckdb.DuckDBPyConnection, out_dir: Path) -> None:
    for view, order in METRIC_EXPORTS.items():
        _copy(con, _rounded(con, view, order), out_dir / f"{view}.csv")


# ---------------------------------------------------------------------------- scorecard
def _pct(v: Any, digits: int = 1) -> str:
    return "—" if v is None else f"{100 * v:.{digits}f}%"


def _num(v: Any, digits: int = 1) -> str:
    return "—" if v is None else f"{v:.{digits}f}"


def _int(v: Any) -> str:
    return "—" if v is None else f"{int(v):,}"


def _table(headers: list[str], rows: list[list[str]]) -> str:
    if not rows:
        return "_No rows._\n"
    lines = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    lines += ["| " + " | ".join(r) + " |" for r in rows]
    return "\n".join(lines) + "\n"


def _q(con: duckdb.DuckDBPyConnection, sql: str) -> list[dict[str, Any]]:
    cur = con.execute(sql)
    names = [d[0] for d in cur.description]
    return [dict(zip(names, row)) for row in cur.fetchall()]


def write_scorecard(con: duckdb.DuckDBPyConnection, cfg: Config, out: Path, summary: dict[str, Any]) -> None:
    k = cfg.kpi
    target = k["target_rate"]
    lines: list[str] = []
    add = lines.append
    add("# WAV Service Scorecard — NYC High-Volume FHV accessible dispatch\n")
    add("_Generated by `run_pipeline.py` from the warehouse (run details: `outputs/last_run.json`). "
        "Do not edit by hand; rerun the pipeline._\n")
    add(f"**Standard:** 35 RCNY §59B-17(f)(3) — an accessible-vehicle dispatcher must serve at least "
        f"**{target:.0%} of WAV requests in under {k['threshold_seconds'] // 60} minutes** "
        f"(request → vehicle on scene). Enforced on a calendar-year basis from April 2025.\n")
    add(f"**Population:** completed HVFHV trips with `wav_request_flag = 'Y'`. Requests that were never "
        f"completed are not in TLC trip records (see Limitations in README).\n")

    # 1. trust
    add("## 1. Can these numbers be trusted? Retrieval completeness\n")
    months = _q(con, "SELECT * FROM month_status ORDER BY month")
    recon = _q(con, "SELECT * FROM reconciliation ORDER BY month, control_source, scope")
    rows = []
    for ms in months:
        def rc(src: str, scope: str) -> str:
            r = next((x for x in recon if x["month"] == ms["month"] and x["control_source"] == src
                      and x["scope"] == scope), None)
            if r is None:
                return "—"
            if r["control_trips"] is None:
                return r["status"].replace("_", " ").lower()
            return f"{r['status']} ({r['diff_pct']:+.3f}%)"
        rows.append([ms["month"], _int(ms["trips_rows"]), rc("opendata_base_aggregate", "HV0003"),
                     rc("opendata_base_aggregate", "HV0005"), rc("tlc_monthly_report", "ALL"),
                     f"**{ms['retrieval_status']}**", f"**{ms['metric_status']}**"])
    add(_table(["Month", "Trip records", "Uber vs Open Data API control", "Lyft vs Open Data API control",
                "All vs TLC monthly report", "Retrieval", "Metrics"], rows))
    add(f"FINAL = licensee-level control totals match within ±{cfg.reconciliation['tolerance_pct']}%. "
        "PROVISIONAL = only the industry-level fallback control (or none) is published yet.\n")
    refused = _q(con, """
        SELECT month, error_type, detail, finished_at FROM (
            SELECT *, row_number() OVER (PARTITION BY month ORDER BY finished_at DESC) AS rn FROM pipeline_run)
        WHERE rn = 1 AND status = 'FAILED' AND month NOT IN (SELECT month FROM month_status)
        ORDER BY month""")
    if refused:
        add("**Months attempted but refused** (nothing published for them; fix the cause and rerun):\n")
        add(_table(["Month", "Stopped by", "Reason"],
                   [[r["month"], r["error_type"], r["detail"]] for r in refused]))

    # 2. KPI
    add("## 2. KPI — WAV 10-minute service rate (M1) and evidence coverage (M2)\n")
    kpi = _q(con, "SELECT * FROM m1_m2_kpi_dispatcher_month ORDER BY month, hvfhs_license_num")
    add(_table(
        ["Month", "Dispatcher", "WAV requests", "Evidence coverage (M2)", "Naive rate (no validation)",
         "Clean-record rate", "Bounds: quarantined all fail / all pass", "**Status**", "Basis"],
        [[r["month"], f"{r['brand']} ({r['hvfhs_license_num']})", _int(r["wav_requests"]),
          _pct(r["m2_evidence_coverage"]), _pct(r["naive_rate_unvalidated"]),
          _pct(r["clean_record_rate"]) + ("" if r["measurable"] else " (not decision-grade)"),
          f"{_pct(r['m1_bound_low'])} – {_pct(r['m1_bound_high'])}", f"**{r['kpi_status']}**",
          r["verdict_basis"]] for r in kpi]))
    add("_Naive_ = share of all records with response < 10 min and no validation (impossible negative times "
        "count as passes). _Clean-record rate_ = the same share among records that pass every ERROR rule. "
        "**M1 is the clean-record rate**, published only when the evidence supports it:\n")
    add(f"- **MEETS / BELOW** (proven): the bounds lie entirely above / below {target:.0%}, so no assumption "
        "about the quarantined records can change the verdict.\n"
        f"- **MEETS / BELOW (estimate)**: the bounds straddle {target:.0%}; the verdict treats the quarantined "
        f"records as out of scope, accepted only when ≥ {k['measurability_gate']:.0%} of records are clean "
        f"(a policy line set equal to the standard's own {1 - target:.0%} tolerance).\n"
        f"- **NOT MEASURABLE**: the bounds straddle {target:.0%} and fewer than {k['measurability_gate']:.0%} of "
        "records are clean. No verdict; request corrected data.\n")
    ytd = _q(con, "SELECT * FROM m1_m2_kpi_ytd ORDER BY year, hvfhs_license_num")
    add("**Calendar-year roll-up of the months processed** (counts are additive, so this is exact for these "
        "months only; enforcement covers the whole calendar year):\n")
    add(_table(["Year", "Dispatcher", "Months included", "WAV requests", "Coverage", "M1", "Bounds", "Status",
                "All months FINAL?"],
               [[r["year"], r["brand"], r["months_included"], _int(r["wav_requests"]),
                 _pct(r["m2_evidence_coverage"]), _pct(r["m1_service_rate_10min"]),
                 f"{_pct(r['m1_bound_low'])} – {_pct(r['m1_bound_high'])}", r["kpi_status"],
                 "yes" if r["all_months_final"] else "no"] for r in ytd]))

    # 3. gap
    add("## 3. Is WAV service comparable to standard service? (M3)\n")
    gap = _q(con, "SELECT * FROM m3_response_gap ORDER BY month, hvfhs_license_num")
    add(_table(["Month", "Dispatcher", "WAV response P50 / P90 (min)", "Non-WAV response P50 / P90 (min)",
                "**P90 gap (M3, min)**", "Non-WAV 10-min rate"],
               [[r["month"], r["brand"], f"{_num(r['wav_response_p50_min'])} / {_num(r['wav_response_p90_min'])}",
                 f"{_num(r['nonwav_response_p50_min'])} / {_num(r['nonwav_response_p90_min'])}",
                 f"**{_num(r['m3_p90_gap_min'], 2)}**", _pct(r["nonwav_service_rate_10min"])] for r in gap]))
    add("P90 is the continuous twin of the standard: “90% under 10 minutes” ⇔ P90 response < 10 minutes. "
        "Parity at P90 does not mean parity at the median: compare the P50 columns.\n")

    # 4. stage decomposition
    add("## 4. Where does the extra wait of WAV riders accumulate? (M4)\n")
    st = _q(con, "SELECT * FROM m4_stage_decomposition ORDER BY month, hvfhs_license_num")
    add(_table(["Month", "Dispatcher", "Mean wait WAV / non-WAV (min)", "Extra wait (min)",
                "…before arrival (regulated)", "…after arrival (curbside)", "**Curbside share of extra wait (M4)**",
                "Median curbside WAV / non-WAV (min)"],
               [[r["month"], r["brand"], f"{_num(r['wav_wait_mean_min'], 2)} / {_num(r['nonwav_wait_mean_min'], 2)}",
                 _num(r["extra_wait_min"], 2), _num(r["extra_response_min"], 2), _num(r["extra_curbside_min"], 2),
                 f"**{_pct(r['m4_curbside_share_of_extra_wait'])}**",
                 f"{_num(r['wav_curbside_p50_min'], 2)} / {_num(r['nonwav_curbside_p50_min'], 2)}"] for r in st]))
    add("wait = response + curbside exactly for clean records, so the extra mean wait splits additively into the "
        "part the 10-minute rule measures (before arrival) and the part it does not (after arrival).\n")

    # 5. where
    add("## 5. Where and when is service below the standard? (M5)\n")
    s5 = _q(con, "SELECT * FROM m5_service_gap_summary ORDER BY month, hvfhs_license_num")
    add(_table(["Month", "Dispatcher", "Cells judged (≥ %d requests)" % k["min_cell_requests"],
                "Cells significantly below target", "**Share of WAV demand in those cells (M5)**",
                "Share of late arrivals in those cells", "Cells below on point estimate only",
                "Requests in low-volume cells"],
               [[r["month"], r["brand"], _int(r["cells_judged"]), _int(r["cells_significantly_below"]),
                 f"**{_pct(r['m5_share_of_demand_in_below_target_cells'])}**",
                 _pct(r["share_of_late_arrivals_in_those_cells"]),
                 _int(r["cells_below_point_estimate"] - r["cells_significantly_below"]),
                 _int(r["requests_in_low_volume_cells"])] for r in s5]))
    cells = _q(con, """SELECT * FROM m5_service_gap_cells WHERE below_target
                       ORDER BY month, hvfhs_license_num, significantly_below DESC, wav_late_arrivals DESC,
                                pu_borough, hour_band""")
    add("Cells below 90% (cell = pickup borough × request time band). **Significant** = the 95% Wilson interval "
        "lies entirely below 90%. The others may be sampling noise:\n")
    add(_table(["Month", "Dispatcher", "Borough", "Time band", "WAV requests", "WAV 10-min rate (95% CI)",
                "Significant?", "WAV P90 (min)", "Late arrivals", "Non-WAV rate, same cell"],
               [[r["month"], r["brand"], r["pu_borough"], r["hour_band"], _int(r["wav_requests_clean"]),
                 f"{_pct(r['wav_service_rate_10min'])} ({_pct(r['wilson_low'])}–{_pct(r['wilson_high'])})",
                 "**yes**" if r["significantly_below"] else "no", _num(r["wav_response_p90_min"]),
                 _int(r["wav_late_arrivals"]), _pct(r["nonwav_service_rate_10min"])] for r in cells]))

    # 6. diagnosis of the impossible response times
    add("## 6. Why are response times impossible? Diagnosis of rule TMP02\n")
    sig = _q(con, "SELECT * FROM dq_negative_response_signature WHERE n_negative > 0 "
                  "ORDER BY month, hvfhs_license_num, wav_requested DESC")
    add(_table(["Month", "Dispatcher", "WAV request", "Records arriving before request", "Share of segment",
                "…with request on a whole minute", "…on a 5-minute mark", "Valid records on a whole minute",
                "Signature"],
               [[r["month"], r["brand"], "Y" if r["wav_requested"] else "N", _int(r["n_negative"]),
                 _pct(r["negative_share"]), _pct(r["negative_whole_minute_share"]),
                 _pct(r["negative_5min_mark_share"]), _pct(r["valid_whole_minute_share"]), r["signature"]]
                for r in sig]))
    seg = _q(con, "SELECT * FROM dq_segment_profile ORDER BY month, hvfhs_license_num, wav_requested DESC")
    add("Segment profile, RAW records before any rule (record path, airport pattern, response distribution):\n")
    add(_table(["Month", "Dispatcher", "WAV", "Records", "Dispatching bases", "originating_base_num set",
                "On-scene = pickup (airports / elsewhere)", "Raw response P01 / P50 / P99 (min)",
                "Clean response P50 / P90 (min)"],
               [[r["month"], r["brand"], "Y" if r["wav_requested"] else "N", _int(r["n_records"]),
                 _int(r["n_dispatching_bases"]), _pct(r["originating_base_present_share"]),
                 f"{_pct(r['onscene_eq_pickup_share_airports'])} / {_pct(r['onscene_eq_pickup_share_elsewhere'])}",
                 f"{_num(r['raw_response_p01_min'])} / {_num(r['raw_response_p50_min'])} / "
                 f"{_num(r['raw_response_p99_min'])}",
                 f"{_num(r['clean_response_p50_min'])} / {_num(r['clean_response_p90_min'])}"] for r in seg]))
    add("By chance about 1 in 60 request timestamps lands on a whole minute. Pre-booked rides store the booked "
        "time, so a failure set that is almost entirely whole-minute (and 5-minute) timestamps matches a "
        "pre-booked ride where the driver arrived early. A failure set at the chance rate does not: its cause is "
        "unknown and needs a data-correction request.\n")

    # 7. data quality
    add("## 7. Data quality — rules that fired (full detail in `outputs/validation/<month>/`)\n")
    _register_rules(con)
    dq = _q(con, """
        SELECT v.month, v.rule_id, r.severity, r.rule, coalesce(l.brand, v.hvfhs_license_num) AS brand,
               v.wav_requested, v.n_failed, 100.0 * v.n_failed / v.n_checked AS pct
        FROM validation_rule_result v JOIN rule_registry r USING (rule_id)
        LEFT JOIN ref_licensee l USING (hvfhs_license_num)
        WHERE v.n_failed > 0 AND v.rule_id <> 'ANY_ERROR'
        ORDER BY v.month, v.rule_id, brand, v.wav_requested""")
    add(_table(["Month", "Rule", "Severity", "Rule text", "Dispatcher", "WAV request", "Rows failed", "% of segment"],
               [[r["month"], r["rule_id"], r["severity"], r["rule"], r["brand"], r["wav_requested"],
                 _int(r["n_failed"]), f"{r['pct']:.3f}%"] for r in dq]))

    tmp = out.with_name(out.name + ".tmp")
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_text("\n".join(lines))
    os.replace(tmp, out)


def write_rules_doc(cfg: Config, out: Path) -> None:
    """Render the rule registries so documentation can never drift from the code."""
    lines = ["# Validation rules\n",
             "_Generated from `src/wavpipe/rules.py` and `src/wavpipe/validate.py` on every run._\n",
             "## File-level checks (can the month be trusted at all?)\n",
             _table(["ID", "Check", "Severity", "Why it matters", "Action on failure"],
                    [list(c) for c in FILE_CHECKS]),
             "## Row-level business rules (evaluated on every record)\n",
             "ERROR = quarantined (excluded from all metrics, kept with reasons in `fct_wav_trip` and "
             "`outputs/validation/<month>/`). WARN = kept in metrics, counted and reported.\n",
             _table(["ID", "Category", "Severity", "Rule", "Why it matters", "Action"],
                    [[r.rule_id, r.category, r.severity, r.rule, r.why, r.action] for r in RULES]),
             "## Segment-level verdict logic (SEG01)\n",
             "For each dispatcher-month, bounds are computed with every quarantined WAV record counted first as a "
             f"failure, then as a pass. If both bounds are on the same side of {cfg.kpi['target_rate']:.0%}, the "
             "verdict is **proven** (MEETS / BELOW). If they straddle the target, the clean-record rate decides "
             f"(**estimate**) only when at least {cfg.kpi['measurability_gate']:.0%} of records are clean, a "
             f"policy line equal to the standard's {1 - cfg.kpi['target_rate']:.0%} tolerance. Otherwise the "
             "KPI is **NOT MEASURABLE** and a data-correction request is the output. M3–M5 are computed only "
             "for dispatchers at or above the coverage line.\n"]
    tmp = out.with_name(out.name + ".tmp")
    tmp.write_text("\n".join(lines))
    os.replace(tmp, out)
