-- Staging for one month of HVFHV trip records.
-- Grain of every view here: one row per record in the preserved raw Parquet file (= one completed trip).
-- The source has NO trip identifier. Lineage key = (month, source_row_id) where source_row_id is the
-- row's position in the raw file (DuckDB file_row_number), so any quarantined row can be traced back.

-- 1. Exact-duplicate detection (rule DOM04).
--    Without a trip ID, "the same trip twice" can only mean a row identical in every column.
--    Pass 1 hashes every column; pass 2 confirms candidates by full-column equality, so a hash
--    collision can never quarantine a legitimate trip. The first occurrence is kept.
CREATE OR REPLACE TEMP TABLE dup_copy AS
WITH hashed AS (
    SELECT file_row_number AS source_row_id, hash({{all_columns}}) AS h
    FROM read_parquet({{trips_path}}, file_row_number = true)
),
candidate_rows AS (
    SELECT source_row_id FROM hashed
    WHERE h IN (SELECT h FROM hashed GROUP BY h HAVING count(*) > 1)
),
ranked AS (
    SELECT t.file_row_number AS source_row_id,
           row_number() OVER (PARTITION BY {{all_columns}} ORDER BY t.file_row_number) AS copy_no
    FROM read_parquet({{trips_path}}, file_row_number = true) t
    WHERE t.file_row_number IN (SELECT source_row_id FROM candidate_rows)
)
SELECT source_row_id FROM ranked WHERE copy_no > 1;

-- 2. Typed staging view with the workflow stage durations (seconds).
--    date_diff counts second boundaries, so response + curbside = wait holds exactly.
CREATE OR REPLACE TEMP VIEW stg_trips AS
SELECT
    {{month}}                                                        AS month,
    t.file_row_number                                                AS source_row_id,
    t.hvfhs_license_num,
    t.dispatching_base_num,
    t.originating_base_num,
    t.request_datetime,
    t.on_scene_datetime,
    t.pickup_datetime,
    t.dropoff_datetime,
    t.PULocationID                                                   AS pu_location_id,
    t.DOLocationID                                                   AS do_location_id,
    t.wav_request_flag,
    t.wav_match_flag,
    date_diff('second', t.request_datetime,  t.on_scene_datetime)    AS response_s,   -- regulated interval
    date_diff('second', t.on_scene_datetime, t.pickup_datetime)      AS curbside_s,
    date_diff('second', t.request_datetime,  t.pickup_datetime)      AS wait_s,
    date_diff('second', t.pickup_datetime,   t.dropoff_datetime)     AS ride_s
FROM read_parquet({{trips_path}}, file_row_number = true) t;

-- 3. Joins to reference data.
--    Every join is a LEFT JOIN onto the reference table's PRIMARY KEY (many trips -> one zone /
--    one licensee), so a join can neither drop a trip nor multiply it. An unmatched key surfaces
--    as NULL and fails rule REF01 / REF02 visibly instead of vanishing (as an INNER JOIN would).
CREATE OR REPLACE TEMP VIEW stg_joined AS
SELECT
    s.*,
    z.location_id IS NOT NULL           AS pu_zone_found,
    z.borough                            AS pu_borough,
    z.service_zone                       AS pu_service_zone,
    l.hvfhs_license_num IS NOT NULL     AS licensee_found,
    d.source_row_id IS NOT NULL         AS is_duplicate_copy,
    {{hour_band_case}}                   AS hour_band
FROM stg_trips s
LEFT JOIN ref_zone     z ON z.location_id       = s.pu_location_id
LEFT JOIN ref_licensee l ON l.hvfhs_license_num = s.hvfhs_license_num
LEFT JOIN dup_copy     d ON d.source_row_id     = s.source_row_id;

-- 4. Rule evaluation: one pass_<RULE> boolean per rule in src/wavpipe/rules.py.
CREATE OR REPLACE TEMP VIEW stg_checked AS
SELECT j.*,
  {{rule_pass_columns}}
FROM stg_joined j;

CREATE OR REPLACE TEMP VIEW stg_final AS
SELECT c.*,
    ({{is_clean}})        AS is_clean,
    {{failed_rules}}      AS failed_rules,
    {{warned_rules}}      AS warned_rules
FROM stg_checked c;

-- 5. Rule failure counts per licensee x WAV-request segment (one scan of the month).
CREATE OR REPLACE TEMP TABLE tmp_rule_counts AS
UNPIVOT (
    SELECT
        coalesce(hvfhs_license_num, '(null)') AS hvfhs_license_num,
        coalesce(wav_request_flag, '(null)')  AS wav_requested,
        count(*)                              AS n_checked,
        count(*) FILTER (WHERE NOT is_clean)  AS f_ANY_ERROR,
        {{fail_counts}}
    FROM stg_final
    GROUP BY ALL
)
ON COLUMNS('^f_') INTO NAME rule_id VALUE n_failed;
