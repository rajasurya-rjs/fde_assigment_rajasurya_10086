"""Retrieval layer. Two genuine retrieval modes:

1. Files over HTTPS (TLC CloudFront Parquet, TLC CSVs)   -> fetch_file()
2. REST API with pagination (NYC Open Data Socrata SODA) -> fetch_base_aggregate()

Rules that apply to every source:
* Raw bytes are preserved exactly as received under data/raw/<source>/ with a JSON manifest
  (URL, retrieval time, byte count, SHA-256, upstream Last-Modified/ETag).
* A download is written to <file>.part and only renamed into place after the byte count matches
  Content-Length, so a dropped connection can never leave a truncated file that looks complete.
* Reruns reuse preserved raw (after re-verifying its SHA-256) so results are reproducible.
  --refresh re-checks upstream; if the source changed, the old version is moved to superseded/.
* --offline never touches the network and fails clearly if a required raw input is missing.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

from .config import Config, Month
from .errors import RawIntegrityError, RetrievalError

log = logging.getLogger("wavpipe.ingest")

RETRYABLE_STATUS = {429, 500, 502, 503, 504}


@dataclass
class FetchResult:
    source: str
    path: Path
    status: str  # downloaded | cached | refreshed | cached_after_failure | not_published | unavailable
    manifest: dict[str, Any]


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while block := fh.read(chunk):
            h.update(block)
    return h.hexdigest()


def manifest_path_for(path: Path) -> Path:
    return path.with_name(path.name + ".manifest.json")


def write_json_atomic(path: Path, payload: Any) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n")
    os.replace(tmp, path)


class RawStore:
    def __init__(self, cfg: Config, session: Any | None = None,
                 offline: bool = False, refresh: bool = False):
        self.cfg = cfg
        self.raw_dir = cfg.path("raw")
        self.session = session or requests.Session()
        self.offline = offline
        self.refresh = refresh
        http = cfg.http
        self.timeout = http["timeout_s"]
        self.retries = http["retries"]
        self.backoff = http["backoff_s"]
        self.chunk = http["chunk_bytes"]
        self.headers = {"User-Agent": http["user_agent"]}

    # ------------------------------------------------------------------ files over HTTPS
    def fetch_file(self, source: str, url: str, dest: Path, force_check: bool = False) -> FetchResult:
        """Return a verified local copy of `url`, downloading only when needed.

        force_check: re-check upstream even without --refresh (used when the cached copy is
        known to be missing something we need, e.g. a month row not yet in a cached report).
        """
        dest.parent.mkdir(parents=True, exist_ok=True)
        mpath = manifest_path_for(dest)
        if dest.exists() and mpath.exists():
            manifest = json.loads(mpath.read_text())
            actual = sha256_file(dest)
            if actual != manifest["sha256"]:
                raise RawIntegrityError(
                    f"{dest} SHA-256 {actual[:12]} does not match manifest {manifest['sha256'][:12]}; "
                    "the preserved raw file was modified or corrupted. Delete it (and its manifest) to re-fetch.")
            if self.offline or not (self.refresh or force_check):
                log.info("[%s] using preserved raw %s (sha256 %s verified)", source, dest.name, actual[:12])
                return FetchResult(source, dest, "cached", manifest)
            try:
                head = self._head(url)
            except RetrievalError as exc:
                log.warning("[%s] upstream check failed (%s); keeping preserved raw", source, exc)
                return FetchResult(source, dest, "cached_after_failure", manifest)
            if self._same_version(head, manifest):
                log.info("[%s] upstream unchanged since %s; reusing %s", source, manifest["retrieved_at"], dest.name)
                return FetchResult(source, dest, "cached", manifest)
            log.warning("[%s] upstream changed (Last-Modified %s -> %s); fetching the new version",
                        source, manifest.get("last_modified"), head.get("Last-Modified"))
            # Download the new version NEXT TO the old one; only once it is complete and verified is the
            # old version moved to superseded/. A failed refresh therefore never loses the preserved raw.
            incoming = dest.with_name(dest.name + ".incoming")
            try:
                new_manifest = self._download(source, url, incoming)
            except RetrievalError as exc:
                log.warning("[%s] refresh failed (%s); keeping preserved raw from %s",
                            source, exc, manifest["retrieved_at"])
                return FetchResult(source, dest, "cached_after_failure", manifest)
            self._supersede(dest, manifest)
            os.replace(incoming, dest)
            os.replace(manifest_path_for(incoming), manifest_path_for(dest))
            log.warning("[%s] old version preserved in superseded/", source)
            return FetchResult(source, dest, "refreshed", new_manifest)

        if dest.exists():
            log.warning("[%s] %s exists without a manifest (interrupted run?) - treating as untrusted", source, dest.name)
        if self.offline:
            raise RetrievalError(f"[{source}] --offline set but no preserved raw copy at {dest}")
        manifest = self._download(source, url, dest)
        return FetchResult(source, dest, "downloaded", manifest)

    def _head(self, url: str) -> dict[str, str]:
        try:
            # Same encoding as the download, so Content-Length is comparable with the manifest's byte count.
            resp = self.session.head(url, headers={**self.headers, "Accept-Encoding": "identity"},
                                     timeout=self.timeout, allow_redirects=True)
        except requests.RequestException as exc:
            raise RetrievalError(f"HEAD {url} failed: {exc}") from exc
        if resp.status_code != 200:
            raise RetrievalError(f"HEAD {url} returned HTTP {resp.status_code}")
        return dict(resp.headers)

    @staticmethod
    def _same_version(head: dict[str, str], manifest: dict[str, Any]) -> bool:
        size = head.get("Content-Length")
        if size is not None and int(size) != manifest["bytes"]:
            return False
        for key, mkey in (("ETag", "etag"), ("Last-Modified", "last_modified")):
            if head.get(key) and manifest.get(mkey) and head[key] != manifest[mkey]:
                return False
        return True

    def _supersede(self, dest: Path, manifest: dict[str, Any]) -> None:
        stamp = manifest["retrieved_at"].replace(":", "").replace("-", "")
        target_dir = dest.parent / "superseded"
        target_dir.mkdir(exist_ok=True)
        shutil.move(str(dest), target_dir / f"{dest.name}.{stamp}")
        shutil.move(str(manifest_path_for(dest)), target_dir / f"{dest.name}.{stamp}.manifest.json")

    def _download(self, source: str, url: str, dest: Path) -> dict[str, Any]:
        part = dest.with_name(dest.name + ".part")
        last_error: Exception | None = None
        for attempt in range(1, self.retries + 1):
            started = time.monotonic()
            try:
                # identity encoding: we want the file byte-for-byte as published (so its SHA-256 is stable).
                headers = {**self.headers, "Accept-Encoding": "identity"}
                with self.session.get(url, headers=headers, stream=True, timeout=self.timeout) as resp:
                    if resp.status_code in (403, 404):
                        raise RetrievalError(
                            f"[{source}] HTTP {resp.status_code} for {url} - not published (yet) or access denied; not retrying")
                    if resp.status_code != 200:
                        raise _Retryable(f"HTTP {resp.status_code}")
                    expected = resp.headers.get("Content-Length")
                    encoding = resp.headers.get("Content-Encoding", "identity").lower()
                    digest, received = hashlib.sha256(), 0
                    with open(part, "wb") as fh:
                        for block in resp.iter_content(chunk_size=self.chunk):
                            fh.write(block)
                            digest.update(block)
                            received += len(block)
                    # Content-Length counts bytes ON THE WIRE. If the server compressed anyway
                    # (e.g. nyc.gov gzips CSVs), compare it with wire bytes, not decoded bytes.
                    wire = received
                    if encoding not in ("", "identity"):
                        raw = getattr(resp, "raw", None)
                        wire = raw.tell() if raw is not None and hasattr(raw, "tell") else None
                    if expected is not None and wire is not None and wire != int(expected):
                        raise _Retryable(f"truncated body: received {wire:,} of {int(expected):,} bytes on the wire")
                    if received == 0:
                        raise _Retryable("empty body")
                    headers = dict(resp.headers)
                manifest = {
                    "source": source,
                    "url": url,
                    "retrieved_at": utc_now(),
                    "bytes": received,
                    "sha256": digest.hexdigest(),
                    "content_length_header": int(expected) if expected is not None else None,
                    "content_encoding": encoding,
                    "last_modified": headers.get("Last-Modified"),
                    "etag": headers.get("ETag"),
                    "attempts": attempt,
                    "seconds": round(time.monotonic() - started, 1),
                }
                os.replace(part, dest)
                write_json_atomic(manifest_path_for(dest), manifest)
                log.info("[%s] downloaded %s: %s bytes, sha256 %s (attempt %d)",
                         source, dest.name, f"{received:,}", manifest["sha256"][:12], attempt)
                return manifest
            except RetrievalError:
                raise
            except (_Retryable, requests.RequestException) as exc:
                last_error = exc
                wait = self.backoff * 2 ** (attempt - 1)
                log.warning("[%s] attempt %d/%d failed: %s%s", source, attempt, self.retries, exc,
                            f" - retrying in {wait}s" if attempt < self.retries else "")
                if attempt < self.retries:
                    time.sleep(wait)
            finally:
                if part.exists():
                    part.unlink()
        raise RetrievalError(f"[{source}] giving up on {url} after {self.retries} attempts: {last_error}")

    # ------------------------------------------------------------------ Socrata SODA API
    def fetch_base_aggregate(self, month: Month) -> FetchResult:
        """Fetch one month of the FHV Base Aggregate Report via paginated SODA API calls.

        Completeness proof: ask the server for count(*) with the same $where, then page with a
        total ordering ($order=base_license_number,:id) and require retrieved == expected.
        A missing control total is not fatal: metrics are published as PROVISIONAL instead.
        """
        source = "base_aggregate"
        cfg = self.cfg.sources[source]
        out_dir = self.raw_dir / "opendata_base_aggregate" / str(month)
        mpath = out_dir / "manifest.json"
        cached = json.loads(mpath.read_text()) if mpath.exists() else None

        if cached and cached["status"] == "complete":
            for page in cached["pages"]:
                if sha256_file(out_dir / page["file"]) != page["sha256"]:
                    raise RawIntegrityError(f"{out_dir / page['file']} does not match its manifest checksum")
            if self.offline or not self.refresh:
                log.info("[%s] using preserved API pages for %s (%d rows)", source, month, cached["retrieved_rows"])
                return FetchResult(source, out_dir, "cached", cached)
        if self.offline:
            if cached:
                return FetchResult(source, out_dir, "cached", cached)
            log.warning("[%s] --offline and no preserved API response for %s", source, month)
            return FetchResult(source, out_dir, "unavailable", {"status": "unavailable"})

        where = f"year={month.year} AND month={month.month}"
        try:
            count_rows = self._soda_get(cfg["endpoint"], {"$select": "count(*) AS n", "$where": where})
            expected = int(count_rows[0]["n"])
            manifest: dict[str, Any] = {
                "source": source, "endpoint": cfg["endpoint"], "where": where,
                "order": "base_license_number,:id", "page_size": cfg["page_size"],
                "retrieved_at": utc_now(), "expected_rows": expected, "pages": [],
            }
            if expected == 0:
                manifest.update(status="not_published", retrieved_rows=0)
                out_dir.mkdir(parents=True, exist_ok=True)
                write_json_atomic(mpath, manifest)
                log.warning("[%s] no rows for %s yet (TLC publishes on a ~2-month lag)", source, month)
                return FetchResult(source, out_dir, "not_published", manifest)

            staging = out_dir.with_name(out_dir.name + ".staging")
            shutil.rmtree(staging, ignore_errors=True)
            staging.mkdir(parents=True)
            offset, retrieved, page_no = 0, 0, 0
            while offset < expected:
                rows = self._soda_get(cfg["endpoint"], {
                    "$where": where, "$order": "base_license_number,:id",
                    "$limit": cfg["page_size"], "$offset": offset,
                })
                if not rows:
                    break
                name = f"page_{page_no:04d}.json"
                body = json.dumps(rows, indent=1, sort_keys=True) + "\n"
                (staging / name).write_text(body)
                manifest["pages"].append({"file": name, "offset": offset, "rows": len(rows),
                                          "sha256": hashlib.sha256(body.encode()).hexdigest()})
                retrieved += len(rows)
                offset += cfg["page_size"]
                page_no += 1
            if retrieved != expected:
                raise RetrievalError(f"[{source}] pagination incomplete for {month}: "
                                     f"server count(*)={expected}, retrieved={retrieved}")
            manifest.update(status="complete", retrieved_rows=retrieved)
            write_json_atomic(staging / "manifest.json", manifest)
            if out_dir.exists():
                old_hashes = [p["sha256"] for p in (cached or {}).get("pages", [])]
                if cached and cached["status"] == "complete" and old_hashes != [p["sha256"] for p in manifest["pages"]]:
                    stamp = cached["retrieved_at"].replace(":", "").replace("-", "")
                    superseded = out_dir.parent / "superseded" / f"{month}.{stamp}"
                    superseded.parent.mkdir(exist_ok=True)
                    shutil.move(str(out_dir), superseded)
                    log.warning("[%s] control data for %s changed upstream; previous pages kept in %s",
                                source, month, superseded)
                else:
                    shutil.rmtree(out_dir)
            os.replace(staging, out_dir)
            log.info("[%s] %s: %d rows in %d pages; server count(*)=%d -> complete",
                     source, month, retrieved, page_no, expected)
            return FetchResult(source, out_dir, "downloaded", manifest)
        except RetrievalError as exc:
            if cached and cached["status"] == "complete":
                log.warning("[%s] API failed (%s); falling back to preserved pages from %s",
                            source, exc, cached["retrieved_at"])
                return FetchResult(source, out_dir, "cached_after_failure", cached)
            log.warning("[%s] API unavailable for %s (%s); control total unavailable", source, month, exc)
            return FetchResult(source, out_dir, "unavailable", {"status": "unavailable", "error": str(exc)})

    def _soda_get(self, endpoint: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        last_error: Exception | None = None
        for attempt in range(1, self.retries + 1):
            try:
                resp = self.session.get(endpoint, params=params, headers=self.headers, timeout=self.timeout)
                if resp.status_code in RETRYABLE_STATUS:
                    raise _Retryable(f"HTTP {resp.status_code}")
                if resp.status_code != 200:
                    raise RetrievalError(f"SODA HTTP {resp.status_code}: {resp.text[:200]}")
                payload = resp.json()
                if not isinstance(payload, list):
                    raise RetrievalError(f"SODA returned non-list payload: {str(payload)[:200]}")
                return payload
            except RetrievalError:
                raise
            except (_Retryable, requests.RequestException, ValueError) as exc:
                last_error = exc
                if attempt < self.retries:
                    time.sleep(self.backoff * 2 ** (attempt - 1))
        raise RetrievalError(f"SODA request failed after {self.retries} attempts: {last_error}")


class _Retryable(Exception):
    """Transient failure worth retrying (5xx, truncated body, connection reset)."""
