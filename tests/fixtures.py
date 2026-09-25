"""SYNTHETIC test fixtures (not real TLC data).

A small month (2026-02) is built row by row so every expected number is known in advance.
Each deliberately bad row carries a marker in `dispatching_base_num` (e.g. 'X_TMP02') naming the ONE
rule it is designed to trip, so tests can assert that exactly that rule catches it.
"""

from __future__ import annotations

import hashlib
import io
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import duckdb
import requests

MONTH = "2026-02"
DAYS = 28
TRIP_URL = f"https://d37ci6vzurychx.cloudfront.net/trip-data/fhvhv_tripdata_{MONTH}.parquet"
ZONES_URL = "https://d37ci6vzurychx.cloudfront.net/misc/taxi_zone_lookup.csv"
SODA_URL = "https://data.cityofnewyork.us/resource/2v9c-2k7f.json"
REPORT_URL = "https://www.nyc.gov/assets/tlc/downloads/csv/data_reports_monthly.csv"

SCHEMA = [
    ("hvfhs_license_num", "VARCHAR"), ("dispatching_base_num", "VARCHAR"), ("originating_base_num", "VARCHAR"),
    ("request_datetime", "TIMESTAMP"), ("on_scene_datetime", "TIMESTAMP"), ("pickup_datetime", "TIMESTAMP"),
    ("dropoff_datetime", "TIMESTAMP"), ("PULocationID", "INTEGER"), ("DOLocationID", "INTEGER"),
    ("trip_miles", "DOUBLE"), ("trip_time", "BIGINT"), ("base_passenger_fare", "DOUBLE"), ("tolls", "DOUBLE"),
    ("bcf", "DOUBLE"), ("sales_tax", "DOUBLE"), ("congestion_surcharge", "DOUBLE"), ("airport_fee", "DOUBLE"),
    ("tips", "DOUBLE"), ("driver_pay", "DOUBLE"), ("shared_request_flag", "VARCHAR"),
    ("shared_match_flag", "VARCHAR"), ("access_a_ride_flag", "VARCHAR"), ("wav_request_flag", "VARCHAR"),
    ("wav_match_flag", "VARCHAR"), ("cbd_congestion_fee", "DOUBLE"),
]

ZONES_CSV = """"LocationID","Borough","Zone","service_zone"
1,"EWR","Newark Airport","EWR"
41,"Manhattan","Central Harlem","Boro Zone"
61,"Brooklyn","Crown Heights North","Boro Zone"
69,"Bronx","East Concourse/Concourse Village","Boro Zone"
132,"Queens","JFK Airport","Airports"
156,"Staten Island","Mariners Harbor","Boro Zone"
264,"Unknown","N/A","N/A"
265,"N/A","Outside of NYC","N/A"
"""

MANHATTAN, BRONX, UNKNOWN_ZONE = 41, 69, 264


def trip(lic: str, base: str, request: datetime, response_s: int | None, curbside_s: int = 60,
         ride_s: int = 900, pu: int = MANHATTAN, wav: str = "N", wav_match: str | None = None,
         **overrides: Any) -> dict[str, Any]:
    on_scene = request + timedelta(seconds=response_s) if response_s is not None else None
    pickup = (on_scene or request) + timedelta(seconds=curbside_s)
    row = {
        "hvfhs_license_num": lic, "dispatching_base_num": base, "originating_base_num": None,
        "request_datetime": request, "on_scene_datetime": on_scene, "pickup_datetime": pickup,
        "dropoff_datetime": pickup + timedelta(seconds=ride_s), "PULocationID": pu, "DOLocationID": 61,
        "trip_miles": 2.5, "trip_time": ride_s, "base_passenger_fare": 20.0, "tolls": 0.0, "bcf": 0.5,
        "sales_tax": 1.8, "congestion_surcharge": 0.0, "airport_fee": 0.0, "tips": 0.0, "driver_pay": 15.0,
        "shared_request_flag": "N", "shared_match_flag": "N", "access_a_ride_flag": "N",
        "wav_request_flag": wav, "wav_match_flag": wav_match or wav, "cbd_congestion_fee": 0.0,
    }
    row.update(overrides)
    return row


def at(day: int, hour: int, minute: int = 0, second: int = 17) -> datetime:
    # Default second is non-zero: real on-demand requests rarely land on a whole minute.
    return datetime(2026, 2, day, hour, minute, second)


def build_rows() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    # Background: 6 clean non-WAV trips per day per dispatcher (every day covered).
    for day in range(1, DAYS + 1):
        for i in range(6):
            rows.append(trip("HV0003", "B03404", at(day, 8 + i), 180))
            rows.append(trip("HV0005", "B03406", at(day, 8 + i, 30), 180))

    # Uber WAV: 20 clean records, 18 under 10 min => exactly 90.0% (MEETS; tests the >= boundary).
    for i in range(14):                                   # Manhattan midday: 14/14 under
        rows.append(trip("HV0003", "B03404", at(2 + i, 12), 300, curbside_s=180, wav="Y"))
    for i in range(3):                                    # Bronx evening: 4 under ...
        rows.append(trip("HV0003", "B03404", at(2 + i, 21), 300, curbside_s=180, pu=BRONX, wav="Y"))
    rows.append(trip("HV0003", "X_DOM01", at(6, 21), 300, curbside_s=180, pu=BRONX, wav="Y", wav_match="N"))
    rows.append(trip("HV0003", "X_EXACT600", at(7, 21), 600, curbside_s=180, pu=BRONX, wav="Y"))  # NOT "under"
    rows.append(trip("HV0003", "B03404", at(8, 21), 900, curbside_s=180, pu=BRONX, wav="Y"))
    # ... plus 1 impossible record: on-scene 5 min BEFORE request. Coverage 20/21 = 95.2% (measurable).
    # Its request is on a whole minute: the pre-booked-ride signature seen in real Uber data.
    rows.append(trip("HV0003", "X_TMP02", at(9, 12, 0, 0), -300, curbside_s=180, wav="Y"))

    # Lyft WAV: 4 clean + 6 impossible => coverage 40% => NOT MEASURABLE, though naive rate = 100%.
    # The impossible ones have ordinary (non-whole-minute) request times: no pre-booking signature.
    for i in range(4):
        rows.append(trip("HV0005", "B03406", at(3 + i, 14), 240, curbside_s=120, wav="Y"))
    for i in range(6):
        rows.append(trip("HV0005", "X_TMP02", at(10 + i, 14), -600, curbside_s=120, wav="Y"))

    # One deliberately bad (or warn-worthy) non-WAV row per rule.
    rows.append(trip("HV0003", "X_TMP01", datetime(2026, 2, 28, 23, 58), 300))          # pickup lands 1 Mar
    rows.append(trip("HV0003", "X_REF02", at(11, 9), 180, pu=999))
    rows.append(trip("HV0003", "X_REF03", at(11, 10), 180, pu=UNKNOWN_ZONE))            # WARN only
    rows.append(trip("HV0003", "X_SCH01", at(12, 9), None))                             # on-scene missing
    rows.append(trip("HV0003", "X_SCH02", at(12, 10), 180, wav="X", wav_match="N"))
    rows.append(trip("HV9999", "X_REF01", at(13, 9), 180))
    rows.append(trip("HV0003", "X_TMP03", at(13, 10), 180, curbside_s=-120))           # boards before arrival
    rows.append(trip("HV0003", "X_TMP04", at(14, 9), 180, ride_s=0))
    rows.append(trip("HV0003", "X_DOM02", at(14, 10), 7200))                            # WARN: 2 h response
    rows.append(trip("HV0003", "X_DOM03", at(15, 9), 180, curbside_s=0))                # WARN: on-scene == pickup
    dup = trip("HV0003", "X_DOM04", at(15, 10), 180)
    rows.extend([dup, dict(dup)])                                                       # exact duplicate pair
    return rows


def pad_to_multiple_of_days(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The TLC monthly report publishes trips PER DAY; keep the synthetic total divisible by 28."""
    i = 0
    while len(rows) % DAYS:
        rows.append(trip("HV0005", "B03406", at(1 + i % DAYS, 20), 180))
        i += 1
    return rows


def write_parquet(rows: list[dict[str, Any]], path: Path, drop: tuple[str, ...] = (),
                  extra: dict[str, str] | None = None) -> bytes:
    path.parent.mkdir(parents=True, exist_ok=True)
    schema = [(c, t) for c, t in SCHEMA if c not in drop] + list((extra or {}).items())
    con = duckdb.connect()
    con.execute("CREATE TABLE t (" + ", ".join(f'"{c}" {t}' for c, t in schema) + ")")
    names = [c for c, _ in schema]
    con.executemany(f"INSERT INTO t VALUES ({', '.join('?' for _ in names)})",
                    [[r.get(c) for c in names] for r in rows])
    con.execute(f"COPY t TO '{path}' (FORMAT PARQUET)")
    con.close()
    return path.read_bytes()


def base_aggregate_rows(rows: list[dict[str, Any]], uber_delta: int = 0) -> list[dict[str, str]]:
    n_uber = sum(r["hvfhs_license_num"] == "HV0003" for r in rows) + uber_delta
    n_lyft = sum(r["hvfhs_license_num"] == "HV0005" for r in rows)
    common = {"year": "2026", "month": "2", "month_name": "February", "total_dispatched_shared_trips": "0"}
    return [
        {**common, "base_license_number": "B00001", "base_name": "SOME LIVERY", "total_dispatched_trips": "42",
         "unique_dispatched_vehicles": "3"},
        {**common, "base_license_number": "LYFT", "base_name": "LYFT", "total_dispatched_trips": str(n_lyft),
         "unique_dispatched_vehicles": "9"},
        {**common, "base_license_number": "UBER", "base_name": "UBER", "total_dispatched_trips": str(n_uber),
         "unique_dispatched_vehicles": "9"},
    ]


def monthly_report_csv(total_trips: int | None) -> bytes:
    lines = ["Month/Year,License Class,Trips Per Day,Farebox Per Day,Unique Drivers",
             '2026-01,FHV - High Volume,"700,000",-,"87,000"']
    if total_trips is not None:
        lines.append(f'{MONTH},FHV - High Volume,"{total_trips // DAYS:,}",-,"87,000"')
    return ("\n".join(lines) + "\n").encode()


# ------------------------------------------------------------------------------ fake HTTP
class _Wire:
    """Stands in for urllib3's raw response: tell() = bytes received on the wire (compressed)."""

    def __init__(self, wire_bytes: int):
        self._n = wire_bytes

    def tell(self) -> int:
        return self._n


class FakeResponse:
    def __init__(self, status: int = 200, body: bytes = b"", headers: dict[str, str] | None = None,
                 declared_length: int | None = None, wire_bytes: int | None = None):
        self.status_code = status
        self._body = body
        self.headers = {"Content-Length": str(declared_length if declared_length is not None else len(body))}
        self.headers.update(headers or {})
        self.text = body.decode(errors="replace")
        if wire_bytes is not None:
            self.raw = _Wire(wire_bytes)

    def iter_content(self, chunk_size: int = 1024):
        stream = io.BytesIO(self._body)
        while chunk := stream.read(chunk_size):
            yield chunk

    def json(self) -> Any:
        return json.loads(self._body)

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *exc: Any) -> None:
        return None


class FakeSession:
    """Serves files by URL and emulates the Socrata SODA API ($select=count(*), $limit/$offset)."""

    def __init__(self) -> None:
        self.files: dict[str, tuple[bytes, dict[str, str]]] = {}
        self.soda_rows: list[dict[str, str]] = []
        self.soda_drop_last_page_row = False
        self.fail_next: dict[str, int] = {}        # url -> number of ConnectionErrors to raise first
        self.truncate: set[str] = set()            # urls whose body is cut short vs Content-Length
        self.status: dict[str, int] = {}
        self.gzip_wire: dict[str, int] = {}        # url -> compressed bytes actually sent on the wire
        self.calls: list[tuple[str, str]] = []

    def serve(self, url: str, body: bytes, last_modified: str = "Mon, 02 Mar 2026 10:00:00 GMT") -> None:
        self.files[url] = (body, {"Last-Modified": last_modified,
                                  "ETag": '"' + hashlib.md5(body).hexdigest() + '"'})

    def head(self, url: str, **_: Any) -> FakeResponse:
        self.calls.append(("HEAD", url))
        body, headers = self.files[url]
        return FakeResponse(200, b"", headers, declared_length=len(body))

    def get(self, url: str, params: dict[str, Any] | None = None, **_: Any) -> FakeResponse:
        self.calls.append(("GET", url))
        if self.fail_next.get(url, 0) > 0:
            self.fail_next[url] -= 1
            raise requests.ConnectionError("simulated connection reset")
        if url in self.status:
            return FakeResponse(self.status[url], b"error")
        if url == SODA_URL:
            return self._soda(params or {})
        body, headers = self.files[url]
        if url in self.truncate:
            return FakeResponse(200, body[: len(body) // 2], headers, declared_length=len(body))
        if url in self.gzip_wire:   # server ignored Accept-Encoding: identity and compressed anyway
            return FakeResponse(200, body, {**headers, "Content-Encoding": "gzip"},
                                declared_length=len(body) // 3, wire_bytes=self.gzip_wire[url])
        return FakeResponse(200, body, headers)

    def _soda(self, params: dict[str, Any]) -> FakeResponse:
        where = params.get("$where", "")
        year, month = (int(x.split("=")[1]) for x in where.split(" AND "))
        rows = [r for r in self.soda_rows if int(r["year"]) == year and int(r["month"]) == month]
        if params.get("$select", "").startswith("count(*)"):
            return FakeResponse(200, json.dumps([{"n": str(len(rows))}]).encode())
        rows = sorted(rows, key=lambda r: r["base_license_number"])
        if self.soda_drop_last_page_row:
            rows = rows[:-1]
        off, lim = int(params["$offset"]), int(params["$limit"])
        return FakeResponse(200, json.dumps(rows[off: off + lim]).encode())

    def get_count(self, method: str, url: str) -> int:
        return sum(1 for m, u in self.calls if m == method and u == url)


def standard_session(rows: list[dict[str, Any]], tmp: Path, **kw: Any) -> FakeSession:
    session = FakeSession()
    session.serve(TRIP_URL, write_parquet(rows, tmp / "_src" / "trips.parquet", **kw))
    session.serve(ZONES_URL, ZONES_CSV.encode())
    session.serve(REPORT_URL, monthly_report_csv(len(rows)))
    session.soda_rows = base_aggregate_rows(rows)
    return session
