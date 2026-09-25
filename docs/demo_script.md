# Demo script (≈ 4½ minutes)

Show evidence and reasoning, not code. Open these tabs beforehand: the GitHub README, `diagrams/source_map.svg`,
`outputs/scorecard.md`, a terminal in the repo, and `docs/judgement_call.md`.

---

**0:00–0:30 · Problem and decision** *(README, top)*

> "TLC requires Uber and Lyft to get a wheelchair-accessible vehicle to 90% of requests in under ten minutes.
> Since April 2025 that has been enforceable per calendar year, with a notice and 30 days to comply. The
> question is simple: *is each company meeting it this month, and where aren't wheelchair users being served?*
> The decision it feeds: open a compliance review, request corrected data, or target WAV supply somewhere."

**0:30–1:15 · Sources and workflow** *(source map, then the workflow diagram)*

> "One regulated KPI, four sources, two retrieval modes:
> - The trip file, 21 million rows a month over HTTPS. It has no trip ID.
> - The zone lookup, for 'where'.
> - Two independent control totals: an Open Data API, paginated against a server count, and TLC's monthly
>   report.
>
> The workflow is request, vehicle on scene, pickup, dropoff. The rule clocks request to on-scene. Rider wait
> adds curbside time. And here, in red, is the gap: requests that were never served aren't in any public data,
> so every rate is on completed trips."

**1:15–2:00 · Data quality and validation** *(scorecard §1 and §6; validation_rules.md)*

> "Before any metric, the month has to prove it's complete. April and May match the API to the trip, exactly.
> July matches the monthly report to 0.0003%, so it's published as *provisional*. **June was refused**: 454
> thousand trips short of TLC's own report. The per-licensee check flags Lyft on 8 to 10 June, and the daily
> series shows Lyft low all week. The refusal and its evidence are files in the repo, not a log line."
>
> Then 13 business rules run on every row. The big one: a vehicle can't arrive before it was requested. For Uber
> that happens on 5% of WAV trips, and nearly all of them have request times on an exact minute. That's the
> signature of pre-booked rides. For Lyft it's **60% of WAV trips**, with no such signature."

**2:00–3:00 · Metrics and insight** *(scorecard §2–§5)*

> "Five metrics:
> - **Uber meets the standard every month**, but the margin shrinks: 95.8, 94.8, 92.2. April is *proven*: it
>   holds even if every quarantined record failed. May and July are labelled *estimates* because their bounds
>   cross 90%.
> - At P90, wheelchair users are reached as fast as other Uber riders (M3), though about a minute slower at the
>   median.
> - **79 to 85% of their extra wait happens after the vehicle arrives** (M4), which the ten-minute rule never
>   sees.
> - M5 only flags a borough-hour window when it's significantly below 90%, not just noisily below. Queens
>   evenings are flagged every month. In July they fell to 70% while non-WAV riders got 82%: a WAV-specific
>   shortfall, and a place to push supply."

**3:00–4:00 · Pipeline execution and dependability** *(terminal)*

```bash
python run_pipeline.py --month 2026-05 --offline      # rebuilds from preserved raw, no network, ~45 s
python -m pytest -q                                   # 54 tests, ~45 s
```

> "Run it twice and you get byte-identical outputs: each month is replaced in one transaction, and primary keys
> enforce the grain. `--offline` rebuilds everything from preserved raw with SHA-256 manifests. The tests
> include a synthetic month with one deliberately bad row per rule, plus a truncated download, a schema change,
> a tampered raw file and a control mismatch. Each one is caught, and a failed month never overwrites the last
> good one."

**4:00–4:45 · The judgement call** *(judgement_call.md, options table)*

> "The call I want to defend is about Lyft. The naive number says 92.7%: compliant. Drop the impossible rows and
> it says 81%: non-compliant, notice issued. They can't both be right, and here neither is. The defect is
> systematic: one base, every day, a separate record path, no benign signature. So the rows that survive
> filtering are a biased slice. Imputing would invent the regulated number.
>
> So the pipeline has one rule for everyone. Compute bounds, treating every excluded record as a fail, then as a
> pass. If both bounds agree, the verdict is proven; that's Uber in April. If they disagree, I use the
> clean-record rate only when at least 90% of records are clean; that's Uber in May and July, labelled as
> estimates. Otherwise, no verdict. Lyft's bounds run from 31 to 93%, with 60% of records excluded. So the
> output is a specific data-correction request. A missing number, clearly explained, beats a confident wrong one
> that triggers enforcement."
