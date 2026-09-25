"""Retrieval: completeness proofs, raw preservation, retries and failure modes (offline, fake HTTP)."""

from __future__ import annotations

import json

import pytest

from fixtures import SODA_URL, TRIP_URL, FakeSession, base_aggregate_rows
from wavpipe.config import Month
from wavpipe.errors import RawIntegrityError, RetrievalError
from wavpipe.ingest import RawStore, manifest_path_for, sha256_file

FEB = Month.parse("2026-02")


def dest(cfg):
    return cfg.path("raw") / "tlc_trips" / "fhvhv_tripdata_2026-02.parquet"


def test_download_preserves_raw_with_manifest_then_reuses_it(cfg, session):
    first = RawStore(cfg, session=session).fetch_file("trips", TRIP_URL, dest(cfg))
    manifest = json.loads(manifest_path_for(dest(cfg)).read_text())
    assert first.status == "downloaded"
    assert manifest["sha256"] == sha256_file(dest(cfg))
    assert manifest["bytes"] == manifest["content_length_header"] == dest(cfg).stat().st_size

    second = RawStore(cfg, session=session).fetch_file("trips", TRIP_URL, dest(cfg))
    assert second.status == "cached"
    assert session.get_count("GET", TRIP_URL) == 1, "a rerun must not download again"


def test_truncated_body_is_never_accepted(cfg, session):
    session.truncate.add(TRIP_URL)
    with pytest.raises(RetrievalError, match="truncated"):
        RawStore(cfg, session=session).fetch_file("trips", TRIP_URL, dest(cfg))
    assert session.get_count("GET", TRIP_URL) == cfg.http["retries"]
    assert not dest(cfg).exists()
    assert not list(dest(cfg).parent.glob("*.part")), "partial download must be cleaned up"


def test_transient_network_failure_is_retried(cfg, session):
    session.fail_next[TRIP_URL] = 1
    result = RawStore(cfg, session=session).fetch_file("trips", TRIP_URL, dest(cfg))
    assert result.status == "downloaded" and result.manifest["attempts"] == 2


def test_not_published_month_fails_fast_without_retries(cfg, session):
    session.status[TRIP_URL] = 404
    with pytest.raises(RetrievalError, match="HTTP 404"):
        RawStore(cfg, session=session).fetch_file("trips", TRIP_URL, dest(cfg))
    assert session.get_count("GET", TRIP_URL) == 1


def test_tampered_raw_file_is_detected(cfg, session):
    RawStore(cfg, session=session).fetch_file("trips", TRIP_URL, dest(cfg))
    with open(dest(cfg), "ab") as fh:
        fh.write(b"tampered")
    with pytest.raises(RawIntegrityError):
        RawStore(cfg, session=session).fetch_file("trips", TRIP_URL, dest(cfg))


def test_offline_without_preserved_raw_fails_clearly(cfg, session):
    with pytest.raises(RetrievalError, match="--offline"):
        RawStore(cfg, session=session, offline=True).fetch_file("trips", TRIP_URL, dest(cfg))
    assert session.calls == []


def test_refresh_detects_upstream_revision_and_keeps_old_version(cfg, session, rows, tmp_path):
    RawStore(cfg, session=session).fetch_file("trips", TRIP_URL, dest(cfg))
    old_sha = sha256_file(dest(cfg))
    session.serve(TRIP_URL, b"revised upstream bytes", last_modified="Tue, 17 Mar 2026 10:00:00 GMT")
    result = RawStore(cfg, session=session, refresh=True).fetch_file("trips", TRIP_URL, dest(cfg))
    assert result.status == "refreshed"
    kept = [p for p in (dest(cfg).parent / "superseded").iterdir() if not p.name.endswith(".json")]
    assert len(kept) == 1 and sha256_file(kept[0]) == old_sha


def test_soda_pagination_proves_completeness(cfg, rows):
    session = FakeSession()
    session.soda_rows = base_aggregate_rows(rows) * 2          # 6 rows -> 3 pages of 2
    cfg.raw["sources"]["base_aggregate"]["page_size"] = 2
    result = RawStore(cfg, session=session).fetch_base_aggregate(FEB)
    assert result.status == "downloaded"
    assert result.manifest["expected_rows"] == result.manifest["retrieved_rows"] == 6
    assert [p["rows"] for p in result.manifest["pages"]] == [2, 2, 2]


def test_soda_short_pagination_is_detected_not_trusted(cfg, rows):
    session = FakeSession()
    session.soda_rows = base_aggregate_rows(rows)
    session.soda_drop_last_page_row = True                      # server count(*) = 3, pages deliver 2
    result = RawStore(cfg, session=session).fetch_base_aggregate(FEB)
    assert result.status == "unavailable"
    assert "pagination incomplete" in result.manifest["error"]
    assert not (cfg.path("raw") / "opendata_base_aggregate" / "2026-02" / "manifest.json").exists()


def test_soda_not_yet_published_is_rechecked_on_next_run(cfg, rows):
    session = FakeSession()
    first = RawStore(cfg, session=session).fetch_base_aggregate(FEB)
    assert first.status == "not_published"
    session.soda_rows = base_aggregate_rows(rows)             # TLC publishes the month later
    second = RawStore(cfg, session=session).fetch_base_aggregate(FEB)
    assert second.status == "downloaded" and second.manifest["retrieved_rows"] == 3


def test_soda_api_outage_falls_back_to_preserved_pages(cfg, rows):
    session = FakeSession()
    session.soda_rows = base_aggregate_rows(rows)
    RawStore(cfg, session=session).fetch_base_aggregate(FEB)
    session.status[SODA_URL] = 503
    result = RawStore(cfg, session=session, refresh=True).fetch_base_aggregate(FEB)
    assert result.status == "cached_after_failure" and result.manifest["retrieved_rows"] == 3


def test_compressed_response_is_checked_against_wire_bytes(cfg, session):
    """Regression for the real nyc.gov case: Content-Length counts compressed bytes, not decoded ones."""
    from fixtures import REPORT_URL
    body = session.files[REPORT_URL][0]
    target = cfg.path("raw") / "tlc_monthly_reports" / "data_reports_monthly.csv"
    session.gzip_wire[REPORT_URL] = len(body) // 3            # complete compressed transfer
    assert RawStore(cfg, session=session).fetch_file("monthly_reports", REPORT_URL, target).status == "downloaded"

    target.unlink()
    manifest_path_for(target).unlink()
    session.gzip_wire[REPORT_URL] = len(body) // 3 - 5        # connection dropped mid-transfer
    with pytest.raises(RetrievalError, match="on the wire"):
        RawStore(cfg, session=session).fetch_file("monthly_reports", REPORT_URL, target)


def test_failed_refresh_keeps_the_preserved_raw(cfg, session):
    RawStore(cfg, session=session).fetch_file("trips", TRIP_URL, dest(cfg))
    good_sha = sha256_file(dest(cfg))
    session.serve(TRIP_URL, b"revised upstream bytes", last_modified="Tue, 17 Mar 2026 10:00:00 GMT")
    session.truncate.add(TRIP_URL)                              # the new version never arrives intact
    result = RawStore(cfg, session=session, refresh=True).fetch_file("trips", TRIP_URL, dest(cfg))
    assert result.status == "cached_after_failure"
    assert sha256_file(dest(cfg)) == good_sha
    assert not (dest(cfg).parent / "superseded").exists()
    assert RawStore(cfg, session=session, offline=True).fetch_file("trips", TRIP_URL, dest(cfg)).status == "cached"
