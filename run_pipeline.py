#!/usr/bin/env python3
"""Run the WAV Service Scorecard pipeline.

Examples
  python run_pipeline.py --month 2026-05                  # fetch (or reuse preserved raw), validate, publish
  python run_pipeline.py --month 2026-05 --month 2026-07  # several months; each succeeds or fails on its own
  python run_pipeline.py --month 2026-05 --offline        # rebuild everything from preserved raw only
  python run_pipeline.py --month 2026-05 --refresh        # re-check upstream; keep superseded raw versions

Exit code: 0 = every month published, 1 = at least one month failed, 2 = bad arguments.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from wavpipe.config import Month, load_config  # noqa: E402
from wavpipe.pipeline import run  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--month", action="append", required=True, help="YYYY-MM (repeatable)")
    parser.add_argument("--offline", action="store_true", help="never touch the network; use preserved raw only")
    parser.add_argument("--refresh", action="store_true", help="re-check upstream sources for revisions")
    parser.add_argument("--allow-unreconciled", action="store_true",
                        help="publish PROVISIONAL metrics even if control totals disagree")
    parser.add_argument("--config", type=Path, default=None, help="alternate pipeline.yaml")
    args = parser.parse_args(argv)
    if args.offline and args.refresh:
        parser.error("--offline and --refresh are mutually exclusive")
    try:
        months = sorted({Month.parse(m) for m in args.month}, key=str)
    except ValueError as exc:
        parser.error(str(exc))
    summary = run(months, load_config(args.config), offline=args.offline, refresh=args.refresh,
                  allow_unreconciled=args.allow_unreconciled)
    print(f"\nrun {summary['run_id']}: {summary.get('status')}")
    for month, res in summary.get("months", {}).items():
        extra = (f"{res['retrieval_status']}, metrics {res['metric_status']}" if res["status"] == "PUBLISHED"
                 else f"{res['error_type']}: {res['error']}")
        print(f"  {month}: {res['status']} ({extra}) in {res['seconds']}s")
    print("  scorecard: outputs/scorecard.md | run manifest: outputs/last_run.json")
    return 0 if summary.get("status") == "OK" else 1


if __name__ == "__main__":
    sys.exit(main())
