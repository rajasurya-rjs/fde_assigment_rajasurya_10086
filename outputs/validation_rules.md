# Validation rules

_Generated from `src/wavpipe/rules.py` and `src/wavpipe/validate.py` on every run._

## File-level checks (can the month be trusted at all?)

| ID | Check | Severity | Why it matters | Action on failure |
|---|---|---|---|---|
| ZON01 | Zone lookup matches its schema contract | FATAL | Borough attribution depends on these columns. | Stop the whole run |
| ZON02 | Zone LocationID is unique | FATAL | A duplicated join key would multiply trips in every borough view. | Stop the whole run |
| FIL01 | Trip file matches its schema contract (11 columns, fixed types) | FATAL | A renamed or retyped timestamp would silently corrupt every duration. | Stop the month; extra columns only WARN |
| FIL02 | Rows read by the validation scan == rows declared in the Parquet footer | FATAL | Proves every row group was read; a partial read would undercount. | Stop the month |
| FIL03 | Every calendar day of the month has pickups | FATAL | A missing day means a truncated or wrong file. | Stop the month |
| FIL04 | No day below 50% of the median daily volume | WARN | Flags a partial extract; could also be weather or a holiday. | Report; do not drop |
| FIL05 | Pickups outside the file month <= 1% | FATAL | More means the partition contract is broken (wrong file or double counting). | Stop the month |
| FIL06 | Rows failing any ERROR rule <= 5% | FATAL | Individual rows are quarantined; this stops a file that is broken wholesale. | Stop the month |
| FIL07 | No licensee-day below 70% of that licensee's median day | WARN | Localizes partial gaps a file-level check misses (added after June 2026). | Report; reconciliation is the gate |
| CTL01 | Control data has one row per base per month | WARN | A duplicated control row would inflate the control total. | Report |
| REC01 | Trips per licensee == Open Data API control (within 0.5%) | FATAL | Independent proof the file is complete at licensee grain. | Stop the month (unless --allow-unreconciled) |
| REC02 | Total trips == TLC monthly report control (within 0.5%) | FATAL | Timely fallback control while the API control is unpublished. | Stop the month (unless --allow-unreconciled) |

## Row-level business rules (evaluated on every record)

ERROR = quarantined (excluded from all metrics, kept with reasons in `fct_wav_trip` and `outputs/validation/<month>/`). WARN = kept in metrics, counted and reported.

| ID | Category | Severity | Rule | Why it matters | Action |
|---|---|---|---|---|---|
| SCH01 | schema | ERROR | All four workflow timestamps are present | A missing event breaks the request -> arrival -> pickup -> dropoff chain; the response time is unknowable. | Quarantine; exclude from all metrics |
| SCH02 | schema | ERROR | WAV request / match flags are Y or N | wav_request_flag defines the KPI population; an unknown value cannot be classified as WAV or not. | Quarantine; exclude from all metrics |
| REF01 | referential | ERROR | Licensee resolves to the TLC licensee reference | The standard applies per dispatcher; a trip that cannot be attributed cannot be scored. | Quarantine; exclude from all metrics |
| REF02 | referential | ERROR | Pickup zone resolves to the TLC zone lookup | An unresolvable zone cannot be placed; an inner join would silently drop it from borough views. | Quarantine; exclude from all metrics |
| REF03 | referential | WARN | Pickup zone is inside one of the five NYC boroughs | TLC zones 264/265 are 'Unknown'/'Outside of NYC' and zone 1 is Newark Airport; they cannot be assigned to a borough cell. | Keep in dispatcher KPI; exclude from borough x time-band cells (M5) |
| TMP01 | temporal | ERROR | Pickup falls inside the file's month | TLC partitions files by pickup month; a row outside it would be double-counted when adjacent months are loaded. | Quarantine; file fails if > max_out_of_month_pct |
| TMP02 | temporal | ERROR | Vehicle arrives on or after the request (request <= on-scene) | A negative response time is impossible. A naive '< 10 min' test counts it as compliant and inflates the KPI. | Quarantine; counts against evidence coverage (M2) and the measurability gate |
| TMP03 | temporal | ERROR | Pickup on or after vehicle arrival (on-scene <= pickup) | A passenger cannot board before the vehicle arrives; the arrival timestamp is then untrustworthy. | Quarantine; exclude from all metrics |
| TMP04 | temporal | ERROR | Dropoff after pickup | A zero or negative ride means the record is broken, so its other timestamps are suspect too. | Quarantine; exclude from all metrics |
| DOM01 | domain | WARN | A WAV request was completed in a WAV | A WAV request completed in a non-accessible vehicle is a service failure the response-time KPI would not see. | Keep; report count (a non-zero value is escalated) |
| DOM02 | domain | WARN | Response time at most 60 minutes | Very long waits may be pre-booked rides, but they are exactly the failures the standard counts. Dropping them as 'outliers' would inflate compliance. | Keep in KPI (counted as failures); report for review |
| DOM03 | domain | WARN | On-scene time differs from pickup time | Arrival and boarding logged at the same second. ~4x more frequent at LGA/JFK pickups (lot/queue pickups?) but found everywhere; cause unconfirmed. Curbside time is 0 for these rows. | Keep; curbside metrics (M4) carry this caveat |
| DOM04 | grain | ERROR | Record is not an exact duplicate of an earlier record | The source has no trip ID; an exact copy of a record would double-count a trip. | Keep first occurrence (lowest source row); quarantine the copies |

## Segment-level verdict logic (SEG01)

For each dispatcher-month, bounds are computed with every quarantined WAV record counted first as a failure, then as a pass. If both bounds are on the same side of 90%, the verdict is **proven** (MEETS / BELOW). If they straddle the target, the clean-record rate decides (**estimate**) only when at least 90% of records are clean, a policy line equal to the standard's 10% tolerance. Otherwise the KPI is **NOT MEASURABLE** and a data-correction request is the output. M3–M5 are computed only for dispatchers at or above the coverage line.
