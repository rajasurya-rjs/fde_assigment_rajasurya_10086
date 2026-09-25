# Source map (detail)

Diagram: [`../diagrams/source_map.svg`](../diagrams/source_map.svg). All observations below come from the
pipeline's own profiling and validation outputs for April, May, June and July 2026 unless marked as documented
(i.e. taken from the publisher's documentation).

## Business question → information → source → fields → metric

| Business question | Required information | Source | Fields | Metric |
|---|---|---|---|---|
| Does each dispatcher serve ≥ 90% of WAV requests in < 10 min? | WAV requests; request time; vehicle arrival time; dispatcher | S1 | `wav_request_flag`, `request_datetime`, `on_scene_datetime`, `hvfhs_license_num` | M1, M2 |
| Are WAV riders served as well as other riders? Where does their extra wait accrue? | Same event times for non-WAV trips; boarding time | S1 | + `pickup_datetime`, `wav_match_flag` | M3, M4 |
| Where and when is service below the standard? | Pickup borough; request hour | S1 + S2 | `PULocationID` → `LocationID` → `Borough` | M5 |
| Did we receive the whole month? Can the numbers be FINAL? | Independently published trip counts | S3, S4 | `total_dispatched_trips`; `Trips Per Day` | FINAL / PROVISIONAL / refused |
| How many WAV requests went unserved or were cancelled? | Request-level records | **none public** | — | Limitation: rates measured on completed trips |

## S1 — TLC High Volume FHV trip records

| Attribute | Detail |
|---|---|
| Owner | NYC TLC. Records are **submitted by each HVFHS licensee** (Uber = HV0003, Lyft = HV0005) under 35 RCNY §59D-14; bases have until the end of the following month to submit (documented in S3 metadata). |
| Format | Apache Parquet, 25 columns (2026 files include `cbd_congestion_fee`, added 2025), ~0.5 GB and ~21 M rows per month. |
| Retrieval | HTTPS GET `https://d37ci6vzurychx.cloudfront.net/trip-data/fhvhv_tripdata_YYYY-MM.parquet`, streamed, `Accept-Encoding: identity`. |
| Grain | **One row = one completed trip**, partitioned by pickup month. **No trip identifier**; lineage uses the row position in the preserved file. |
| Key fields | `hvfhs_license_num`, `dispatching_base_num`, `originating_base_num`, `request_datetime`, `on_scene_datetime`, `pickup_datetime`, `dropoff_datetime`, `PULocationID`, `DOLocationID`, `wav_request_flag`, `wav_match_flag` |
| Business meaning | Each row is a rider request that ended in a completed trip; the four timestamps are the workflow events. |
| Refresh | Monthly, about 2 months behind. Files can be **re-published**: June and July 2026 both carry `Last-Modified: 17 Sep 2026`. |
| Expected completeness | Every completed HVFHS trip with a pickup in the month, i.e. equal to what licensees submitted (S3). |
| Known quality problems | Vehicle "on scene" before the request (Lyft WAV 58–61%, Uber WAV 4–5%, non-WAV ~1.5–1.9%). On-scene = pickup to the second (Uber non-WAV 6.4%). The dictionary says `on_scene_datetime` is "Accessible Vehicles-only", but it is 0.00% null for all trips. `originating_base_num` is null for all Lyft non-WAV trips but populated for all Lyft WAV trips (`dq_segment_profile.csv`). **The June 2026 file is 2.14% short** of TLC's own monthly report. |
| Join keys | `hvfhs_license_num` → `ref_licensee`; `PULocationID` → zone `LocationID` |
| Potential gaps | Unserved or cancelled requests; a pre-booked/scheduled flag; vehicle and driver IDs; a cancellation reason. |
| Why necessary | The only source of the timestamps that define the KPI. |

## S2 — TLC taxi zone lookup

| Attribute | Detail |
|---|---|
| Owner / format / retrieval | TLC; CSV (4 columns, 265 rows); HTTPS GET `…/misc/taxi_zone_lookup.csv` |
| Grain | One row = one taxi zone. `LocationID` is unique (checked every run, ZON02) and is the join key. |
| Business meaning | Zone → borough → service zone. Zone 1 = Newark Airport (borough "EWR"); 264 = "Unknown"; 265 = "Outside of NYC". |
| Refresh / completeness | Rarely changes; the pipeline expects 265 unique ids. |
| Known quality problems | Three zones (103–105) share one name; zones 1, 264 and 265 are not NYC boroughs, so those trips are flagged REF03 (≤ 0.02% of any segment) and left out of borough cells. |
| Why necessary | M5 ("where") needs borough; the trip file only has zone ids. |

## S3 — NYC Open Data "FHV Base Aggregate Report" (`2v9c-2k7f`) — API

| Attribute | Detail |
|---|---|
| Owner | TLC, published on NYC Open Data (Socrata). |
| Format / retrieval | JSON via the SODA API. `$select=count(*)` gives the expected count; then `$where=year=Y AND month=M`, `$order=base_license_number,:id`, `$limit=200`, `$offset=…` until retrieved = expected. Raw pages are preserved. |
| Grain | **Base × month.** HVFHS licensees appear as single rows `UBER` and `LYFT` (not per dispatching base). ~300–400 rows per month. |
| Key fields | `base_license_number`, `year`, `month`, `total_dispatched_trips` |
| Refresh | Monthly on a ~2-month lag (documented); TLC may defer if bases submit late. **May 2026 was the latest month available on 25 Sep 2026.** |
| Expected completeness | Every base that submitted trips that month. |
| Known quality problems | Numeric fields arrive as strings; grain uniqueness is checked (CTL01). |
| Join keys | `base_license_number` = `ref_licensee.control_key` (`UBER`/`LYFT`). **Grain mismatch:** trips are aggregated to licensee × month before comparing. |
| Important limit | It is tabulated **from the same licensee submissions** as S1 (documented). It proves the published file is complete, not that the licensee submitted every trip. |
| Why necessary | The only per-licensee control total; the basis of the FINAL status. |

## S4 — TLC monthly industry report (`data_reports_monthly.csv`)

| Attribute | Detail |
|---|---|
| Owner / format / retrieval | TLC; CSV over HTTPS from nyc.gov. It returns 403 to non-browser user agents and gzips the response unless identity encoding is requested. Served chunked (no `Content-Length`), so truncation is detected by the HTTP layer and by requiring the month's row. |
| Grain | **Licence class × month**; `Trips Per Day` is a **rounded daily average**. |
| Refresh | Monthly; more timely than S3 (includes July 2026). |
| Use | Control total = trips per day × days in month, compared with all rows in the file. Rounding to whole trips per day can explain at most ±0.5 × days (±15 trips). The observed differences, +62 to +114 trips (≤ 0.0005%), are larger than that, so TLC tabulates S4 slightly differently from the published file. The ±0.5% tolerance allows for that, and still catches June's −2.14%. |
| Why necessary | A timely second control while S3 lags. It is what caught the June 2026 shortfall. |

## Reference documents (read, cited, not ingested)

| Document | Used for |
|---|---|
| Notice of Promulgation (adopted 29 Jan 2025) amending 35 RCNY §59B-17(f)(3) and §59D-14 | KPI definition (90% in under 10 min), cancellation exclusion, on-scene reporting requirement, calendar-year enforcement |
| TLC FHV Annual Accessibility Report, Year 5 (FY2023) | Wait measured to vehicle arrival; the "add WAV metrics to the dashboard" recommendation |
| HVFHS data dictionary (18 Mar 2025) | Field meanings; the out-of-date "Accessible Vehicles-only" note |
| Open Data "CURRENT BASES" (`eccv-9dzr`), looked up once | Base names: B03404 = UBER USA, LLC; B03406 = TRI-CITY, LLC (Lyft) |
