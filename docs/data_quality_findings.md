# Data quality findings

Format: **Issue → Evidence → Business impact → Decision → Handling → Limitation.** Evidence comes from pipeline
outputs (`outputs/validation/<month>/`, `outputs/metrics/dq_negative_response_signature.csv`,
`outputs/profile/`) for April, May and July 2026. June 2026 was refused, so its rule counts are not
published. Nothing is silently fixed: every excluded record stays in `fct_wav_trip` with its rule IDs.

---

## 1. Lyft WAV records: vehicle "on scene" before the request (rule TMP02) — the critical finding

- **Evidence:**
  - 13,761 of 22,920 (60.0%) Lyft WAV records in April, 16,380 of 26,682 (61.4%) in May, and 13,447 of
    23,176 (58.0%) in July.
  - All come from one dispatching base (B03406) and occur on every day of the month. The gaps run from 1 s to
    47 min "early".
  - Only **2.1–2.5%** of these records have a request time on a whole minute, which is the chance rate
    (valid rows: 1.6–2.0%). So they do **not** carry the pre-booked-ride signature (see issue 2).
  - The raw median response of Lyft WAV records is **−6.4 min** (Apr, May) and −5.8 min (Jul): the *typical*
    record has the vehicle arriving before the request (`dq_segment_profile.csv`).
  - Lyft's WAV records are the **only Lyft records with `originating_base_num` populated** (100% of WAV vs
    0% of 5.6–6.7 M non-WAV rows per month; `dq_segment_profile.csv`). They come from a separate
    record-generation path, and that path has the defect.
  - Even the "valid" Lyft WAV records look unlike every peer: P50 6.1 min and P90 12.5 min, against Uber
    WAV's 4.55 and 8.5 (May).
- **Business impact:** the KPI flips depending on how the defect is handled.
  - Naive (count every record with response < 10 min, negatives included): **92.7% → "compliant"**.
  - Drop the bad rows: **81.0% → "non-compliant"**, which would justify a 30-day compliance notice.
  - Honest bounds: **31.3%–92.7%**.
- **Decision:** **withhold** Lyft's KPI (`NOT MEASURABLE`) and recommend a **data-correction request**. See
  [`judgement_call.md`](judgement_call.md).
- **Handling:** rows quarantined (TMP02). The verdict logic sees bounds that straddle 90% (31–93%) with only
  39–42% coverage, so it returns NOT MEASURABLE and withholds M1, M3, M4 and M5 for Lyft. The naive and
  clean-record rates are shown, labelled "not decision-grade", so the reader can see why.
- **Limitation:** the cause is **unknown**. Plausible but unconfirmed: the request time is stamped later in
  the WAV hand-off path (e.g. at re-dispatch).

## 2. Uber: vehicle on scene before the request (TMP02) with a pre-booked-ride signature

- **Evidence:**
  - 4.4% / 5.1% / 5.4% of Uber WAV records (Apr / May / Jul) and ~1.7–1.9% of non-WAV records.
  - **99.95–100%** of the WAV ones (96–99% of the non-WAV ones) have a request time on a whole minute, and
    90–98% on a 5-minute mark. Valid rows hit a whole minute ~1.8% of the time (chance ≈ 1/60).
- **Inference:** these are pre-booked rides. `request_datetime` holds the booked pickup time, and the driver
  arrived early. The field's meaning differs for these trips; this is not random corruption.
- **Business impact:** coverage is 94.6–95.6%. Whether pre-booked rides belong in the on-demand standard is
  **unknown**.
- **Decision:** quarantine the rows (they cannot be timed as on-demand requests); report M1 on the rest *with
  bounds*. In April the verdict is **proven** (it holds even if every quarantined record failed). In May and
  July the bounds straddle 90%, so the verdict is labelled **(estimate)**: it rests on treating these
  pre-booked-signature records as out of scope. Uber confirming that they are pre-booked rides would make
  them proven.
- **Limitation:** pre-booked rides whose driver arrived *late* stay in the KPI, timed from the booked time
  (arguably the right measure). The whole-minute excess among valid rows is small, about 0.1–0.3 points
  (valid WAV rows: 1.8–2.0% vs 1/60 = 1.67%).

## 3. `on_scene_datetime` is documented as "Accessible Vehicles-only" but is always populated

- **Evidence:** 0.00% null for all trips in April, May, June and July 2026 (profiles; 20.8–22.1 M rows per
  month). The 2025 rule package required HVFHS to report on-scene time "so that TLC can better compare WAV and
  non-WAV wait times".
- **Decision:** trust the observed data plus the rule text over the out-of-date dictionary. The pipeline
  depends on the column (schema contract), so a regression to null values would fail SCH01 loudly.
- **Impact:** enables M3 and M4 (WAV vs non-WAV).

## 4. The June 2026 file is 2.14% short of TLC's own monthly report

- **Evidence** (`outputs/validation/2026-06/`: `REFUSED.json`, `reconciliation.csv`,
  `daily_volume_by_licensee.csv`):
  - The file has 20,775,868 trips; S4 implies 707,670 × 30 = 21,230,100. The difference is −454,232.
  - S3 has not published June yet.
  - FIL07 flags Lyft on 8, 9 and 10 June (117,202 / 113,548 / 113,530 trips against a Lyft median of 187,690).
    Lyft stays low through 14 June (136,629 / 134,260 / 172,623 / 146,701) while Uber is unaffected.
  - Lyft's 8–14 June deficit against the median of the same weekday in the other June weeks is ≈ 489K, the
    same order as the 454K gap.
- **Decision:** **refuse** to publish June (ReconciliationError). Previously published data is untouched.
- **Inference:** Lyft trips for about 8–14 June are missing from the published file. Unconfirmed.
- **Handling:** rerun when S3 publishes June or TLC re-issues the file; `--refresh` preserves the old version.

## 5. Other file-level observations

| Issue | Evidence | Decision |
|---|---|---|
| Lyft 6 April 2026 low day | 127,028 trips vs a Lyft median ~188K (FIL07 WARN), yet April reconciles exactly with S3 | WARN only. Shows why the controls prove *receipt*, not *submission*, completeness (S3 is tabulated from the same submissions). |
| S4 served gzip-compressed | `Content-Length` 24,863 vs 79,774 decoded bytes: the first version of the byte check refused the file | Request identity encoding; compare wire bytes when compressed. |
| Requests before the month start | e.g. request 29 Jun 14:40 for a 1 Jul pickup (July) | Month = pickup month (the file partition); documented assumption. |

## 6. Row-level WARN rules (kept in the metrics)

| Rule | Evidence | Why kept |
|---|---|---|
| DOM03 on-scene = pickup to the second | 6.4% of Uber non-WAV (May), 2.2% of Uber WAV; 20.0–22.8% of Uber non-WAV LGA/JFK pickups vs 5.1–6.0% elsewhere | Plausible for lot/queue pickups; curbside = 0 slightly lowers the non-WAV curbside median (medians are robust) |
| DOM02 response > 60 min | at most 0.0046% of any segment | These are exactly the failures the standard counts; dropping "outliers" would inflate compliance |
| REF03 pickup outside the five boroughs | ≤ 0.02% of any segment (zones 1, 264, 265) | Kept in the dispatcher KPI; only left out of borough cells |
| DOM01 WAV request served by a non-WAV vehicle | 0 in every month | A non-zero count would be escalated |

## 7. Checks that found nothing (and still run every time)

Exact duplicate records (DOM04), pickups outside the month (TMP01), missing timestamps (SCH01), invalid flags
(SCH02), unknown licensee (REF01), unknown zone (REF02): **0 in every published month**. They stay in place
because each guards against a failure that would silently corrupt the KPI if a future file had it. The
synthetic test month proves each one fires.
