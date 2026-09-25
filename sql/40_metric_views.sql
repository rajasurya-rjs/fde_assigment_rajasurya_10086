-- Metric layer: views over the modelled tables, re-created each run from config values.
-- Views (not stored copies) mean a metric can never drift out of sync with the data it summarises.
-- Join discipline: every join below is on a full primary key of the joined table (1:1 or many:1).

-- M1 + M2 at the grain the rule is enforced: dispatcher x month.
--
-- Verdict logic (tests/test_metrics.py::test_verdict_logic_*):
--   bounds  = [every quarantined record a failure, every quarantined record a pass]
--   MEETS / BELOW                   bounds lie entirely on one side of the target: PROVEN, whatever the
--                                   quarantined records really are.
--   MEETS / BELOW (estimate)        bounds straddle the target, so the verdict rests on treating the
--                                   quarantined records as out of scope; accepted only when coverage >= gate
--                                   (an explicit policy line set equal to the standard's 10% tolerance).
--   NOT MEASURABLE                  bounds straddle the target and coverage < gate: no defensible verdict.
-- M1 (the published rate) IS the clean-record rate; what validation adds is WHEN it may be used.
CREATE OR REPLACE VIEW m1_m2_kpi_dispatcher_month AS
WITH w AS (
    SELECT a.month, a.hvfhs_license_num, l.brand,
           a.n_records, a.n_clean, a.n_clean_under, a.n_naive_under,
           a.n_clean / a.n_records                                        AS coverage,
           a.n_clean_under / nullif(a.n_clean, 0)                         AS clean_rate,
           a.n_clean_under / a.n_records                                  AS low,
           (a.n_clean_under + a.n_records - a.n_clean) / a.n_records     AS high
    FROM agg_segment_month a
    JOIN ref_licensee l ON l.hvfhs_license_num = a.hvfhs_license_num
    WHERE a.level = 'dispatcher' AND a.wav_requested
)
SELECT
    w.month,
    w.hvfhs_license_num,
    w.brand,
    w.n_records                                                         AS wav_requests,
    w.n_clean                                                           AS wav_requests_clean,
    w.n_records - w.n_clean                                             AS wav_requests_quarantined,
    w.coverage                                                          AS m2_evidence_coverage,
    w.coverage >= {{gate}}                                              AS measurable,
    w.n_naive_under / w.n_records                                       AS naive_rate_unvalidated,
    w.clean_rate                                                        AS clean_record_rate,
    CASE WHEN w.coverage >= {{gate}} THEN w.clean_rate END              AS m1_service_rate_10min,
    w.low                                                               AS m1_bound_low,
    w.high                                                              AS m1_bound_high,
    CASE
        WHEN w.low >= {{target}}                                 THEN 'MEETS'
        WHEN w.high < {{target}}                                 THEN 'BELOW'
        WHEN w.coverage >= {{gate}} AND w.clean_rate >= {{target}} THEN 'MEETS (estimate)'
        WHEN w.coverage >= {{gate}}                              THEN 'BELOW (estimate)'
        ELSE 'NOT MEASURABLE'
    END                                                                 AS kpi_status,
    CASE
        WHEN w.low >= {{target}}  THEN 'proven: holds even if every quarantined record failed'
        WHEN w.high < {{target}}  THEN 'proven: holds even if every quarantined record passed'
        WHEN w.coverage >= {{gate}}
            THEN 'estimate: bounds straddle target; quarantined records treated as out of scope'
        ELSE 'withheld: bounds straddle target and coverage below gate'
    END                                                                 AS verdict_basis,
    ms.metric_status
FROM w
JOIN month_status ms ON ms.month = w.month;

-- M1 + M2 for the calendar year so far (enforcement basis). Counts are additive, so this is exact for the
-- months processed; months_included states the coverage explicitly. Same verdict logic as above.
CREATE OR REPLACE VIEW m1_m2_kpi_ytd AS
WITH y AS (
    SELECT
        substr(k.month, 1, 4)                                           AS year,
        k.hvfhs_license_num, k.brand,
        string_agg(k.month, ' ' ORDER BY k.month)                       AS months_included,
        count(*)                                                        AS n_months,
        sum(k.wav_requests)                                             AS n_records,
        sum(k.wav_requests_clean)                                       AS n_clean,
        sum(a.n_clean_under)                                            AS n_clean_under,
        bool_and(k.metric_status = 'FINAL')                             AS all_months_final
    FROM m1_m2_kpi_dispatcher_month k
    JOIN agg_segment_month a
      ON a.month = k.month AND a.hvfhs_license_num = k.hvfhs_license_num
     AND a.level = 'dispatcher' AND a.wav_requested AND a.pu_borough = 'ALL' AND a.hour_band = 'ALL'
    GROUP BY ALL
)
SELECT
    year, hvfhs_license_num, brand, months_included, n_months,
    n_records                                                           AS wav_requests,
    n_clean / n_records                                                 AS m2_evidence_coverage,
    CASE WHEN n_clean / n_records >= {{gate}} THEN n_clean_under / n_clean END AS m1_service_rate_10min,
    n_clean_under / n_records                                           AS m1_bound_low,
    (n_clean_under + n_records - n_clean) / n_records                   AS m1_bound_high,
    CASE
        WHEN n_clean_under / n_records >= {{target}}                         THEN 'MEETS'
        WHEN (n_clean_under + n_records - n_clean) / n_records < {{target}}  THEN 'BELOW'
        WHEN n_clean / n_records >= {{gate}} AND n_clean_under / n_clean >= {{target}} THEN 'MEETS (estimate)'
        WHEN n_clean / n_records >= {{gate}}                                 THEN 'BELOW (estimate)'
        ELSE 'NOT MEASURABLE'
    END                                                                 AS kpi_status,
    all_months_final
FROM y;

-- M3: WAV vs non-WAV response time (request -> on-scene), same dispatcher and month.
-- P90 is the continuous twin of the standard: "90% under 10 min" <=> P90 < 10 min.
CREATE OR REPLACE VIEW m3_response_gap AS
SELECT
    k.month, k.hvfhs_license_num, k.brand, k.kpi_status,
    CASE WHEN k.measurable THEN w.response_p50_s / 60 END   AS wav_response_p50_min,
    n.response_p50_s / 60                                                       AS nonwav_response_p50_min,
    CASE WHEN k.measurable THEN w.response_p90_s / 60 END   AS wav_response_p90_min,
    n.response_p90_s / 60                                                       AS nonwav_response_p90_min,
    CASE WHEN k.measurable
         THEN (w.response_p90_s - n.response_p90_s) / 60 END                    AS m3_p90_gap_min,
    n.n_clean / n.n_records                                                     AS nonwav_evidence_coverage,
    n.n_clean_under / n.n_clean                                                 AS nonwav_service_rate_10min
FROM m1_m2_kpi_dispatcher_month k
JOIN agg_segment_month w
  ON w.month = k.month AND w.hvfhs_license_num = k.hvfhs_license_num
 AND w.level = 'dispatcher' AND w.wav_requested AND w.pu_borough = 'ALL' AND w.hour_band = 'ALL'
JOIN agg_segment_month n
  ON n.month = k.month AND n.hvfhs_license_num = k.hvfhs_license_num
 AND n.level = 'dispatcher' AND NOT n.wav_requested AND n.pu_borough = 'ALL' AND n.hour_band = 'ALL';

-- M4: where the extra wait of WAV riders accumulates.
-- For clean rows wait = response + curbside exactly, so the mean gap decomposes additively:
--   extra_wait = extra_response (regulated, before arrival) + extra_curbside (after arrival).
CREATE OR REPLACE VIEW m4_stage_decomposition AS
SELECT
    k.month, k.hvfhs_license_num, k.brand, k.kpi_status,
    CASE WHEN k.measurable THEN w.wait_mean_s / 60 END                           AS wav_wait_mean_min,
    n.wait_mean_s / 60                                                                               AS nonwav_wait_mean_min,
    CASE WHEN k.measurable THEN (w.wait_mean_s - n.wait_mean_s) / 60 END         AS extra_wait_min,
    CASE WHEN k.measurable THEN (w.response_mean_s - n.response_mean_s) / 60 END AS extra_response_min,
    CASE WHEN k.measurable THEN (w.curbside_mean_s - n.curbside_mean_s) / 60 END AS extra_curbside_min,
    CASE WHEN k.measurable
         THEN (w.curbside_mean_s - n.curbside_mean_s) / nullif(w.wait_mean_s - n.wait_mean_s, 0) END AS m4_curbside_share_of_extra_wait,
    CASE WHEN k.measurable THEN w.curbside_p50_s / 60 END                        AS wav_curbside_p50_min,
    n.curbside_p50_s / 60                                                                            AS nonwav_curbside_p50_min,
    CASE WHEN k.measurable THEN w.wait_p50_s / 60 END                            AS wav_wait_p50_min,
    n.wait_p50_s / 60                                                                                AS nonwav_wait_p50_min
FROM m1_m2_kpi_dispatcher_month k
JOIN agg_segment_month w
  ON w.month = k.month AND w.hvfhs_license_num = k.hvfhs_license_num
 AND w.level = 'dispatcher' AND w.wav_requested AND w.pu_borough = 'ALL' AND w.hour_band = 'ALL'
JOIN agg_segment_month n
  ON n.month = k.month AND n.hvfhs_license_num = k.hvfhs_license_num
 AND n.level = 'dispatcher' AND NOT n.wav_requested AND n.pu_borough = 'ALL' AND n.hour_band = 'ALL';

-- M5 detail: borough x request-time-band cells for measurable dispatchers.
-- A cell is judged only with >= min_cell clean requests. "significantly_below" uses the 95% Wilson score
-- interval: the cell is flagged only if even its upper bound is below the target, so sampling noise in a
-- few hundred requests cannot put a cell on the intervention list.
CREATE OR REPLACE VIEW m5_service_gap_cells AS
WITH c AS (
    SELECT c.*, c.n_clean_under / c.n_clean AS p, 1.959964 AS z
    FROM agg_segment_month c
    WHERE c.level = 'cell' AND c.wav_requested AND c.n_clean > 0
      AND c.pu_borough IN ('Manhattan', 'Brooklyn', 'Queens', 'Bronx', 'Staten Island')
)
SELECT
    c.month, c.hvfhs_license_num, k.brand, c.pu_borough, c.hour_band,
    c.n_clean                                                          AS wav_requests_clean,
    c.p                                                                AS wav_service_rate_10min,
    (c.p + c.z*c.z/(2*c.n_clean) - c.z*sqrt(c.p*(1-c.p)/c.n_clean + c.z*c.z/(4*c.n_clean*c.n_clean)))
        / (1 + c.z*c.z/c.n_clean)                                      AS wilson_low,
    (c.p + c.z*c.z/(2*c.n_clean) + c.z*sqrt(c.p*(1-c.p)/c.n_clean + c.z*c.z/(4*c.n_clean*c.n_clean)))
        / (1 + c.z*c.z/c.n_clean)                                      AS wilson_high,
    c.n_clean - c.n_clean_under                                        AS wav_late_arrivals,
    c.response_p90_s / 60                                              AS wav_response_p90_min,
    n.n_clean_under / n.n_clean                                        AS nonwav_service_rate_10min,
    c.n_clean >= {{min_cell}}                                          AS judged,
    c.n_clean >= {{min_cell}} AND c.p < {{target}}                     AS below_target,
    c.n_clean >= {{min_cell}} AND
      (c.p + c.z*c.z/(2*c.n_clean) + c.z*sqrt(c.p*(1-c.p)/c.n_clean + c.z*c.z/(4*c.n_clean*c.n_clean)))
        / (1 + c.z*c.z/c.n_clean) < {{target}}                        AS significantly_below
FROM c
JOIN m1_m2_kpi_dispatcher_month k
  ON k.month = c.month AND k.hvfhs_license_num = c.hvfhs_license_num
LEFT JOIN agg_segment_month n
  ON n.month = c.month AND n.level = 'cell' AND n.hvfhs_license_num = c.hvfhs_license_num
 AND NOT n.wav_requested AND n.pu_borough = c.pu_borough AND n.hour_band = c.hour_band
WHERE k.measurable;

-- M5: share of WAV demand in cells SIGNIFICANTLY below the standard (what the citywide average hides).
-- The point-estimate version is kept alongside for transparency.
CREATE OR REPLACE VIEW m5_service_gap_summary AS
SELECT
    month, hvfhs_license_num, brand,
    count(*) FILTER (WHERE judged)                                     AS cells_judged,
    count(*) FILTER (WHERE significantly_below)                        AS cells_significantly_below,
    count(*) FILTER (WHERE below_target)                               AS cells_below_point_estimate,
    sum(wav_requests_clean) FILTER (WHERE judged)                      AS requests_in_judged_cells,
    coalesce(sum(wav_requests_clean) FILTER (WHERE significantly_below), 0)
        / sum(wav_requests_clean) FILTER (WHERE judged)                AS m5_share_of_demand_in_below_target_cells,
    coalesce(sum(wav_late_arrivals) FILTER (WHERE significantly_below), 0)
        / nullif(sum(wav_late_arrivals) FILTER (WHERE judged), 0)      AS share_of_late_arrivals_in_those_cells,
    coalesce(sum(wav_requests_clean) FILTER (WHERE below_target), 0)
        / sum(wav_requests_clean) FILTER (WHERE judged)                AS share_of_demand_below_point_estimate,
    coalesce(sum(wav_requests_clean) FILTER (WHERE NOT judged), 0)     AS requests_in_low_volume_cells
FROM m5_service_gap_cells
GROUP BY ALL;

-- Data-quality diagnostic: does each segment's TMP02 failure set carry the pre-booked-ride signature?
-- By chance ~1/60 of request timestamps fall on a whole minute.
CREATE OR REPLACE VIEW dq_negative_response_signature AS
SELECT
    d.month, d.hvfhs_license_num, l.brand, d.wav_requested,
    d.n_negative,
    d.n_negative / nullif(d.n_negative + d.n_valid, 0)                  AS negative_share,
    d.n_negative_whole_minute / nullif(d.n_negative, 0)                  AS negative_whole_minute_share,
    d.n_negative_5min_mark / nullif(d.n_negative, 0)                     AS negative_5min_mark_share,
    d.n_valid_whole_minute / nullif(d.n_valid, 0)                        AS valid_whole_minute_share,
    CASE
        WHEN d.n_negative = 0 THEN 'none'
        WHEN d.n_valid_whole_minute / nullif(d.n_valid, 0) >= 0.10
            THEN 'inconclusive: whole-minute requests are common in valid rows too'
        WHEN d.n_negative_whole_minute / d.n_negative >= 0.90
            THEN 'pre-booked-ride signature (request = booked time; driver arrived early)'
        WHEN d.n_negative_whole_minute / d.n_negative <= 2 * d.n_valid_whole_minute / nullif(d.n_valid, 0)
            THEN 'no pre-booking signature: cause unknown'
        ELSE 'mixed'
    END                                                                  AS signature
FROM diag_negative_response d
JOIN ref_licensee l ON l.hvfhs_license_num = d.hvfhs_license_num
JOIN month_status ms ON ms.month = d.month;

-- Data-quality diagnostic: per licensee x WAV segment, the facts behind the findings in
-- docs/data_quality_findings.md (record path, airport pattern, raw vs clean response distribution).
CREATE OR REPLACE VIEW dq_segment_profile AS
SELECT
    d.month, d.hvfhs_license_num, l.brand, d.wav_requested,
    d.n_records,
    d.n_dispatching_bases,
    d.n_originating_base_present / d.n_records                           AS originating_base_present_share,
    d.n_onscene_eq_pickup / d.n_records                                  AS onscene_eq_pickup_share,
    d.n_onscene_eq_pickup_airport / nullif(d.n_airport_pickups, 0)       AS onscene_eq_pickup_share_airports,
    (d.n_onscene_eq_pickup - d.n_onscene_eq_pickup_airport)
        / nullif(d.n_records - d.n_airport_pickups, 0)                   AS onscene_eq_pickup_share_elsewhere,
    d.raw_response_p01_s / 60                                            AS raw_response_p01_min,
    d.raw_response_p50_s / 60                                            AS raw_response_p50_min,
    d.raw_response_p99_s / 60                                            AS raw_response_p99_min,
    a.response_p50_s / 60                                                AS clean_response_p50_min,
    a.response_p90_s / 60                                                AS clean_response_p90_min
FROM diag_segment d
JOIN ref_licensee l ON l.hvfhs_license_num = d.hvfhs_license_num
JOIN month_status ms ON ms.month = d.month
LEFT JOIN agg_segment_month a
  ON a.month = d.month AND a.level = 'dispatcher' AND a.hvfhs_license_num = d.hvfhs_license_num
 AND a.wav_requested = d.wav_requested AND a.pu_borough = 'ALL' AND a.hour_band = 'ALL';
