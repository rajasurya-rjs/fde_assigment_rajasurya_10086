# Metric definitions

All metrics are SQL views in [`../sql/40_metric_views.sql`](../sql/40_metric_views.sql), recomputed from the
modelled tables on every run and exported to `outputs/metrics/*.csv`. Thresholds live in
[`../config/pipeline.yaml`](../config/pipeline.yaml).

**Common scope:**
- Completed HVFHV trips attributable to a known licensee, with a valid WAV flag.
- "Clean" means passing every ERROR rule.
- "WAV" means `wav_request_flag = 'Y'`.
- The dispatcher is the HVFHS licensee.
- Timestamps are NYC local time.

---

## M1 — WAV 10-minute service rate (project KPI)

| Field | Definition |
|---|---|
| Definition | Share of clean WAV-requested trips whose vehicle was on scene in under 10 minutes of the request |
| Formula | `n_clean_under / n_clean`, where `n_clean_under = #(clean AND on_scene − request < 600 s)` |
| Unit / grain | % · dispatcher × month (the grain the rule is enforced at). Calendar-year roll-up from additive counts (`m1_m2_kpi_ytd`). |
| Source fields | `request_datetime`, `on_scene_datetime`, `wav_request_flag`, `hvfhs_license_num` (S1) |
| Filters | WAV-requested; clean; licensee known |
| Validation dependency | All ERROR rules. **M1 is the clean-record rate**: validation does not change the formula, it decides whether the formula may be used. |
| Bounds | [`n_clean_under / n_all`, `(n_clean_under + n_quarantined) / n_all`]: every quarantined record counted as a failure, then as a pass. |
| Verdict | **MEETS / BELOW (proven)** if both bounds are on the same side of 90%. **MEETS / BELOW (estimate)** if they straddle 90% and M2 ≥ 90% (the quarantined records are then treated as out of scope, a stated assumption). **NOT MEASURABLE** if they straddle 90% and M2 < 90%. A provable failure is reported as BELOW even when coverage is low. Tested for all five states in `tests/test_decision_logic.py`. |
| Diagnostics | The **naive rate** (no validation) is shown next to M1 so the reader can see what validation changed. |
| Interpretation | ≥ 90% = meets the standard *on completed trips*. Unserved requests are invisible, so this is an upper bound on regulatory compliance. |
| Decision supported | Compliance review / notice under §59B-17(f)(3)(iii); monthly monitoring toward the calendar-year evaluation. |

## M2 — Evidence coverage

| Field | Definition |
|---|---|
| Definition | Share of a dispatcher's WAV records whose response time can be trusted (they pass every ERROR rule) |
| Formula | `n_clean / n_all` |
| Unit / grain | % · dispatcher × month |
| Source fields | All the fields the ERROR rules use |
| Threshold | **90%: a stated policy line, not a mathematical necessity.** It is set equal to the standard's own 10% tolerance and is configurable (`kpi.measurability_gate`). It only matters when the bounds straddle the target; a proven verdict never needs it. |
| Interpretation | Below the line, with bounds straddling the target, the dispatcher's own data can demonstrate neither compliance nor non-compliance. |
| Decision supported | Whether to issue a **data-correction request** before any compliance determination. |

## M3 — WAV response-time gap at P90

| Field | Definition |
|---|---|
| Definition | P90 of `on_scene − request` for WAV requests minus the same for non-WAV requests, same dispatcher and month |
| Formula | `quantile_cont(response_s, 0.9 | WAV, clean) − quantile_cont(response_s, 0.9 | non-WAV, clean)`, in minutes; P50s reported alongside |
| Unit / grain | minutes · dispatcher × month |
| Why P90 | It is the continuous twin of the standard: "90% under 10 min" ⇔ P90 < 10 min. P90 also shows the tail that averages hide. |
| Validation dependency | Clean rows; withheld for NOT MEASURABLE dispatchers |
| Interpretation | > 0: wheelchair users wait longer for a vehicle than other riders of the same company. ≤ 0: parity **in the tail only**; always read it with the P50s (Uber WAV riders wait ~1 min longer at the median). |
| Decision supported | Equity monitoring; this is the comparison TLC gave as the reason for requiring on-scene reporting (2025 rule). |

## M4 — Curbside share of WAV riders' extra wait

| Field | Definition |
|---|---|
| Definition | Of the extra mean wait (request → pickup) WAV riders experience relative to non-WAV riders, the share that accrues *after* the vehicle arrives |
| Formula | `(mean curbside_WAV − mean curbside_nonWAV) / (mean wait_WAV − mean wait_nonWAV)`. For clean rows `wait = response + curbside` exactly, so the extra wait decomposes additively into before-arrival (regulated) and after-arrival (unregulated) parts. |
| Unit / grain | % (plus minutes) · dispatcher × month |
| Validation dependency | Clean rows (TMP02/TMP03 guarantee non-negative stages); DOM03 caveat (arrival = boarding at the second) |
| Interpretation | High = the 10-minute clock misses most of what wheelchair users experience. Some curbside time is inherent (ramp, securement), so this shows *where* time accrues, not that a failure occurred. |
| Decision supported | Whether the standard (or TLC reporting) should also track request → pickup; driver-training and equipment focus. |

## M5 — Share of WAV demand in below-target cells

| Field | Definition |
|---|---|
| Definition | Share of clean WAV requests made in pickup-borough × request-time-band cells that are **significantly** below 90% |
| Formula | `Σ n_clean(cells whose 95% Wilson upper bound < 90%) / Σ n_clean(judged cells)`. A cell is judged only if n ≥ 100. Also reported: the point-estimate version, the share of late arrivals in those cells, and the non-WAV rate in the same cell. |
| Why a significance test | A cell of 300 requests at 87% has a 95% interval of about 83–90%; flagging it would send supply after noise. |
| Bands | 00–06 overnight, 06–10 AM peak, 10–16 midday, 16–20 PM peak, 20–24 evening (standard operating periods; fixed before looking at results) |
| Unit / grain | % · dispatcher × month (cell detail: `m5_service_gap_cells`) |
| Validation dependency | Clean rows; REF03 (non-borough zones excluded from cells); withheld for NOT MEASURABLE dispatchers |
| Interpretation | How much WAV demand gets below-standard service even when the citywide average passes. The non-WAV rate in the same cell separates a general supply shortage (both low) from a WAV-specific one (WAV low, non-WAV fine). |
| Decision supported | Where and when to target WAV supply (incentives, fleet positioning), and which windows to raise with the dispatcher. |

---

### Why not other metrics?

- **"WAV request fulfilled in a WAV" (wav_match given wav_request)** is 100% in every month. It is kept as rule
  DOM01 rather than as a metric, because a metric that cannot move informs no decision.
- **The mean response time** is dominated by the tail and has no regulatory meaning; P50/P90 and the 10-minute
  share are used instead.
- **WAV fleet size or utilisation** has no vehicle IDs in S1 to support it, so it is listed as a limitation
  instead of approximated.
