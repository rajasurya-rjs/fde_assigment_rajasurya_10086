-- Load one month into the warehouse. Executed inside a single transaction by pipeline.py:
-- DELETE + INSERT per month partition, so a rerun replaces the month instead of appending to it.

-- Fact table: every WAV-requested trip, clean or quarantined, with the reasons attached.
DELETE FROM fct_wav_trip WHERE month = {{month}};
INSERT INTO fct_wav_trip
SELECT month, source_row_id, hvfhs_license_num, dispatching_base_num,
       request_datetime, on_scene_datetime, pickup_datetime, dropoff_datetime,
       pu_location_id, do_location_id, pu_borough, hour_band, wav_match_flag,
       response_s, curbside_s, wait_s, ride_s,
       failed_rules, warned_rules, is_clean
FROM stg_final
WHERE wav_request_flag = 'Y';

-- Segment aggregates at two grains in one pass (GROUPING SETS):
--   dispatcher : month x licensee x WAV-request                      (KPI grain of the rule)
--   cell       : month x licensee x WAV-request x borough x time band (where the gap is)
-- Only attributable, classifiable rows are segmented (licensee known, WAV flag Y/N);
-- the rest are counted in validation_rule_result under REF01 / SCH02.
DELETE FROM agg_segment_month WHERE month = {{month}};
INSERT INTO agg_segment_month
SELECT
    {{month}}                                                         AS month,
    CASE WHEN grouping(pu_borough_cell) = 0 THEN 'cell' ELSE 'dispatcher' END AS level,
    hvfhs_license_num,
    wav_requested,
    coalesce(pu_borough_cell, 'ALL')                                  AS pu_borough,
    coalesce(hour_band_cell, 'ALL')                                   AS hour_band,
    count(*)                                                          AS n_records,
    count(*) FILTER (WHERE is_clean)                                  AS n_clean,
    count(*) FILTER (WHERE is_clean AND response_s < {{threshold_s}}) AS n_clean_under,
    count(*) FILTER (WHERE response_s < {{threshold_s}})              AS n_naive_under,
    quantile_cont(response_s, 0.5) FILTER (WHERE is_clean)            AS response_p50_s,
    quantile_cont(response_s, 0.9) FILTER (WHERE is_clean)            AS response_p90_s,
    quantile_cont(curbside_s, 0.5) FILTER (WHERE is_clean)            AS curbside_p50_s,
    quantile_cont(wait_s, 0.5)     FILTER (WHERE is_clean)            AS wait_p50_s,
    avg(response_s)                FILTER (WHERE is_clean)            AS response_mean_s,
    avg(curbside_s)                FILTER (WHERE is_clean)            AS curbside_mean_s,
    avg(wait_s)                    FILTER (WHERE is_clean)            AS wait_mean_s
FROM (
    SELECT *,
        wav_request_flag = 'Y'                                              AS wav_requested,
        CASE WHEN pass_REF03 THEN pu_borough ELSE 'Non-borough zone' END    AS pu_borough_cell,
        coalesce(hour_band, 'UNKNOWN')                                      AS hour_band_cell
    FROM stg_final
    WHERE licensee_found AND wav_request_flag IN ('Y', 'N')
)
GROUP BY GROUPING SETS (
    (hvfhs_license_num, wav_requested),
    (hvfhs_license_num, wav_requested, pu_borough_cell, hour_band_cell)
);

DELETE FROM diag_negative_response WHERE month = {{month}};
INSERT INTO diag_negative_response
SELECT
    {{month}}, hvfhs_license_num, wav_request_flag = 'Y',
    count(*) FILTER (WHERE NOT pass_TMP02),
    count(*) FILTER (WHERE NOT pass_TMP02 AND second(request_datetime) = 0),
    count(*) FILTER (WHERE NOT pass_TMP02 AND second(request_datetime) = 0 AND minute(request_datetime) % 5 = 0),
    count(*) FILTER (WHERE pass_TMP02 AND response_s IS NOT NULL),
    count(*) FILTER (WHERE pass_TMP02 AND response_s IS NOT NULL AND second(request_datetime) = 0)
FROM stg_final
WHERE licensee_found AND wav_request_flag IN ('Y', 'N')
GROUP BY ALL;

DELETE FROM diag_segment WHERE month = {{month}};
INSERT INTO diag_segment
SELECT
    {{month}}, hvfhs_license_num, wav_request_flag = 'Y',
    count(*),
    count(DISTINCT dispatching_base_num),
    count(originating_base_num),
    count(*) FILTER (WHERE pu_service_zone = 'Airports'),
    count(*) FILTER (WHERE on_scene_datetime = pickup_datetime),
    count(*) FILTER (WHERE on_scene_datetime = pickup_datetime AND pu_service_zone = 'Airports'),
    quantile_cont(response_s, 0.01),     -- RAW distribution, before any rule: shows negative mass directly
    quantile_cont(response_s, 0.50),
    quantile_cont(response_s, 0.99)
FROM stg_final
WHERE licensee_found AND wav_request_flag IN ('Y', 'N')
GROUP BY ALL;

DELETE FROM validation_rule_result WHERE month = {{month}};
INSERT INTO validation_rule_result
SELECT {{month}}, replace(rule_id, 'f_', ''), hvfhs_license_num, wav_requested, n_checked, n_failed
FROM tmp_rule_counts;
