"""Ops rapor .md dosyalarından metrik tablosu parse — dashboard + 12_ops_report paylaşımı."""
from __future__ import annotations

import re
from pathlib import Path

_VALUE_RE = re.compile(r"^\|\s*(?P<metric>.+?)\s*\|\s*(?P<value>[^|]+?)\s*\|", re.UNICODE)
_NUM_LEAD_RE = re.compile(r"^\$?\s*([\d,]+(?:\.\d+)?)")
_COST_SPREAD_RE = re.compile(
    r"p50\s*\$?([\d.]+)\s*/\s*p90\s*\$?([\d.]+)\s*/\s*p95\s*\$?([\d.]+)\s*/\s*max\s*\$?([\d.]+)",
    re.IGNORECASE,
)


def parse_report_metric_value(raw_value: str) -> float | None:
    raw = raw_value.strip()
    if raw in ("—", "-", "baseline"):
        return None
    if "/" in raw.split()[0]:
        pct_m = re.search(r"\(([\d.]+)%", raw)
        if pct_m:
            return float(pct_m.group(1)) / 100.0
        return None
    num_m = _NUM_LEAD_RE.match(raw.replace(",", ""))
    if not num_m:
        return None
    num = float(num_m.group(1))
    if "%" in raw:
        return num / 100.0
    return num


def parse_cost_spread(raw_value: str) -> dict[str, float]:
    m = _COST_SPREAD_RE.search(raw_value or "")
    if not m:
        return {}
    p50, p90, p95, mx = (float(x) for x in m.groups())
    return {
        "$/claim p50": p50,
        "$/claim p90": p90,
        "$/claim p95": p95,
        "$/claim max": mx,
    }


def metrics_from_report_file(path: Path) -> dict[str, float]:
    if not path.is_file():
        return {}
    text = path.read_text(encoding="utf-8")
    mapping: dict[str, float] = {}
    for line in text.splitlines():
        m = _VALUE_RE.match(line.strip())
        if not m:
            continue
        metric = m.group("metric").strip()
        raw_value = m.group("value")
        if metric.startswith("$/claim p50/p90/p95/max"):
            mapping.update(parse_cost_spread(raw_value))
            continue
        parsed = parse_report_metric_value(raw_value)
        if parsed is not None:
            mapping[metric] = parsed
    return mapping
