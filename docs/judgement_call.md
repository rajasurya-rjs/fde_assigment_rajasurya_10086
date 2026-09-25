# FDE judgement call: withhold Lyft's KPI instead of reporting either available number

**Context → Options → Trade-off → Decision → Evidence → Consequence**

## Context

The client (TLC accessibility compliance) needs a monthly answer to *"Is each dispatcher serving ≥ 90% of
wheelchair-accessible requests in under 10 minutes?"* A "no" can lead to a formal notice and 30 days to comply.
The measure is `on_scene_datetime − request_datetime`, taken from records the dispatcher itself submits.

In May 2026, **16,380 of Lyft's 26,682 WAV records (61.4%) have the vehicle arriving before the request.** The
*median* raw Lyft WAV response is −6.4 minutes. The same pattern appears in April (60.0%) and July (58.0%).

## Options

| Option | Lyft, May 2026 | What it implies |
|---|---|---|
| A. Report the naive rate (no validation) | **92.7%, meets** | Impossible negative times are counted as "under 10 minutes". |
| B. Drop impossible rows, report the rest | **81.0%, below** | Assumes the surviving 38.6% represent all WAV trips. |
| C. Impute the missing response times | any number you like | Invents the very quantity being regulated. |
| D. **Withhold the KPI, report bounds, and request corrected data** | **NOT MEASURABLE (31.3%–92.7%)** | Says what the data can and cannot support. |

## Trade-off

- A and B are both "a number", and a dashboard wants a number. But **they disagree about pass/fail**, so
  whichever is shown decides a regulatory outcome by accident of cleaning choice.
- **B is not wrong in itself: it is exactly how M1 is computed for Uber.** What makes it unsafe for Lyft is
  its conditions:
  - 61% of records are excluded, not 5%.
  - The excluded records have no benign explanation (Uber's carry a pre-booked-ride signature).
  - If Lyft's request time is stamped *late* by a variable delay, a record survives filtering only when the
    true wait exceeded that delay. The survivors are then biased toward slow trips, *and* their measured times
    are too short. Two biases of unknown size pull in opposite directions, so the remainder is not a sample;
    it is a filter artifact.
- D costs something real: TLC gets no compliance answer for Lyft this month. But it gets a specific,
  actionable data request instead of a number that could be overturned.

## Decision

**D.** It is encoded as a rule that treats every dispatcher the same way, not a one-off exception:

1. **Bounds first.** Count every quarantined record as a failure, then as a pass.
   - Both bounds above 90% → **MEETS, proven**. Uber, April: 91.6–96.0%.
   - Both below 90% → **BELOW, proven**. This applies even at low coverage: a provable failure is never
     hidden.
2. **If the bounds straddle 90%**, the verdict depends on what the quarantined records are.
   - The clean-record rate is used, labelled **(estimate)**, only if at least 90% of records are clean.
     Uber, May and July: 94.9% and 94.6% coverage.
   - The 90% line is a stated **policy choice**, set equal to the standard's own 10% tolerance and
     configurable. It is not a mathematical necessity.
3. **Otherwise NOT MEASURABLE.** Lyft: bounds 31–93%, coverage 39–42%. The output is a data-correction
   request.

The scorecard still shows Lyft's naive and filtered numbers, labelled "not decision-grade", so the reasoning
is visible and not hidden. Every verdict state is covered by `tests/test_decision_logic.py`.

## Evidence that the defect is systematic, not noise

Every item below comes from pipeline outputs: `dq_segment_profile.csv`, `dq_negative_response_signature.csv`
and `outputs/validation/<month>/`.

- **Scale and persistence:** 58–61% of Lyft WAV records in each of three months, spread across every day.
  Lyft's non-WAV records fail the same rule at only 1.5%.
- **One path:**
  - All of them come from one base (B03406).
  - Lyft's WAV records are the *only* Lyft records with `originating_base_num` populated (100% of WAV vs 0%
    of non-WAV). That points to a separate record-generation path.
- **Not the benign explanation:** Uber's impossible records carry a pre-booked-ride signature (99.95–100% of
  request times on a whole minute vs ~1.8% chance). Lyft's WAV failures do not: 2.1–2.5%, the chance rate.
- **Even the "valid" rows look wrong:** Lyft WAV's surviving records have P50 6.1 / P90 12.5 min, against Uber
  WAV's 4.55 / 8.5 min and Lyft's own non-WAV 3.5 / 7.5 min.

## Consequence

- **For TLC:** no compliance determination for Lyft from public data. Instead, a data-correction request that
  names the field (`request_datetime`), the base (B03406), the path (WAV records with `originating_base_num`)
  and the size (~13–16 K records per month).
- **For Uber:** 94.3% over April, May and July 2026. That is 3 of the 7 months of 2026 so far, with July still
  provisional. It is a MEETS verdict whose basis is stated: proven in April, estimates in May and July.
  Confirming that the quarantined records are pre-booked rides would make all three proven.
- **For the product:** "NOT MEASURABLE" is a first-class, tested output state, not an error. It is how the
  pipeline says "you need better data", which is the most useful thing an FDE can tell a client here.

## Second judgement call (backup for Q&A): refuse June rather than publish it

- **The gap:** the June 2026 file is 2.14% short of TLC's own monthly report (−454,232 trips). The
  licensee-level control is not yet published.
- **Why the choice mattered:** publishing would have looked fine. Every day is present and the WAV counts
  look normal.
- **What the pipeline did:** it refused the month and wrote the evidence to `outputs/validation/2026-06/`.
  - FIL07 flags Lyft on 8–10 June.
  - The published daily series shows Lyft low through 14 June. Its deficit against the same weekdays
    (≈ 489K) is the size of the gap.
- **The trade-off:** a missing month now, instead of a wrong month that someone might act on.
