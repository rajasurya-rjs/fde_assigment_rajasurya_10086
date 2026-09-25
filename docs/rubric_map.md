# Where to find the evidence for each grading dimension

| Dimension (20% each) | What to look at | What it proves |
|---|---|---|
| **Source reasoning** | [`README` §1–6](../README.md), [`source_map.md`](source_map.md), [`diagrams/source_map.svg`](../diagrams/source_map.svg) | Question → information → source → fields → metric; owner, grain, refresh, join keys and gaps per source; grain mismatches (trip vs base × month vs class × month per-day); the unserved-request gap made explicit; the dictionary's "WAV-only" note contradicted by the data |
| **Retrieval** | [`src/wavpipe/ingest.py`](../src/wavpipe/ingest.py), `data/raw/**/manifest.json`, [`outputs/validation/<month>/reconciliation.csv`](../outputs/validation/), README §9 | Two genuine modes (HTTPS files + paginated SODA API); `.part` → byte check → SHA-256 → atomic rename; server `count(*)` vs retrieved; exact reconciliation to independent controls; June refused at −2.14%; the gzip `Content-Length` lesson; raw preserved and versioned; `--offline` rebuild |
| **Validation** | [`outputs/profile/`](../outputs/profile/), [`outputs/validation_rules.md`](../outputs/validation_rules.md), [`data_quality_findings.md`](data_quality_findings.md), scorecard §6–7, `tests/test_validation.py` | Profile before cleaning; 12 file-level checks plus 13 row rules across schema / referential / temporal / domain / grain; failure counts and % per segment; quarantine with reasons (nothing silently fixed); the pre-booking signature and segment-profile diagnostics; verdict logic from bounds (proven / estimate / withheld) |
| **Workflow + metrics** | [`diagrams/workflow_data_model.svg`](../diagrams/workflow_data_model.svg), [`sql/`](../sql/), [`metrics.md`](metrics.md), [`outputs/scorecard.md`](../outputs/scorecard.md) | Entities, events, stages, the unobserved state, intervention and outcome; PK/FK model with explicit safe joins; five metrics tied to the regulatory KPI, each with a formula and the decision it supports; M4 shows *where* delay accumulates |
| **Pipeline dependability** | [`diagrams/pipeline_flow.svg`](../diagrams/pipeline_flow.svg), [`src/wavpipe/pipeline.py`](../src/wavpipe/pipeline.py), README §12–13, `tests/test_pipeline.py`, `outputs/last_run.json` | Transactional month publish; byte-identical reruns; last-known-good protection; per-month isolation; retries and fail-fast; schema-drift handling; audit table and run manifest; 54 offline tests |

## Assignment checklist

| Requirement (assignment PDF) | Where |
|---|---|
| Map business questions → information → source systems; ownership, grain, gaps | `docs/source_map.md`, `diagrams/source_map.svg` |
| ≥ 2 retrieval modes; show completeness; preserve raw | `ingest.py`, `data/raw/`, `reconciliation.csv` |
| Profile, meaningful quality issues, business validation rules, assumptions/limitations recorded not silently fixed | `outputs/profile/`, `validation_rules.md`, `data_quality_findings.md`, README §17 |
| Entities, events/states, interventions/outcomes; relational/event model; 3–5 metrics | `workflow_data_model.svg`, `sql/00_schema.sql`, `docs/metrics.md` |
| Repeatable ingest → validate → transform/model → metric output with logging, rerun behaviour, failure handling | `run_pipeline.py`, `pipeline.py`, `pipeline_flow.svg`, tests |
| README: problem, stakeholders, KPI, sources, setup/run, decision | `README.md` |
| Final evidence table / dashboard + Known/Unknown/Assumption/Limitation | `outputs/scorecard.md`, README §16–17 |
| 3–5 minute demo with one FDE judgement call | `docs/demo_script.md`, `docs/judgement_call.md` |

## Interpretation choices (made conservatively, and stated)

- The assignment says "at least two retrieval modes across SQL, API, JSON/CSV/files". This project uses **files
  over HTTPS** (Parquet, CSV) and a **REST API** (Socrata SODA, JSON). DuckDB SQL is used for modelling, but it
  is *not* counted as a retrieval mode, because querying our own warehouse is not retrieval from a client
  system.
- "Track B — NYC TLC" asks for trip-duration/location validation and a monthly pipeline. This project uses the
  High-Volume FHV trip records (not yellow taxi), because only they carry the request and on-scene timestamps
  the regulated KPI needs.
