"""The verdict logic and the M5 significance test, on hand-built aggregates (every state covered)."""

from __future__ import annotations

import math

import duckdb
import pytest

from wavpipe.config import ROOT, load_config, render_sql, sql_literal
from wavpipe.pipeline import metric_views_sql

# (licensee, n_records, n_clean, n_clean_under) -> expected status
SCENARIOS = {
    "HVA": ((100, 95, 93), "MEETS"),              # low = 0.93 >= 0.90: proven, whatever the 5 quarantined are
    "HVB": ((100, 85, 60), "BELOW"),              # high = 0.75 < 0.90: proven BELOW despite 85% coverage
    "HVC": ((100, 95, 86), "MEETS (estimate)"),   # bounds 0.86-0.91 straddle; coverage 95% -> clean rate 90.5%
    "HVD": ((100, 95, 84), "BELOW"),              # high = 0.89 < 0.90: proven BELOW
    "HVE": ((100, 40, 38), "NOT MEASURABLE"),     # bounds 0.38-0.98 straddle; coverage 40%
    "HVF": ((100, 95, 85), "BELOW (estimate)"),   # bounds 0.85-0.90 straddle; clean rate 89.5%
}


@pytest.fixture(scope="module")
def con(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("logic")
    cfg = load_config(root=tmp)
    con = duckdb.connect()
    con.execute(render_sql("00_schema.sql"))
    lic = tmp / "lic.csv"
    lic.write_text("hvfhs_license_num,brand,control_key,note\n" + "".join(f"{k},{k},," + "\n" for k in SCENARIOS))
    zones = tmp / "z.csv"
    zones.write_text('"LocationID","Borough","Zone","service_zone"\n1,"Queens","Z","Boro Zone"\n')
    con.execute(render_sql("10_reference.sql", zones_path=sql_literal(zones), licensees_path=sql_literal(lic)))
    con.execute("INSERT INTO month_status VALUES ('2026-01', 'RECONCILED', 'FINAL', 'x', 1, 'r', now())")
    for lic_id, ((n, clean, under), _) in SCENARIOS.items():
        con.execute("""INSERT INTO agg_segment_month VALUES ('2026-01', 'dispatcher', ?, true, 'ALL', 'ALL',
                       ?, ?, ?, ?, 300, 500, 60, 400, 320, 90, 410)""", [lic_id, n, clean, under, under])
        con.execute("""INSERT INTO agg_segment_month VALUES ('2026-01', 'dispatcher', ?, false, 'ALL', 'ALL',
                       1000, 990, 950, 950, 200, 500, 40, 250, 210, 45, 255)""", [lic_id])
    # Two cells for HVA: 1,000 requests at 70% (clearly below) and 300 at 87% (below, but within noise).
    con.execute("""INSERT INTO agg_segment_month VALUES ('2026-01', 'cell', 'HVA', true, 'Queens', '20-24 evening',
                   1000, 1000, 700, 700, 300, 800, 60, 400, 320, 90, 410)""")
    con.execute("""INSERT INTO agg_segment_month VALUES ('2026-01', 'cell', 'HVA', true, 'Bronx', '06-10 AM peak',
                   300, 300, 261, 261, 300, 620, 60, 400, 320, 90, 410)""")
    con.execute(metric_views_sql(cfg))
    return con


def test_verdict_states(con):
    got = dict(con.execute("SELECT hvfhs_license_num, kpi_status FROM m1_m2_kpi_dispatcher_month").fetchall())
    assert got["HVA"] == "MEETS"
    assert got["HVB"] == "BELOW"
    assert got["HVC"] == "MEETS (estimate)"
    assert got["HVD"] == "BELOW"          # high = (84 + 5) / 100 = 0.89 < 0.90: provably below, not an estimate
    assert got["HVE"] == "NOT MEASURABLE"
    assert got["HVF"] == "BELOW (estimate)"


def test_proven_below_is_reported_even_when_coverage_is_under_the_line(con):
    row = con.execute("""SELECT kpi_status, measurable, m1_service_rate_10min, m1_bound_high
                         FROM m1_m2_kpi_dispatcher_month WHERE hvfhs_license_num = 'HVB'""").fetchone()
    assert row[0] == "BELOW" and row[1] is False and row[2] is None and row[3] == pytest.approx(0.75)


def wilson(p: float, n: int, z: float = 1.959964) -> tuple[float, float]:
    centre = p + z * z / (2 * n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (centre - half) / (1 + z * z / n), (centre + half) / (1 + z * z / n)


def test_m5_significance_uses_the_wilson_interval(con):
    cells = {r[0]: r[1:] for r in con.execute("""
        SELECT pu_borough, wilson_low, wilson_high, below_target, significantly_below
        FROM m5_service_gap_cells WHERE hvfhs_license_num = 'HVA'""").fetchall()}
    lo, hi = wilson(0.70, 1000)
    assert cells["Queens"] == (pytest.approx(lo), pytest.approx(hi), True, True)
    lo, hi = wilson(0.87, 300)
    assert hi > 0.90                                     # 87% on 300 requests is not distinguishable from 90%
    assert cells["Bronx"] == (pytest.approx(lo), pytest.approx(hi), True, False)
