# Run logs kept as evidence

`logs/` is git-ignored (every run writes one), so a few real runs from 25 Sep 2026 are preserved here.

| Log | What it shows |
|---|---|
| `first_runs_gzip_content_length_refusal.log` | The first attempt to fetch the TLC monthly report: nyc.gov sent a gzip body, so `Content-Length` (24,863) ≠ decoded bytes (79,774). The byte check refused the file three times, and the month was still published because the primary API control matched exactly. This led to the identity-encoding and wire-byte fix in `ingest.py`. |
| `first_runs_june_refused_july_downloaded.log` | First download of June and July 2026 (~0.5 GB each). June refused by reconciliation (−2.14% vs TLC's monthly report); July published as PROVISIONAL because Open Data had not yet published its control. |
| `example_run_online.log` | Final run of all four months: preserved raw reused after SHA-256 verification, API re-queried for the unpublished months, every check logged. |
| `example_run_offline.log` | The same four months rebuilt with `--offline` (no network). Its outputs are byte-identical to the online run's. |
