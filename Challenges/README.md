# FlashEats in-class challenges (Classes 5–7)

The course's FlashEats challenge notebooks, answered on the real classroom pack. Each instructor prompt is kept
verbatim, with our answer (code + findings) directly below it. All notebooks are executed, so the outputs are
visible on GitHub.

| Notebook | Challenges | Headline answer |
|---|---|---|
| [`FlashEats_Class5_Starter.ipynb`](FlashEats_Class5_Starter.ipynb) | Class 5, SQL-first: size the problem, test "traffic", tickets, reliable API ingestion, driver events, recommendation | 56.4% late (843 / 1,495); delay sits before pickup; don't build the predictor yet |
| [`FlashEats_Class5_Student.ipynb`](FlashEats_Class5_Student.ipynb) | Class 5, full investigation: source map + the same five challenges in depth + one-slide synthesis | Delay correlates 0.94 with order→pickup time vs 0.23 with travel; the 4 "worst" late orders are data errors |
| [`FlashEats_Class6_Student.ipynb`](FlashEats_Class6_Student.ipynb) | Class 6: validation contract, competing definitions of "late", categories, cross-source integrity, freshness, PASS/WARN/FAIL/UNKNOWN gate | Do **not** publish "56%" as is: grain FAILs, definition UNKNOWN (23.3%–58.3% depending on the rule) |
| [`FlashEats_Class7_Challenge.ipynb`](FlashEats_Class7_Challenge.ipynb) | Class 7: order timelines, canonical model, interaction → intervention → outcome table, 5 metrics, workflow questions, KPI linkage | 219 frustrated journeys got no intervention; intervention effect is not identifiable (selection bias) |

## Key findings

**Class 5: where is the problem?**
- **56.4% of delivered orders are late**, but the median miss is only 8 min. 37 delivered orders have no delivery time, so the true rate is 55.0–57.4%.
- **Traffic is associated with lateness (49% → 67%), but the delay accumulates before pickup.** Late orders take 31.9 vs 19.8 min from order to pickup, and are picked up 13.9 min after dispatch's own plan.
- **Data traps:**
  - 3 conflicting duplicate orders.
  - `"Delivered"` vs `"delivered"`.
  - 4 ETAs set *before* the order was created (these are the "worst" late orders).
  - 5 deliveries before pickup.
- **The dispatch API was ingested completely** despite a deliberate HTTP 500 and a 429 (both retried). The raw pages are kept in [`output/`](output/).
- **No "driver arrived at restaurant" event exists**, and GPS pings are about 13 min apart. So the pickup delay cannot be attributed to the restaurant or the driver. Recommendation: instrument arrival and food-ready events before any ML.

**Class 6: is "56%" safe to publish?**
- The number is reproducible, but **grain FAILs** (3 conflicting duplicate order IDs) and **the definition is UNKNOWN**: four stakeholders, four definitions, no owner.
- **Categories:**
  - Case and whitespace variants are safe to normalise.
  - `handoff` vs `handed_off` and `ETA issue` need an owner's call.
- **Mappings:** fine for the company-wide rate, not for ranking individual restaurants or drivers (6 orders lack a restaurant or driver).
- **Restaurant status freshness:** WARN for weekly analytics, FAIL for live ETA and accountability (manual, minute-rounded, "ready" logged after pickup on 60 orders).

**Class 7: model the workflow.**
- **A 6-table canonical model** built around the order: customer, order, event, interaction, intervention, outcome.
- **Source keys can't be trusted blindly:** `interaction_id` is reused across records, and the intervention log contradicts dispatch on at least one reassignment.
- **Metrics:** late rate 56.4% · order-to-pickup 31.9 vs 19.8 min · support contact on late orders 30.6% · preventive intervention share 58.8% · unattended frustration 74.5%.
- **Interventions show no visible effect (56.4% late with or without).** But pre-pickup interventions go to the riskiest orders (69–74% late), so their effect cannot be judged without a comparable control group.

## Run it

```bash
bash Challenges/get_pack.sh                 # clones the instructor pack (pinned commit) into Challenges/flasheats-classroom-pack
pip install -r Challenges/requirements.txt  # pandas, matplotlib, requests, flask, nbconvert, ipykernel (no ML)
cd Challenges && jupyter nbconvert --to notebook --execute --inplace FlashEats_Class5_Student.ipynb
```

- The Class 5 notebooks start the pack's mock dispatch API on port 8000.
- The pack itself (the course data) is not committed. `FLASHEATS_PACK=/path/to/pack` points the notebooks at another copy.
- No notebook writes to the pack. The SQL join of tickets to orders uses an in-memory database with `flasheats.db` attached, so the source database is never modified.
