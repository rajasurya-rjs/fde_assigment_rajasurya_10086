-- Reference tables are small and global (not month-partitioned): fully replaced each run.

DELETE FROM ref_zone;
INSERT INTO ref_zone
SELECT
    CAST(LocationID AS INTEGER)                                                  AS location_id,
    Borough                                                                      AS borough,
    Zone                                                                         AS zone,
    service_zone,
    Borough IN ('Manhattan', 'Brooklyn', 'Queens', 'Bronx', 'Staten Island')     AS is_nyc_borough
FROM read_csv({{zones_path}}, header = true, auto_detect = true);

DELETE FROM ref_licensee;
INSERT INTO ref_licensee
SELECT hvfhs_license_num, brand, nullif(control_key, '')
FROM read_csv({{licensees_path}}, header = true, all_varchar = true);
