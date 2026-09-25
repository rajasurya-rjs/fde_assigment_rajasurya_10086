"""Configuration loading and small shared helpers."""

from __future__ import annotations

import calendar
import csv
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
MONTH_RE = re.compile(r"^(\d{4})-(0[1-9]|1[0-2])$")


@dataclass(frozen=True)
class Month:
    year: int
    month: int

    @classmethod
    def parse(cls, text: str) -> "Month":
        m = MONTH_RE.match(text.strip())
        if not m:
            raise ValueError(f"Month must look like YYYY-MM, got {text!r}")
        return cls(int(m.group(1)), int(m.group(2)))

    def __str__(self) -> str:
        return f"{self.year:04d}-{self.month:02d}"

    @property
    def start(self) -> date:
        return date(self.year, self.month, 1)

    @property
    def next_start(self) -> date:
        return date(self.year + (self.month == 12), self.month % 12 + 1, 1)

    @property
    def days(self) -> int:
        return calendar.monthrange(self.year, self.month)[1]


@dataclass
class Config:
    raw: dict[str, Any]
    root: Path = ROOT
    licensees: list[dict[str, str]] = field(default_factory=list)
    schema_contract: dict[str, dict[str, str]] = field(default_factory=dict)

    def path(self, key: str) -> Path:
        p = Path(self.raw["paths"][key])
        return p if p.is_absolute() else self.root / p

    @property
    def sources(self) -> dict[str, Any]:
        return self.raw["sources"]

    @property
    def http(self) -> dict[str, Any]:
        return self.raw["http"]

    @property
    def kpi(self) -> dict[str, Any]:
        return self.raw["kpi"]

    @property
    def validation(self) -> dict[str, Any]:
        return self.raw["validation"]

    @property
    def reconciliation(self) -> dict[str, Any]:
        return self.raw["reconciliation"]


def load_config(path: Path | None = None, root: Path | None = None,
                overrides: dict[str, Any] | None = None) -> Config:
    root = root or ROOT
    cfg_path = path or ROOT / "config" / "pipeline.yaml"
    raw = yaml.safe_load(cfg_path.read_text())
    for dotted, value in (overrides or {}).items():
        node = raw
        *parents, leaf = dotted.split(".")
        for key in parents:
            node = node[key]
        node[leaf] = value
    with open(ROOT / "config" / "licensees.csv", newline="") as fh:
        licensees = list(csv.DictReader(fh))
    contract = yaml.safe_load((ROOT / "config" / "schema_contract.yaml").read_text())
    return Config(raw=raw, root=root, licensees=licensees, schema_contract=contract)


def sql_literal(value: str) -> str:
    """Quote a string for inline use in SQL (only used for paths/months we control)."""
    return "'" + str(value).replace("'", "''") + "'"


def render_sql(name: str, **params: str) -> str:
    """Load sql/<name> and substitute {{placeholders}}. Unknown placeholders are an error."""
    text = (ROOT / "sql" / name).read_text()
    for key, value in params.items():
        text = text.replace("{{" + key + "}}", value)
    leftover = re.findall(r"\{\{(\w+)\}\}", text)
    if leftover:
        raise KeyError(f"Unrendered SQL placeholders in {name}: {leftover}")
    return text
