"""Diagrams as code: renders the source map, workflow + data model, and pipeline flow as SVG.

    python diagrams/build_diagrams.py        # writes diagrams/*.svg (and *.png if rsvg-convert exists)
"""

from __future__ import annotations

import shutil
import subprocess
import textwrap
from html import escape
from pathlib import Path

OUT = Path(__file__).resolve().parent
FONT = "Helvetica, Arial, sans-serif"
INK, MUTED, LINE = "#1b2733", "#4a5a6a", "#8a9aab"
NAVY, NAVY_BG = "#1f3a5f", "#e8eef6"
TEAL, TEAL_BG = "#0f6e6e", "#e3f3f1"
AMBER, AMBER_BG = "#8a5a00", "#fdf3dc"
RED, RED_BG = "#a3261f", "#fbe9e7"
GREEN, GREEN_BG = "#2d6a2d", "#e8f3e5"
GREY_BG = "#f4f6f8"


class Svg:
    def __init__(self, w: int, h: int, title: str):
        self.w, self.h = w, h
        self.parts: list[str] = [
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}" '
            f'font-family="{FONT}" role="img" aria-label="{escape(title)}">',
            f'<title>{escape(title)}</title>',
            '<defs><marker id="arr" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" '
            f'orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="{MUTED}"/></marker>'
            '<marker id="arr-red" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" '
            f'orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="{RED}"/></marker></defs>',
            f'<rect width="{w}" height="{h}" fill="#ffffff"/>',
        ]

    def text(self, x: float, y: float, s: str, size: int = 13, weight: str = "normal", color: str = INK,
             anchor: str = "start", italic: bool = False) -> None:
        style = ' font-style="italic"' if italic else ""
        self.parts.append(f'<text x="{x}" y="{y}" font-size="{size}" font-weight="{weight}" fill="{color}" '
                          f'text-anchor="{anchor}"{style}>{escape(s)}</text>')

    def lines(self, x: float, y: float, lines: list[str], size: int = 12, color: str = INK,
              gap: float = 1.35, weight: str = "normal", anchor: str = "start") -> float:
        for i, line in enumerate(lines):
            self.text(x, y + i * size * gap, line, size, weight, color, anchor)
        return y + len(lines) * size * gap

    def box(self, x: float, y: float, w: float, h: float, fill: str = GREY_BG, stroke: str = LINE,
            dash: bool = False, radius: int = 6, width: float = 1.2) -> None:
        d = ' stroke-dasharray="6 4"' if dash else ""
        self.parts.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{radius}" fill="{fill}" '
                          f'stroke="{stroke}" stroke-width="{width}"{d}/>')

    def card(self, x: float, y: float, w: float, h: float, title: str, body: list[str], fill: str, stroke: str,
             title_color: str | None = None, size: int = 12, dash: bool = False) -> None:
        self.box(x, y, w, h, fill, stroke, dash)
        self.text(x + 10, y + 19, title, size + 1, "bold", title_color or stroke)
        for i, line in enumerate(body):
            sub = len(line) > 3 and line[0] == "S" and line[1].isdigit() and line[2:4] == "  "
            self.text(x + 10, y + 19 + size * 1.5 + i * size * 1.35, line, size + (1 if sub else 0),
                      "bold" if sub else "normal", (title_color or stroke) if sub else INK)

    def arrow(self, x1: float, y1: float, x2: float, y2: float, color: str = MUTED, dash: bool = False,
              label: str | None = None, label_dy: float = -6, red: bool = False) -> None:
        d = ' stroke-dasharray="5 4"' if dash else ""
        marker = "arr-red" if red else "arr"
        self.parts.append(f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{color}" stroke-width="1.6"'
                          f'{d} marker-end="url(#{marker})"/>')
        if label:
            self.text((x1 + x2) / 2, (y1 + y2) / 2 + label_dy, label, 11, "normal", color, "middle")

    def path(self, d: str, color: str = MUTED, dash: bool = False, red: bool = False) -> None:
        dd = ' stroke-dasharray="5 4"' if dash else ""
        marker = "arr-red" if red else "arr"
        self.parts.append(f'<path d="{d}" fill="none" stroke="{color}" stroke-width="1.6"{dd} '
                          f'marker-end="url(#{marker})"/>')

    def save(self, name: str) -> Path:
        path = OUT / name
        path.write_text("\n".join(self.parts + ["</svg>"]) + "\n")
        return path


def wrap(s: str, width: int) -> list[str]:
    return textwrap.wrap(s, width)


# --------------------------------------------------------------------------------------- 1. source map
def source_map() -> Path:
    s = Svg(1480, 760, "Source map: business question to metric")
    s.text(20, 32, "Source map — business question → required information → source system → fields → metric",
           19, "bold", NAVY)
    s.text(20, 54, "Client: NYC TLC accessibility compliance. KPI: share of WAV requests with vehicle on scene "
                   "in under 10 minutes (35 RCNY §59B-17(f)(3), target ≥ 90%).", 13, color=MUTED)
    cols = [(20, 300, "Business question"), (340, 250, "Required information"), (610, 400, "Source system"),
            (1030, 250, "Fields used (table / file / API)"), (1300, 160, "Metric / output")]
    for x, w, label in cols:
        s.box(x, 70, w, 30, NAVY, NAVY, radius=4)
        s.text(x + 10, 90, label, 13, "bold", "#ffffff")

    rows = [
        dict(q="Does each dispatcher (Uber, Lyft) serve ≥ 90% of WAV requests in under 10 minutes?",
             info="Which trips were WAV requests; when requested; when the vehicle arrived; who dispatched",
             src=("S1  TLC High-Volume FHV trip records",
                  ["Owner: TLC; submitted by Uber/Lyft (§59D-14)", "Mode: FILE — Parquet over HTTPS, monthly",
                   "Grain: 1 row = 1 COMPLETED trip; no trip ID", "~21M rows / 0.5 GB per month; ~2-month lag"]),
             fields="wav_request_flag, request_datetime, on_scene_datetime, hvfhs_license_num",
             metric=("M1  10-min service rate", "M2  evidence coverage"), color=(TEAL, TEAL_BG)),
        dict(q="Are WAV riders served as well as other riders? Where does their extra wait accrue?",
             info="The same event times for non-WAV trips; time from arrival to boarding",
             src=("S1  (same file, non-WAV rows as the baseline)",
                  ["on_scene_datetime: dictionary says 'WAV only';", "observed 100% populated in 2026 files"]),
             fields="+ pickup_datetime, wav_match_flag",
             metric=("M3  P90 gap vs non-WAV", "M4  curbside share"), color=(TEAL, TEAL_BG)),
        dict(q="Where and when is service below the standard?",
             info="Borough of each pickup; hour of each request",
             src=("S2  TLC taxi zone lookup",
                  ["Owner: TLC · Mode: FILE — CSV over HTTPS", "Grain: 1 row = 1 zone (265; 264/265 = unknown)"]),
             fields="PULocationID → LocationID (unique) → Borough",
             metric=("M5  share of demand in", "     below-target cells"), color=(TEAL, TEAL_BG)),
        dict(q="Did we receive the whole month? Can the numbers be called FINAL?",
             info="Trip counts published independently of the file, per licensee and in total",
             src=("S3  Open Data 'FHV Base Aggregate Report'",
                  ["Owner: TLC · Mode: API — Socrata SODA JSON, paginated", "Grain: base × month ('UBER', 'LYFT' rows)",
                   "S4  TLC monthly industry report", "Owner: TLC · Mode: FILE — CSV; grain: class × month,",
                   "trips PER DAY (rounded) — more timely than S3"]),
             fields="total_dispatched_trips (S3); Trips Per Day × days in month (S4)",
             metric=("FINAL / PROVISIONAL /", "month refused"), color=(AMBER, AMBER_BG)),
    ]
    y = 112
    heights = [118, 92, 88, 128]
    for row, h in zip(rows, heights):
        stroke, fill = row["color"]
        s.box(20, y, 300, h, GREY_BG)
        s.lines(30, y + 22, wrap(row["q"], 40), 13, weight="bold")
        s.box(340, y, 250, h, GREY_BG)
        s.lines(350, y + 22, wrap(row["info"], 34), 12)
        title, body = row["src"]
        s.card(610, y, 400, h, title, body, fill, stroke)
        s.box(1030, y, 250, h, "#ffffff")
        s.lines(1040, y + 22, wrap(row["fields"], 33), 12, color=INK)
        s.box(1300, y, 160, h, NAVY_BG, NAVY)
        s.lines(1310, y + 24, list(row["metric"]), 12, weight="bold", color=NAVY)
        mid = y + h / 2
        for x1, x2 in ((320, 340), (590, 610), (1010, 1030), (1280, 1300)):
            s.arrow(x1, mid, x2 - 1, mid)
        y += h + 14

    # the gap row
    h = 70
    s.box(20, y, 300, h, RED_BG, RED, dash=True)
    s.lines(30, y + 22, wrap("How many WAV requests were never served, or cancelled?", 40), 13, weight="bold",
            color=RED)
    s.box(340, y, 250, h, RED_BG, RED, dash=True)
    s.lines(350, y + 22, wrap("Request-level records including cancellations", 34), 12, color=RED)
    s.card(610, y, 400, h, "GAP — no public source", ["Lives only in Uber/Lyft dispatch systems;",
                                                      "trip records contain completed trips only."],
           RED_BG, RED, dash=True)
    s.box(1030, y, 430, h, RED_BG, RED, dash=True)
    s.lines(1040, y + 22, ["Limitation: every rate is measured on completed trips.",
                           "If unserved requests count as failures, true compliance is lower."], 12, color=RED)
    y += h + 22

    s.text(20, y, "Grain mismatches handled explicitly", 14, "bold", NAVY)
    s.lines(20, y + 20, [
        "• Trips (1 row = 1 trip) are aggregated to licensee × month before comparing with S3, and to all-HVFHS × month "
        "for S4 (per-day average × days; rounding tolerance ±0.5%).",
        "• S3/S4 are tabulated from the same licensee submissions as S1: they prove we RECEIVED the whole file, not that "
        "the licensee SUBMITTED every trip.",
        "• Zone lookup joins on its primary key (many trips → one zone) with a LEFT JOIN: an unknown zone is flagged "
        "(REF02), never silently dropped.",
    ], 12, color=INK)
    return s.save("source_map.svg")


# ---------------------------------------------------------------------------- 2. workflow + data model
def workflow_model() -> Path:
    s = Svg(1480, 890, "Workflow and data model")
    s.text(20, 32, "Workflow — what happens to one wheelchair-accessible (WAV) request", 19, "bold", NAVY)
    ys, bh, bw = 70, 88, 210
    xs = [40, 400, 760, 1120]
    events = [("REQUESTED", "request_datetime", "Rider asks for a WAV", "(wav_request_flag = 'Y')"),
              ("VEHICLE ON SCENE", "on_scene_datetime", "WAV arrives at pickup", "(wav_match_flag = 'Y')"),
              ("PICKED UP", "pickup_datetime", "Rider boarded; ramp /", "securement complete"),
              ("DROPPED OFF", "dropoff_datetime", "Trip completed", "(only completed trips exist)")]
    for x, (title, field, l1, l2) in zip(xs, events):
        s.card(x, ys, bw, bh, title, [field, l1, l2], TEAL_BG, TEAL)
    stages = [("response_s  —  REGULATED: must be < 10 min for ≥ 90%", AMBER),
              ("curbside_s  —  boarding (not regulated)", MUTED), ("ride_s", MUTED)]
    for i, (label, color) in enumerate(stages):
        x1, x2 = xs[i] + bw, xs[i + 1]
        s.arrow(x1 + 2, ys + bh / 2, x2 - 2, ys + bh / 2, color)
        s.lines((x1 + x2) / 2, ys + bh / 2 - 22, wrap(label, 26), 11, color=color, anchor="middle")
    # wait bracket
    by = ys + bh + 26
    s.parts.append(f'<path d="M{xs[0] + bw / 2},{by - 8} V{by} H{xs[2] + bw / 2} V{by - 8}" fill="none" '
                   f'stroke="{NAVY}" stroke-width="1.4"/>')
    s.text((xs[0] + xs[2] + bw) / 2, by + 18, "wait_s = response_s + curbside_s   (what the rider experiences;"
                                             " exact for clean records)", 12, "bold", NAVY, "middle")
    # unobserved branch
    s.path(f"M{xs[0] + 30},{ys + bh} V{ys + bh + 66}", RED, dash=True, red=True)
    s.card(xs[0] - 20, ys + bh + 68, 340, 76, "CANCELLED / NEVER SERVED",
           ["Not in any public data. The rule's", "denominator includes them; the trip", "files do not."],
           RED_BG, RED, dash=True)
    # actors
    s.card(xs[1], ys + bh + 70, 590, 76, "Actors, interventions, outcome", [
        "Dispatcher = HVFHS licensee (HV0003 Uber via base B03404; HV0005 Lyft via B03406)",
        "Intervention: WAV dispatch obligation + TLC compliance notice (30 days to comply)",
        "Outcome: served < 10 min (compliance) · rider wait · gap vs non-WAV riders"], GREY_BG, NAVY, NAVY)
    s.card(1020, ys + bh + 70, 420, 76, "Where delay accumulates (evidence, Apr–Jul 2026)", [
        "Uber WAV P90 8.1–9.3 min (≤ non-WAV); P50 ~1 min slower",
        "79–85% of WAV riders' EXTRA wait is curbside,",
        "i.e. after arrival — outside the 10-minute clock"], AMBER_BG, AMBER, AMBER)

    # ------------------------------------------------------------------ data model
    top = 345
    s.parts.append(f'<line x1="20" y1="{top - 18}" x2="1460" y2="{top - 18}" stroke="#d5dde6" stroke-width="1"/>')
    s.text(20, top + 8, "Data model (DuckDB) — keys, grain and why every join is safe", 19, "bold", NAVY)

    def entity(x: float, y: float, w: float, name: str, grain: str, cols: list[str], color: str, bg: str,
               h: float | None = None) -> tuple[float, float, float, float]:
        hh = h or 54 + 16 * len(cols)
        s.box(x, y, w, hh, bg, color)
        s.parts.append(f'<rect x="{x}" y="{y}" width="{w}" height="26" rx="6" fill="{color}"/>')
        s.text(x + 10, y + 18, name, 13, "bold", "#ffffff")
        s.text(x + 10, y + 42, grain, 11, "normal", MUTED, italic=True)
        for i, c in enumerate(cols):
            s.text(x + 10, y + 60 + i * 16, c, 12, "bold" if c.startswith(("PK", "FK")) else "normal")
        return x, y, w, hh

    raw = entity(20, top + 30, 300, "raw trips (Parquet, read in place)", "grain: 1 row = 1 completed trip",
                 ["PK (none in source) → source_row_id =", "   row position in the preserved file",
                  "hvfhs_license_num, PULocationID", "request / on_scene / pickup / dropoff",
                  "wav_request_flag, wav_match_flag"], MUTED, GREY_BG)
    lic = entity(400, top + 95, 280, "ref_licensee", "grain: 1 row = 1 HVFHS licence",
                 ["PK hvfhs_license_num", "brand (Uber, Lyft, …)", "control_key ('UBER' / 'LYFT')"], NAVY, NAVY_BG)
    zone = entity(400, top + 215, 280, "ref_zone", "grain: 1 row = 1 taxi zone (265)",
                  ["PK location_id", "borough, zone", "is_nyc_borough"], NAVY, NAVY_BG)
    fct = entity(760, top + 30, 330, "fct_wav_trip", "grain: 1 row = 1 WAV-requested trip",
                 ["PK (month, source_row_id)", "FK hvfhs_license_num → ref_licensee",
                  "FK pu_location_id → ref_zone", "response_s, curbside_s, wait_s, ride_s",
                  "failed_rules, warned_rules, is_clean", "(quarantined rows kept, with reasons)"], TEAL, TEAL_BG)
    agg = entity(760, top + 330, 330, "agg_segment_month", "grain: month × licensee × WAV × borough × band",
                 ["PK (month, level, licensee, wav, borough, band)", "n_records, n_clean, n_clean_under",
                  "P50/P90 response, mean wait parts", "level 'dispatcher' = rule grain"], TEAL, TEAL_BG)
    ctl = entity(1160, top + 30, 300, "evidence tables", "grain: month (× rule / check / control)",
                 ["validation_rule_result", "file_check, reconciliation",
                  "diag_negative_response, diag_segment", "PK month_status (FINAL/PROV.)",
                  "pipeline_run (audit, append-only)"],
                 AMBER, AMBER_BG)
    views = entity(1160, top + 330, 300, "metric views (always recomputed)", "grain: dispatcher × month (M5: cell)",
                   ["m1_m2_kpi_dispatcher_month", "m1_m2_kpi_ytd (additive counts)", "m3_response_gap",
                    "m4_stage_decomposition", "m5_service_gap_cells / _summary"], NAVY, NAVY_BG)

    s.arrow(raw[0] + raw[2], raw[1] + 40, fct[0] - 2, fct[1] + 40, label="WAV-requested rows", label_dy=-7)
    s.path(f"M{raw[0] + raw[2] / 2},{raw[1] + raw[3]} V{agg[1] + 60} H{agg[0] - 2}")
    s.text(raw[0] + raw[2] / 2 + 8, agg[1] + 52, "all rows (WAV and non-WAV), GROUPING SETS", 11, color=MUTED)
    s.arrow(lic[0] + lic[2], lic[1] + 50, fct[0] - 2, fct[1] + 92, label="1 : many", label_dy=-6)
    s.arrow(zone[0] + zone[2], zone[1] + 50, fct[0] - 2, fct[1] + 112, label="1 : many", label_dy=16)
    s.arrow(fct[0] + fct[2], fct[1] + 60, ctl[0] - 2, ctl[1] + 60)
    s.arrow(agg[0] + agg[2], agg[1] + 60, views[0] - 2, views[1] + 60)
    s.arrow(ctl[0] + ctl[2] / 2, ctl[1] + ctl[3], views[0] + views[2] / 2, views[1] - 2, label="          month_status",
            label_dy=4)
    s.lines(20, top + 492, [
        "Join safety: every join is a LEFT JOIN onto a PRIMARY KEY (many trips → one zone / one licensee), so it cannot drop "
        "or multiply trips. FKs are logical: enforced by rules REF01/REF02 (violations quarantined), not DB constraints.",
        "Tested: dispatcher totals = fact table = sum of cells (tests/test_metrics.py::test_aggregates_reconcile_to_the_fact_table).",
    ], 12, color=INK)
    return s.save("workflow_data_model.svg")


# ----------------------------------------------------------------------------------- 3. pipeline flow
def pipeline_flow() -> Path:
    s = Svg(1480, 560, "Pipeline flow and failure handling")
    s.text(20, 32, "Pipeline — python run_pipeline.py --month YYYY-MM   (per month: every check runs BEFORE the "
                   "publish transaction)", 19, "bold", NAVY)
    y, h = 70, 196
    stages = [
        ("1 INGEST", TEAL, TEAL_BG, ["4 sources, 2 modes", "(HTTPS files + SODA API)", "write .part → verify",
                                    "Content-Length → SHA-256", "→ atomic rename", "manifest.json per input",
                                    "reuse raw on rerun;", "--refresh: new file to", ".incoming, old kept in",
                                    "superseded/"]),
        ("2 PROFILE", TEAL, TEAL_BG, ["one pass over the raw", "file, before cleaning:", "nulls, EXACT distinct,",
                                     "min / max / mean", "+ raw response P01/", "P50/P99 per segment",
                                     "staged; published only", "with its month"]),
        ("3 VALIDATE file", AMBER, AMBER_BG, ["FIL01 schema contract", "FIL02 rows read = footer",
                                              "FIL03 every day present", "FIL04/07 low-volume days",
                                              "FIL05 month partition", "FIL06 error rows ≤ 5%"]),
        ("4 VALIDATE rows", AMBER, AMBER_BG, ["13 business rules", "schema · referential ·", "temporal · domain · grain",
                                              "ERROR → quarantine", "WARN → keep + count", "one SQL pass / month",
                                              "exact-duplicate check"]),
        ("5 RECONCILE", AMBER, AMBER_BG, ["file trips vs S3 API", "(licensee, exact)", "and S4 report (total)",
                                          "match → FINAL", "S3 not yet published", "+ S4 match → PROVISIONAL",
                                          "mismatch → refuse"]),
        ("6 MODEL", NAVY, NAVY_BG, ["ONE transaction:", "DELETE month;", "INSERT fact, aggregates,",
                                    "evidence, month_status", "PKs enforce grain", "→ rerun = replace,", "never append"]),
        ("7 METRICS + OUTPUT", NAVY, NAVY_BG, ["views m1–m5 (+ YTD)", "verdict from bounds:", "proven / estimate /",
                                               "withheld", "CSV exports, sorted", "scorecard.md",
                                               "validation/<month>/", "last_run.json"]),
    ]
    w, gap = 180, 22
    for i, (title, color, bg, body) in enumerate(stages):
        x = 20 + i * (w + gap)
        s.card(x, y, w, h, title, body, bg, color, color, size=12)
        if i < len(stages) - 1:
            s.arrow(x + w + 1, y + h / 2, x + w + gap - 1, y + h / 2)

    fy = y + h + 60
    s.text(20, fy - 14, "Failure handling — what happens when…", 15, "bold", RED)
    cases = [
        ("a download drops / truncates", "retry ×3 with backoff; .part deleted; never renamed into place"),
        ("the month is not published (404)", "fail fast, no retries; other months continue"),
        ("the SODA API is down", "reuse preserved pages; if none → control UNAVAILABLE → PROVISIONAL"),
        ("a raw file changed / refresh fails", "SHA-256 ≠ manifest → stop; failed refresh keeps old raw"),
        ("the schema changes", "missing / retyped column → stop; new column → WARN only"),
        ("rows are impossible or duplicated", "quarantined with reasons; verdict proven / estimate / withheld"),
        ("counts disagree with a control", "month REFUSED (June 2026: −2.14%); evidence kept, nothing published"),
        ("the pipeline runs twice", "byte-identical outputs; month replaced in one transaction"),
    ]
    for i, (when, then) in enumerate(cases):
        col, row = i % 2, i // 2
        x, yy = 20 + col * 725, fy + row * 44
        s.box(x, yy, 715, 36, RED_BG if "REFUSED" in then or "stopped" in then or "stop" in then else GREY_BG,
              "#d9c4c1")
        s.text(x + 10, yy + 23, when, 12, "bold", RED)
        s.text(x + 255, yy + 23, "→ " + then, 12)
    s.text(20, fy + 4 * 44 + 18, "A failed month is rolled back: its previously published version stays untouched "
                                 "(last known good). Every attempt is appended to pipeline_run. Exit code 1 if any "
                                 "month failed.", 12, "bold", NAVY)
    return s.save("pipeline_flow.svg")


if __name__ == "__main__":
    for svg in (source_map(), workflow_model(), pipeline_flow()):
        print("wrote", svg.relative_to(OUT.parent))
        if shutil.which("rsvg-convert"):
            png = svg.with_suffix(".png")
            subprocess.run(["rsvg-convert", "-z", "1.5", "-o", str(png), str(svg)], check=True)
            print("wrote", png.relative_to(OUT.parent))
