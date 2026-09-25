"""Metrics: KPI arithmetic, the measurability gate, and agreement with an independent Python reference."""

from __future__ import annotations

import math

import pytest

from conftest import query

UNCLEAN_MARKERS = {"X_TMP01", "X_REF02", "X_SCH01", "X_TMP03", "X_TMP04", "X_TMP02"}
UNSEGMENTED_MARKERS = {"X_SCH02", "X_REF01"}      # cannot be attributed / classified


def quantile_cont(values: list[float], q: float) -> float:
    v = sorted(values)
    pos = (len(v) - 1) * q
    lo, hi = math.floor(pos), math.ceil(pos)
    return v[lo] + (pos - lo) * (v[hi] - v[lo])


def reference_clean(rows, lic: str, wav: str) -> list[dict]:
    """Independent re-implementation of 'clean & segmented' using the fixture's design markers."""
    out, seen_dup = [], False
    for r in rows:
        if r["hvfhs_license_num"] != lic or r["wav_request_flag"] != wav:
            continue
        base = r["dispatching_base_num"]
        if base in UNCLEAN_MARKERS or base in UNSEGMENTED_MARKERS:
            continue
        if base == "X_DOM04":
            if seen_dup:
                continue
            seen_dup = True
        out.append(r)
    return out


def secs(a, b) -> float:
    return (b - a).total_seconds()


@pytest.fixture(scope="module")
def module_run(tmp_path_factory):
    """One published synthetic month shared by every metric test (read-only queries)."""
    from fixtures import MONTH, build_rows, pad_to_multiple_of_days, standard_session
    from wavpipe.config import Month, load_config
    from wavpipe.pipeline import run
    tmp = tmp_path_factory.mktemp("metrics")
    cfg = load_config(root=tmp, overrides={"kpi.min_cell_requests": 5, "http.backoff_s": 0})
    rows = pad_to_multiple_of_days(build_rows())
    summary = run([Month.parse(MONTH)], cfg, session=standard_session(rows, tmp))
    assert summary["months"][MONTH]["status"] == "PUBLISHED"
    return cfg, rows


@pytest.fixture
def cfg(module_run):
    return module_run[0]


@pytest.fixture
def rows(module_run):
    return module_run[1]


@pytest.fixture
def published(module_run):
    return module_run


def kpi(cfg, lic):
    cols = ["wav_requests", "wav_requests_clean", "m2_evidence_coverage", "naive_rate_unvalidated",
            "m1_service_rate_10min", "m1_bound_low", "m1_bound_high", "kpi_status", "verdict_basis",
            "clean_record_rate", "measurable"]
    row = query(cfg, f"SELECT {', '.join(cols)} FROM m1_m2_kpi_dispatcher_month WHERE hvfhs_license_num = ?", [lic])[0]
    return dict(zip(cols, row))


def test_kpi_counts_exactly_600s_as_a_miss_and_meets_at_90pct(cfg, published):
    k = kpi(cfg, "HV0003")
    assert (k["wav_requests"], k["wav_requests_clean"]) == (21, 20)
    assert k["m1_service_rate_10min"] == pytest.approx(18 / 20)       # 600 s is not "under ten minutes"
    assert k["m1_bound_low"] == pytest.approx(18 / 21)
    assert k["m1_bound_high"] == pytest.approx(19 / 21)
    # 90.0% >= 90% target, but the bounds straddle 90% -> an estimate, not a proven verdict
    assert k["kpi_status"] == "MEETS (estimate)"
    assert k["verdict_basis"].startswith("estimate")
    assert k["naive_rate_unvalidated"] == pytest.approx(19 / 21)      # the -300 s record "passes" naively


def test_systematic_defect_withholds_kpi_instead_of_reporting_naive_100pct(cfg, published):
    k = kpi(cfg, "HV0005")
    assert k["naive_rate_unvalidated"] == pytest.approx(1.0)           # what an unvalidated dashboard shows
    assert k["m2_evidence_coverage"] == pytest.approx(0.4)
    assert k["m1_service_rate_10min"] is None and not k["measurable"]
    assert k["clean_record_rate"] == pytest.approx(1.0)               # what silently filtering would claim
    assert k["kpi_status"] == "NOT MEASURABLE"
    gap = query(cfg, "SELECT wav_response_p90_min, m3_p90_gap_min FROM m3_response_gap WHERE hvfhs_license_num='HV0005'")
    assert gap == [(None, None)]
    assert query(cfg, "SELECT count(*) FROM m5_service_gap_cells WHERE hvfhs_license_num='HV0005'") == [(0,)]


def test_m3_m4_match_independent_reference(cfg, rows, published):
    wav = reference_clean(rows, "HV0003", "Y")
    non = reference_clean(rows, "HV0003", "N")
    resp = lambda rs: [secs(r["request_datetime"], r["on_scene_datetime"]) for r in rs]   # noqa: E731
    curb = lambda rs: [secs(r["on_scene_datetime"], r["pickup_datetime"]) for r in rs]    # noqa: E731
    wait = lambda rs: [secs(r["request_datetime"], r["pickup_datetime"]) for r in rs]     # noqa: E731
    mean = lambda xs: sum(xs) / len(xs)                                                    # noqa: E731

    m3 = query(cfg, """SELECT wav_response_p50_min, wav_response_p90_min, nonwav_response_p90_min, m3_p90_gap_min
                       FROM m3_response_gap WHERE hvfhs_license_num = 'HV0003'""")[0]
    assert m3[0] == pytest.approx(quantile_cont(resp(wav), 0.5) / 60)
    assert m3[1] == pytest.approx(quantile_cont(resp(wav), 0.9) / 60)
    assert m3[2] == pytest.approx(quantile_cont(resp(non), 0.9) / 60)
    assert m3[3] == pytest.approx((quantile_cont(resp(wav), 0.9) - quantile_cont(resp(non), 0.9)) / 60)

    m4 = query(cfg, """SELECT extra_wait_min, extra_response_min, extra_curbside_min, m4_curbside_share_of_extra_wait
                       FROM m4_stage_decomposition WHERE hvfhs_license_num = 'HV0003'""")[0]
    extra_wait = mean(wait(wav)) - mean(wait(non))
    extra_curb = mean(curb(wav)) - mean(curb(non))
    assert m4[0] == pytest.approx(extra_wait / 60)
    assert m4[1] + m4[2] == pytest.approx(m4[0])                       # additive decomposition holds
    assert m4[2] == pytest.approx(extra_curb / 60)
    assert m4[3] == pytest.approx(extra_curb / extra_wait)


def test_m5_flags_the_low_cell_but_not_as_significant_on_six_requests(cfg, published):
    cells = query(cfg, """SELECT pu_borough, hour_band, wav_requests_clean, wav_service_rate_10min, below_target,
                                 significantly_below
                          FROM m5_service_gap_cells WHERE hvfhs_license_num = 'HV0003' ORDER BY pu_borough""")
    assert cells == [("Bronx", "20-24 evening", 6, pytest.approx(4 / 6), True, False),
                     ("Manhattan", "10-16 midday", 14, pytest.approx(1.0), False, False)]
    summary = query(cfg, """SELECT m5_share_of_demand_in_below_target_cells, share_of_demand_below_point_estimate
                            FROM m5_service_gap_summary WHERE hvfhs_license_num = 'HV0003'""")[0]
    assert summary == (pytest.approx(0.0), pytest.approx(6 / 20))


def test_aggregates_reconcile_to_the_fact_table(cfg, published):
    # Dispatcher-level WAV totals == fact table; cell rows sum back to the dispatcher row (no join fan-out).
    fact = dict(query(cfg, "SELECT hvfhs_license_num, count(*) FROM fct_wav_trip GROUP BY 1"))
    disp = dict(query(cfg, """SELECT hvfhs_license_num, n_records FROM agg_segment_month
                              WHERE level = 'dispatcher' AND wav_requested"""))
    cells = dict(query(cfg, """SELECT hvfhs_license_num, sum(n_records) FROM agg_segment_month
                               WHERE level = 'cell' AND wav_requested GROUP BY 1"""))
    assert fact == disp == cells == {"HV0003": 21, "HV0005": 10}


def test_ytd_rollup_is_additive(cfg, published):
    ytd = query(cfg, "SELECT wav_requests, m1_service_rate_10min, kpi_status FROM m1_m2_kpi_ytd WHERE hvfhs_license_num='HV0003'")
    assert ytd == [(21, pytest.approx(0.9), "MEETS (estimate)")]


def test_clean_only_rate_is_reported_for_diagnosis_but_not_as_the_kpi(cfg, published):
    k = query(cfg, """SELECT clean_record_rate, m1_service_rate_10min FROM m1_m2_kpi_dispatcher_month
                      WHERE hvfhs_license_num = 'HV0005'""")[0]
    assert k == (pytest.approx(1.0), None)


def test_negative_response_signature_separates_prebooked_from_unknown(cfg, published):
    sig = dict(query(cfg, """SELECT hvfhs_license_num, signature FROM dq_negative_response_signature
                             WHERE wav_requested"""))
    assert sig["HV0003"].startswith("pre-booked-ride signature")
    assert sig["HV0005"].startswith("no pre-booking signature")
