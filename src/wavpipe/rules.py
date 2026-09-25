"""Row-level validation rules: the single source of truth for checks, reports and docs.

Each predicate is a SQL boolean that is TRUE when the row PASSES. A NULL result counts as a
failure (coalesce(pred, false)), so missing data can never pass a rule by accident.
Temporal/domain rules skip rows whose timestamps are missing: those rows already fail SCH01 (ERROR),
so every quarantined row is attributed to its root cause once instead of to three rules at once.

Severity:
  ERROR -> row is quarantined: excluded from every metric, kept (with reasons) for review.
  WARN  -> row is kept in the metrics; the count is reported so reviewers can judge impact.

Columns available to predicates come from sql/20_stage_trips.sql (view `stg_checked`).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Rule:
    rule_id: str
    category: str   # schema | referential | temporal | domain | grain
    severity: str   # ERROR | WARN
    rule: str
    predicate: str
    why: str
    action: str


RULES: tuple[Rule, ...] = (
    Rule("SCH01", "schema", "ERROR",
         "All four workflow timestamps are present",
         "request_datetime IS NOT NULL AND on_scene_datetime IS NOT NULL "
         "AND pickup_datetime IS NOT NULL AND dropoff_datetime IS NOT NULL",
         "A missing event breaks the request -> arrival -> pickup -> dropoff chain; the response time is unknowable.",
         "Quarantine; exclude from all metrics"),
    Rule("SCH02", "schema", "ERROR",
         "WAV request / match flags are Y or N",
         "wav_request_flag IN ('Y','N') AND wav_match_flag IN ('Y','N')",
         "wav_request_flag defines the KPI population; an unknown value cannot be classified as WAV or not.",
         "Quarantine; exclude from all metrics"),
    Rule("REF01", "referential", "ERROR",
         "Licensee resolves to the TLC licensee reference",
         "licensee_found",
         "The standard applies per dispatcher; a trip that cannot be attributed cannot be scored.",
         "Quarantine; exclude from all metrics"),
    Rule("REF02", "referential", "ERROR",
         "Pickup zone resolves to the TLC zone lookup",
         "pu_zone_found",
         "An unresolvable zone cannot be placed; an inner join would silently drop it from borough views.",
         "Quarantine; exclude from all metrics"),
    Rule("REF03", "referential", "WARN",
         "Pickup zone is inside one of the five NYC boroughs",
         "pu_borough IN ('Manhattan','Brooklyn','Queens','Bronx','Staten Island')",
         "TLC zones 264/265 are 'Unknown'/'Outside of NYC' and zone 1 is Newark Airport; they cannot be assigned to a borough cell.",
         "Keep in dispatcher KPI; exclude from borough x time-band cells (M5)"),
    Rule("TMP01", "temporal", "ERROR",
         "Pickup falls inside the file's month",
         "pickup_datetime IS NULL OR (pickup_datetime >= {{month_start}} AND pickup_datetime < {{next_month_start}})",
         "TLC partitions files by pickup month; a row outside it would be double-counted when adjacent months are loaded.",
         "Quarantine; file fails if > max_out_of_month_pct"),
    Rule("TMP02", "temporal", "ERROR",
         "Vehicle arrives on or after the request (request <= on-scene)",
         "request_datetime IS NULL OR on_scene_datetime IS NULL OR request_datetime <= on_scene_datetime",
         "A negative response time is impossible. A naive '< 10 min' test counts it as compliant and inflates the KPI.",
         "Quarantine; counts against evidence coverage (M2) and the measurability gate"),
    Rule("TMP03", "temporal", "ERROR",
         "Pickup on or after vehicle arrival (on-scene <= pickup)",
         "on_scene_datetime IS NULL OR pickup_datetime IS NULL OR on_scene_datetime <= pickup_datetime",
         "A passenger cannot board before the vehicle arrives; the arrival timestamp is then untrustworthy.",
         "Quarantine; exclude from all metrics"),
    Rule("TMP04", "temporal", "ERROR",
         "Dropoff after pickup",
         "pickup_datetime IS NULL OR dropoff_datetime IS NULL OR dropoff_datetime > pickup_datetime",
         "A zero or negative ride means the record is broken, so its other timestamps are suspect too.",
         "Quarantine; exclude from all metrics"),
    Rule("DOM01", "domain", "WARN",
         "A WAV request was completed in a WAV",
         "wav_request_flag <> 'Y' OR wav_match_flag = 'Y'",
         "A WAV request completed in a non-accessible vehicle is a service failure the response-time KPI would not see.",
         "Keep; report count (a non-zero value is escalated)"),
    Rule("DOM02", "domain", "WARN",
         "Response time at most 60 minutes",
         "response_s IS NULL OR response_s <= {{long_response_s}}",
         "Very long waits may be pre-booked rides, but they are exactly the failures the standard counts. "
         "Dropping them as 'outliers' would inflate compliance.",
         "Keep in KPI (counted as failures); report for review"),
    Rule("DOM03", "domain", "WARN",
         "On-scene time differs from pickup time",
         "on_scene_datetime IS NULL OR pickup_datetime IS NULL OR on_scene_datetime <> pickup_datetime",
         "Arrival and boarding logged at the same second. ~4x more frequent at LGA/JFK pickups (lot/queue "
         "pickups?) but found everywhere; cause unconfirmed. Curbside time is 0 for these rows.",
         "Keep; curbside metrics (M4) carry this caveat"),
    Rule("DOM04", "grain", "ERROR",
         "Record is not an exact duplicate of an earlier record",
         "NOT is_duplicate_copy",
         "The source has no trip ID; an exact copy of a record would double-count a trip.",
         "Keep first occurrence (lowest source row); quarantine the copies"),
)

RULES_BY_ID = {r.rule_id: r for r in RULES}
ERROR_RULES = tuple(r for r in RULES if r.severity == "ERROR")
WARN_RULES = tuple(r for r in RULES if r.severity == "WARN")


def render_predicate(rule: Rule, params: dict[str, str]) -> str:
    text = rule.predicate
    for key, value in params.items():
        text = text.replace("{{" + key + "}}", value)
    return text


def pass_columns_sql(params: dict[str, str]) -> str:
    """SELECT-list fragment: one boolean pass_<RULE> column per rule, NULL treated as fail."""
    return ",\n  ".join(
        f"coalesce(({render_predicate(r, params)}), false) AS pass_{r.rule_id}" for r in RULES)


def is_clean_sql() -> str:
    return " AND ".join(f"pass_{r.rule_id}" for r in ERROR_RULES)


def failed_list_sql(rules: tuple[Rule, ...]) -> str:
    """SQL expression producing e.g. 'TMP02,TMP03' for the rules a row fails ('' if none)."""
    parts = ", ".join(f"CASE WHEN NOT pass_{r.rule_id} THEN '{r.rule_id}' END" for r in rules)
    return f"concat_ws(',', {parts})"
