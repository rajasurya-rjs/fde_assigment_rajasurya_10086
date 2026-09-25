"""File-level validation (contract + completeness) and reconciliation against control totals.

Row-level business rules live in rules.py and are evaluated in SQL (sql/20_stage_trips.sql).
This module decides whether a month's file can be trusted AT ALL before any metric is computed.
"""

from __future__ import annotations

import csv
import json
import logging
import statistics
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import duckdb

from .config import Config, Month, sql_literal
from .errors import (CompletenessError, ReconciliationError, SchemaContractError,
                     ValidationThresholdError)
from .ingest import FetchResult

log = logging.getLogger("wavpipe.validate")


# Documentation registry for file-level checks (rendered into outputs/validation_rules.md).
# tests/test_validation.py asserts every check the pipeline emits is listed here.
FILE_CHECKS: tuple[tuple[str, str, str, str, str], ...] = (
    ("ZON01", "Zone lookup matches its schema contract", "FATAL",
     "Borough attribution depends on these columns.", "Stop the whole run"),
    ("ZON02", "Zone LocationID is unique", "FATAL",
     "A duplicated join key would multiply trips in every borough view.", "Stop the whole run"),
    ("FIL01", "Trip file matches its schema contract (11 columns, fixed types)", "FATAL",
     "A renamed or retyped timestamp would silently corrupt every duration.", "Stop the month; extra columns only WARN"),
    ("FIL02", "Rows read by the validation scan == rows declared in the Parquet footer", "FATAL",
     "Proves every row group was read; a partial read would undercount.", "Stop the month"),
    ("FIL03", "Every calendar day of the month has pickups", "FATAL",
     "A missing day means a truncated or wrong file.", "Stop the month"),
    ("FIL04", "No day below 50% of the median daily volume", "WARN",
     "Flags a partial extract; could also be weather or a holiday.", "Report; do not drop"),
    ("FIL05", "Pickups outside the file month <= 1%", "FATAL",
     "More means the partition contract is broken (wrong file or double counting).", "Stop the month"),
    ("FIL06", "Rows failing any ERROR rule <= 5%", "FATAL",
     "Individual rows are quarantined; this stops a file that is broken wholesale.", "Stop the month"),
    ("FIL07", "No licensee-day below 70% of that licensee's median day", "WARN",
     "Localizes partial gaps a file-level check misses (added after June 2026).", "Report; reconciliation is the gate"),
    ("CTL01", "Control data has one row per base per month", "WARN",
     "A duplicated control row would inflate the control total.", "Report"),
    ("REC01", "Trips per licensee == Open Data API control (within 0.5%)", "FATAL",
     "Independent proof the file is complete at licensee grain.", "Stop the month (unless --allow-unreconciled)"),
    ("REC02", "Total trips == TLC monthly report control (within 0.5%)", "FATAL",
     "Timely fallback control while the API control is unpublished.", "Stop the month (unless --allow-unreconciled)"),
)


@dataclass
class Check:
    check_id: str
    name: str
    severity: str      # FATAL (stops the month) | WARN | INFO
    status: str        # PASS | FAIL | WARN | SKIPPED
    observed: str = ""
    expected: str = ""
    detail: str = ""

    def as_row(self) -> dict[str, str]:
        return asdict(self)


def _record(checks: list[Check], check: Check) -> Check:
    checks.append(check)
    level = logging.INFO if check.status in ("PASS", "SKIPPED") else logging.WARNING
    log.log(level, "%s %-5s %s | observed=%s expected=%s %s", check.check_id, check.status,
            check.name, check.observed, check.expected, check.detail)
    return check


# ---------------------------------------------------------------------------- contracts
def check_schema(con: duckdb.DuckDBPyConnection, relation_sql: str, contract: dict[str, str],
                 check_id: str, label: str, checks: list[Check]) -> list[str]:
    """Compare the actual schema with the contract. Missing/changed -> FATAL; extra -> WARN."""
    actual = {row[0]: row[1] for row in con.execute(f"DESCRIBE SELECT * FROM {relation_sql}").fetchall()}
    missing = [c for c in contract if c not in actual]
    changed = [f"{c}: {actual[c]} (expected {t})" for c, t in contract.items() if c in actual and actual[c] != t]
    extra = sorted(set(actual) - set(contract))
    if missing or changed:
        _record(checks, Check(check_id, f"{label} schema contract", "FATAL", "FAIL",
                              observed=f"missing={missing} changed={changed}",
                              expected=f"{len(contract)} contracted columns with fixed types"))
        raise SchemaContractError(f"{label}: schema contract broken - missing={missing}, changed={changed}")
    _record(checks, Check(check_id, f"{label} schema contract", "FATAL", "PASS",
                          observed=f"{len(contract)} contracted columns OK",
                          expected=f"{len(contract)} contracted columns with fixed types",
                          detail=f"{len(extra)} uncontracted columns ignored: {', '.join(extra)}" if extra else ""))
    return list(actual)


def check_zone_file(con: duckdb.DuckDBPyConnection, zones_path: Path, cfg: Config,
                    checks: list[Check]) -> None:
    rel = f"read_csv({sql_literal(zones_path)}, header = true, auto_detect = true)"
    check_schema(con, rel, cfg.schema_contract["zones"], "ZON01", "Zone lookup", checks)
    n, ids, lo, hi = con.execute(
        f"SELECT count(*), count(DISTINCT LocationID), min(LocationID), max(LocationID) FROM {rel}").fetchone()
    status = "PASS" if n == ids and n > 0 else "FAIL"
    _record(checks, Check("ZON02", "Zone lookup: LocationID unique (valid join key)", "FATAL", status,
                          observed=f"{n} rows, {ids} distinct ids ({lo}-{hi})", expected="rows == distinct ids"))
    if status == "FAIL":
        raise SchemaContractError("Zone lookup LocationID is not unique: joins would multiply trips")


# ---------------------------------------------------------------------------- completeness
def parquet_footer_rows(con: duckdb.DuckDBPyConnection, path: Path) -> int:
    return int(con.execute(
        f"SELECT sum(num_rows) FROM parquet_file_metadata({sql_literal(path)})").fetchone()[0])


def check_day_coverage(con: duckdb.DuckDBPyConnection, path: Path, month: Month, cfg: Config,
                       checks: list[Check]) -> dict[int, int]:
    rows = con.execute(f"""
        SELECT day(pickup_datetime) AS d, count(*) AS n
        FROM read_parquet({sql_literal(path)})
        WHERE pickup_datetime >= DATE '{month.start}' AND pickup_datetime < DATE '{month.next_start}'
        GROUP BY 1 ORDER BY 1""").fetchall()
    per_day = {int(d): int(n) for d, n in rows}
    missing = [d for d in range(1, month.days + 1) if d not in per_day]
    if missing:
        _record(checks, Check("FIL03", "Every calendar day has pickups", "FATAL", "FAIL",
                              observed=f"missing days {missing}", expected=f"days 1-{month.days}"))
        raise CompletenessError(f"{month}: no pickups on days {missing} - truncated or wrong file")
    _record(checks, Check("FIL03", "Every calendar day has pickups", "FATAL", "PASS",
                          observed=f"{len(per_day)} of {month.days} days", expected=f"{month.days} days"))
    median = statistics.median(per_day.values())
    floor = cfg.validation["min_day_share_of_median"] * median
    low = {d: n for d, n in per_day.items() if n < floor}
    _record(checks, Check("FIL04", "No day below 50% of the median daily volume", "WARN",
                          "WARN" if low else "PASS",
                          observed=f"min {min(per_day.values()):,} / median {median:,.0f}"
                                   + (f"; low days {low}" if low else ""),
                          expected=f">= {floor:,.0f} trips per day",
                          detail="Low days are reported, not dropped (could be weather/holiday or a partial extract)."))
    return per_day


def check_licensee_days(con: duckdb.DuckDBPyConnection, path: Path, month: Month, cfg: Config,
                        checks: list[Check]) -> list[tuple[str, int, int]]:
    """Localize partial gaps a file-level day check misses (one licensee missing part of a week).

    Returns every (licensee, day, trips) row so the series is published as evidence.
    """
    rows = con.execute(f"""
        SELECT hvfhs_license_num, day(pickup_datetime) AS d, count(*) AS n
        FROM read_parquet({sql_literal(path)})
        WHERE pickup_datetime >= DATE '{month.start}' AND pickup_datetime < DATE '{month.next_start}'
        GROUP BY ALL ORDER BY 1, 2""").fetchall()
    share = cfg.validation["min_licensee_day_share_of_median"]
    by_lic: dict[str, dict[int, int]] = {}
    for lic, d, n in rows:
        by_lic.setdefault(lic, {})[int(d)] = int(n)
    low: list[tuple[str, int, int]] = []
    for lic, days in by_lic.items():
        median = statistics.median(days.values())
        low += [(lic, d, n) for d, n in days.items() if n < share * median]
    detail = ", ".join(f"{lic} day {d}: {n:,} (median {statistics.median(by_lic[lic].values()):,.0f})"
                       for lic, d, n in low[:12])
    _record(checks, Check("FIL07", f"No licensee-day below {share:.0%} of that licensee's median day", "WARN",
                          "WARN" if low else "PASS", observed=f"{len(low)} low licensee-days",
                          expected="0", detail=detail or "Localizes partial gaps; reconciliation remains the gate."))
    return [(lic, int(d), int(n)) for lic, d, n in rows]


def check_scan_thresholds(con: duckdb.DuckDBPyConnection, footer_rows: int, cfg: Config,
                          checks: list[Check]) -> dict[str, int]:
    """Checks computed from the validation scan (tmp_rule_counts)."""
    totals = dict(con.execute("""
        SELECT rule_id, sum(n_failed) FROM tmp_rule_counts GROUP BY rule_id""").fetchall())
    scanned = int(con.execute(
        "SELECT sum(n_checked) FROM tmp_rule_counts WHERE rule_id = 'f_ANY_ERROR'").fetchone()[0] or 0)

    status = "PASS" if scanned == footer_rows else "FAIL"
    _record(checks, Check("FIL02", "Rows read by validation scan == rows declared in Parquet footer", "FATAL",
                          status, observed=f"{scanned:,}", expected=f"{footer_rows:,}"))
    if status == "FAIL":
        raise CompletenessError(f"validation scan read {scanned:,} rows but the footer declares {footer_rows:,}")

    out_of_month = int(totals.get("f_TMP01", 0))
    pct = 100 * out_of_month / scanned if scanned else 100.0
    limit = cfg.validation["max_out_of_month_pct"]
    status = "PASS" if pct <= limit else "FAIL"
    _record(checks, Check("FIL05", "Pickups outside the file month (partition contract)", "FATAL", status,
                          observed=f"{out_of_month:,} rows ({pct:.3f}%)", expected=f"<= {limit}%"))
    if status == "FAIL":
        raise CompletenessError(f"{pct:.2f}% of pickups fall outside the month - wrong file?")

    errors = int(totals.get("f_ANY_ERROR", 0))
    pct = 100 * errors / scanned if scanned else 100.0
    limit = cfg.validation["max_error_row_pct"]
    status = "PASS" if pct <= limit else "FAIL"
    _record(checks, Check("FIL06", "Rows failing any ERROR rule (whole file)", "FATAL", status,
                          observed=f"{errors:,} rows ({pct:.2f}%)", expected=f"<= {limit}%",
                          detail="Rows are quarantined individually; this gate stops a file that is broken wholesale."))
    if status == "FAIL":
        raise ValidationThresholdError(f"{pct:.2f}% of rows fail ERROR rules (limit {limit}%)")
    return {"scanned": scanned, "error_rows": errors}


# ---------------------------------------------------------------------------- reconciliation
@dataclass
class Recon:
    month: str
    control_source: str
    scope: str
    file_trips: int | None
    control_trips: int | None
    diff: int | None
    diff_pct: float | None
    status: str       # MATCH | MISMATCH | NOT_PUBLISHED | NOT_IN_CONTROL | UNAVAILABLE


def _compare(month: Month, source: str, scope: str, file_n: int, control_n: int | None,
             tolerance_pct: float, missing_status: str) -> Recon:
    if control_n is None:
        return Recon(str(month), source, scope, file_n, None, None, None, missing_status)
    diff = file_n - control_n
    pct = 100 * diff / control_n if control_n else None
    ok = pct is not None and abs(pct) <= tolerance_pct
    return Recon(str(month), source, scope, file_n, control_n, diff, pct, "MATCH" if ok else "MISMATCH")


def load_base_aggregate_rows(result: FetchResult) -> list[dict[str, Any]] | None:
    if result.manifest.get("status") != "complete":
        return None
    rows: list[dict[str, Any]] = []
    for page in result.manifest["pages"]:
        rows.extend(json.loads((result.path / page["file"]).read_text()))
    return rows


def monthly_report_trips(path: Path | None, month: Month, license_class: str) -> int | None:
    """TLC monthly report gives trips PER DAY (rounded); control total = per_day x days."""
    if path is None or not path.exists():
        return None
    with open(path, newline="", encoding="utf-8-sig") as fh:
        for row in csv.DictReader(fh):
            if row.get("Month/Year") == str(month) and row.get("License Class") == license_class:
                per_day = int(row["Trips Per Day"].replace(",", ""))
                return per_day * month.days
    return None


def reconcile(month: Month, cfg: Config, file_counts: dict[str, int], base_agg: FetchResult,
              monthly_report: Path | None, allow_unreconciled: bool,
              checks: list[Check], recons: list[Recon] | None = None) -> tuple[list[Recon], str]:
    """Compare file trip counts with the controls. `recons` (if given) is filled in place, so the caller
    still has the comparison when a mismatch raises ReconciliationError."""
    tol = cfg.reconciliation["tolerance_pct"]
    recons = recons if recons is not None else []

    # Control 1 (primary): Open Data base aggregate, licensee grain, exact integers.
    rows = load_base_aggregate_rows(base_agg)
    if rows is not None:
        keys = [r.get("base_license_number") for r in rows]
        dupes = sorted({k for k in keys if keys.count(k) > 1})
        _record(checks, Check("CTL01", "Control data grain: one row per base per month", "WARN",
                              "WARN" if dupes else "PASS", observed=f"{len(rows)} rows, duplicates={dupes}",
                              expected="unique base_license_number"))
    for lic in cfg.licensees:
        if not lic["control_key"] or lic["hvfhs_license_num"] not in file_counts:
            continue
        control = None
        if rows is not None:
            matched = [int(float(r["total_dispatched_trips"])) for r in rows
                       if r.get("base_license_number") == lic["control_key"]]
            control = sum(matched) if matched else None
        if rows is not None:
            missing = "NOT_IN_CONTROL"      # month published but this licensee absent
        elif base_agg.manifest.get("status") == "not_published":
            missing = "NOT_PUBLISHED"       # TLC has not published the month yet (~2-month lag)
        else:
            missing = "UNAVAILABLE"         # API failed and nothing preserved
        recons.append(_compare(month, "opendata_base_aggregate", lic["hvfhs_license_num"],
                               file_counts[lic["hvfhs_license_num"]], control, tol, missing))

    # Control 2 (fallback): TLC monthly industry report, license-class grain, rounded per-day average.
    total_file = sum(file_counts.values())
    control_total = monthly_report_trips(monthly_report, month, cfg.sources["monthly_reports"]["license_class"])
    recons.append(_compare(month, "tlc_monthly_report", "ALL", total_file, control_total, tol,
                           "NOT_PUBLISHED" if monthly_report else "UNAVAILABLE"))

    for r in recons:
        status = {"MATCH": "PASS", "MISMATCH": "FAIL"}.get(r.status, "SKIPPED")
        prefix = "REC01" if r.control_source == "opendata_base_aggregate" else "REC02"
        _record(checks, Check(f"{prefix}-{r.scope}",
                              f"File trips vs {r.control_source} ({r.scope})", "FATAL", status,
                              observed=f"{r.file_trips:,}" if r.file_trips is not None else "",
                              expected=f"{r.control_trips:,} +/- {tol}%" if r.control_trips is not None else r.status,
                              detail=f"diff {r.diff:+,} ({r.diff_pct:+.4f}%)" if r.diff is not None else ""))

    mismatches = [r for r in recons if r.status == "MISMATCH"]
    primary = [r for r in recons if r.control_source == "opendata_base_aggregate"]
    fallback = [r for r in recons if r.control_source == "tlc_monthly_report"]
    if mismatches:
        if not allow_unreconciled:
            raise ReconciliationError(
                "; ".join(f"{r.scope}: file {r.file_trips:,} vs {r.control_source} {r.control_trips:,} "
                          f"({r.diff_pct:+.3f}%)" for r in mismatches))
        status = "UNRECONCILED"
    elif primary and all(r.status == "MATCH" for r in primary):
        status = "RECONCILED"
    elif fallback and fallback[0].status == "MATCH":
        status = "RECONCILED_FALLBACK"
    else:
        status = "UNRECONCILED"
    log.info("retrieval completeness status for %s: %s", month, status)
    return recons, status
