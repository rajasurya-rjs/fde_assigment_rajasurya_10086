-- Warehouse schema (DuckDB). Month-partitioned tables are replaced atomically per month,
-- so re-running a month can never duplicate rows. Primary keys enforce the stated grain.

-- Reference: one row per TLC taxi zone (265 zones). Source: taxi_zone_lookup.csv.
CREATE TABLE IF NOT EXISTS ref_zone (
    location_id     INTEGER PRIMARY KEY,
    borough         VARCHAR,
    zone            VARCHAR,
    service_zone    VARCHAR,
    is_nyc_borough  BOOLEAN NOT NULL
);

-- Reference: one row per HVFHS licence number. Source: TLC HVFHS data dictionary (config/licensees.csv).
CREATE TABLE IF NOT EXISTS ref_licensee (
    hvfhs_license_num VARCHAR PRIMARY KEY,
    brand             VARCHAR NOT NULL,
    control_key       VARCHAR          -- base_license_number used for this licensee in Open Data 2v9c-2k7f
);

-- Fact: one row per completed WAV-requested trip (wav_request_flag = 'Y'), including quarantined rows.
-- Lineage key: (month, source_row_id) where source_row_id = row position in the preserved raw Parquet file.
CREATE TABLE IF NOT EXISTS fct_wav_trip (
    month               VARCHAR  NOT NULL,
    source_row_id       BIGINT   NOT NULL,
    hvfhs_license_num   VARCHAR,
    dispatching_base_num VARCHAR,
    request_datetime    TIMESTAMP,
    on_scene_datetime   TIMESTAMP,
    pickup_datetime     TIMESTAMP,
    dropoff_datetime    TIMESTAMP,
    pu_location_id      INTEGER,
    do_location_id      INTEGER,
    pu_borough          VARCHAR,
    hour_band           VARCHAR,
    wav_match_flag      VARCHAR,
    response_s          BIGINT,   -- request -> on-scene  (the regulated interval)
    curbside_s          BIGINT,   -- on-scene -> pickup   (boarding / securement)
    wait_s              BIGINT,   -- request -> pickup    (what the rider experiences)
    ride_s              BIGINT,   -- pickup -> dropoff
    failed_rules        VARCHAR,  -- ERROR rules failed ('' = clean)
    warned_rules        VARCHAR,  -- WARN rules triggered
    is_clean            BOOLEAN  NOT NULL,
    PRIMARY KEY (month, source_row_id)
);

-- Aggregate: trips by month x licensee x WAV-request x (borough x request-time band).
-- level = 'dispatcher' rows use pu_borough = hour_band = 'ALL'.
CREATE TABLE IF NOT EXISTS agg_segment_month (
    month             VARCHAR NOT NULL,
    level             VARCHAR NOT NULL,     -- dispatcher | cell
    hvfhs_license_num VARCHAR NOT NULL,
    wav_requested     BOOLEAN NOT NULL,
    pu_borough        VARCHAR NOT NULL,
    hour_band         VARCHAR NOT NULL,
    n_records         BIGINT  NOT NULL,     -- all records in segment
    n_clean           BIGINT  NOT NULL,     -- records passing every ERROR rule
    n_clean_under     BIGINT  NOT NULL,     -- clean AND response_s < threshold
    n_naive_under     BIGINT  NOT NULL,     -- response_s < threshold with NO validation (for contrast)
    response_p50_s    DOUBLE,
    response_p90_s    DOUBLE,
    curbside_p50_s    DOUBLE,
    wait_p50_s        DOUBLE,
    response_mean_s   DOUBLE,
    curbside_mean_s   DOUBLE,
    wait_mean_s       DOUBLE,
    PRIMARY KEY (month, level, hvfhs_license_num, wav_requested, pu_borough, hour_band)
);

-- Validation evidence: failures per rule per licensee x WAV segment.
CREATE TABLE IF NOT EXISTS validation_rule_result (
    month             VARCHAR NOT NULL,
    rule_id           VARCHAR NOT NULL,
    hvfhs_license_num VARCHAR NOT NULL,
    wav_requested     VARCHAR NOT NULL,     -- 'Y' | 'N' | other raw value
    n_checked         BIGINT  NOT NULL,
    n_failed          BIGINT  NOT NULL,
    PRIMARY KEY (month, rule_id, hvfhs_license_num, wav_requested)
);

-- Diagnostic for rule TMP02 (vehicle "arrives" before the request). Pre-booked rides store the booked
-- pickup time in request_datetime, which lands on a whole minute; if nearly all TMP02 failures do and
-- valid rows do not, the failures carry a pre-booking signature rather than random corruption.
CREATE TABLE IF NOT EXISTS diag_negative_response (
    month                   VARCHAR NOT NULL,
    hvfhs_license_num       VARCHAR NOT NULL,
    wav_requested           BOOLEAN NOT NULL,
    n_negative              BIGINT  NOT NULL,
    n_negative_whole_minute BIGINT  NOT NULL,
    n_negative_5min_mark    BIGINT  NOT NULL,
    n_valid                 BIGINT  NOT NULL,
    n_valid_whole_minute    BIGINT  NOT NULL,
    PRIMARY KEY (month, hvfhs_license_num, wav_requested)
);

-- Diagnostic profile per licensee x WAV segment (record path, airport pattern, RAW response distribution).
CREATE TABLE IF NOT EXISTS diag_segment (
    month                        VARCHAR NOT NULL,
    hvfhs_license_num            VARCHAR NOT NULL,
    wav_requested                BOOLEAN NOT NULL,
    n_records                    BIGINT  NOT NULL,
    n_dispatching_bases          BIGINT  NOT NULL,
    n_originating_base_present   BIGINT  NOT NULL,
    n_airport_pickups            BIGINT  NOT NULL,
    n_onscene_eq_pickup          BIGINT  NOT NULL,
    n_onscene_eq_pickup_airport  BIGINT  NOT NULL,
    raw_response_p01_s           DOUBLE,
    raw_response_p50_s           DOUBLE,
    raw_response_p99_s           DOUBLE,
    PRIMARY KEY (month, hvfhs_license_num, wav_requested)
);

-- File-level checks (contract, completeness) and reconciliation against control totals.
CREATE TABLE IF NOT EXISTS file_check (
    month     VARCHAR NOT NULL,
    check_id  VARCHAR NOT NULL,
    name      VARCHAR NOT NULL,
    severity  VARCHAR NOT NULL,
    status    VARCHAR NOT NULL,
    observed  VARCHAR,
    expected  VARCHAR,
    detail    VARCHAR,
    PRIMARY KEY (month, check_id)
);

CREATE TABLE IF NOT EXISTS reconciliation (
    month          VARCHAR NOT NULL,
    control_source VARCHAR NOT NULL,
    scope          VARCHAR NOT NULL,
    file_trips     BIGINT,
    control_trips  BIGINT,
    diff           BIGINT,
    diff_pct       DOUBLE,
    status         VARCHAR NOT NULL,
    PRIMARY KEY (month, control_source, scope)
);

-- One row per successfully published month.
CREATE TABLE IF NOT EXISTS month_status (
    month            VARCHAR PRIMARY KEY,
    retrieval_status VARCHAR NOT NULL,   -- RECONCILED | RECONCILED_FALLBACK | UNRECONCILED
    metric_status    VARCHAR NOT NULL,   -- FINAL | PROVISIONAL
    trips_sha256     VARCHAR NOT NULL,
    trips_rows       BIGINT  NOT NULL,
    run_id           VARCHAR NOT NULL,
    loaded_at        TIMESTAMP NOT NULL
);

-- Every attempt, including failures (append-only audit trail).
CREATE TABLE IF NOT EXISTS pipeline_run (
    run_id      VARCHAR NOT NULL,
    month       VARCHAR NOT NULL,
    started_at  TIMESTAMP NOT NULL,
    finished_at TIMESTAMP NOT NULL,
    status      VARCHAR NOT NULL,      -- PUBLISHED | FAILED
    error_type  VARCHAR,
    detail      VARCHAR
);
