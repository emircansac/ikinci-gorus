"""Ops rapor dosyalarını okuyup dashboard için özet üretir."""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from utils.ops_report_parse import metrics_from_report_file

SKIP_NAME_PARTS = ("PAUSE", "554-close", "pre554", "cost-phase", "baseline")
_HEADER_CLAIMS_RE = re.compile(r"Toplam iddia \(aktif\):\s*\*\*(\d+)\*\*")
_HEADER_VERDICTS_RE = re.compile(r"Verdict almış:\s*\*\*(\d+)\*\*")
_HEADER_VIDEOS_RE = re.compile(r"Video sayısı:\s*\*\*(\d+)\*\*")
_SCOPE_RE = re.compile(r"\*\*Kapsam:\*\*\s*(.+)")
_REPORT_DATE_RE = re.compile(r"# Üretim izleme raporu — (\d{4}-\d{2}-\d{2})")


def find_latest_ops_report(ops_dir: Path) -> Path | None:
    if not ops_dir.is_dir():
        return None
    candidates: list[Path] = []
    for path in ops_dir.glob("*.md"):
        if any(part in path.name for part in SKIP_NAME_PARTS):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        if "## Özet metrikler" not in text:
            continue
        candidates.append(path)
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


def _parse_warnings(text: str) -> list[str]:
    warnings: list[str] = []
    in_section = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("## ⚠️ Uyarılar"):
            in_section = True
            continue
        if in_section:
            if stripped.startswith("## ") or stripped.startswith("**Kapsam:**"):
                break
            if stripped.startswith("- ⚠️"):
                warnings.append(stripped[2:].strip())
            elif stripped.startswith("- "):
                warnings.append(stripped[2:].strip())
    return warnings


def _parse_header_int(text: str, pattern: re.Pattern[str]) -> int | None:
    m = pattern.search(text)
    return int(m.group(1)) if m else None


def load_ops_summary(ops_dir: Path) -> dict | None:
    path = find_latest_ops_report(ops_dir)
    if path is None:
        return None

    text = path.read_text(encoding="utf-8")
    metrics = metrics_from_report_file(path)
    warnings = _parse_warnings(text)

    date_m = _REPORT_DATE_RE.search(text)
    report_date = date_m.group(1) if date_m else None
    if not report_date:
        try:
            report_date = datetime.strptime(path.stem[:10], "%Y-%m-%d").date().isoformat()
        except ValueError:
            report_date = datetime.fromtimestamp(path.stat().st_mtime).date().isoformat()

    scope_m = _SCOPE_RE.search(text)
    scope = scope_m.group(1).strip() if scope_m else None

    n_claims = _parse_header_int(text, _HEADER_CLAIMS_RE)
    n_verdicts = _parse_header_int(text, _HEADER_VERDICTS_RE)
    n_videos = _parse_header_int(text, _HEADER_VIDEOS_RE)

    cost_per_claim = metrics.get("$/claim (tahmini)")
    needs_human_rate = metrics.get("needs_human oranı")
    retrieval_cited_rate = metrics.get("retrieval_cited oranı (escalated)")

    if n_claims is None:
        processed = metrics.get("processed (verdict almış)")
        if processed is not None:
            n_claims = int(processed)

    return {
        "source_file": path.name,
        "report_date": report_date,
        "scope": scope,
        "n_claims": n_claims,
        "n_verdicts": n_verdicts,
        "n_videos": n_videos,
        "cost_per_claim_usd": cost_per_claim,
        "needs_human_rate": needs_human_rate,
        "retrieval_cited_rate": retrieval_cited_rate,
        "has_alarms": bool(warnings),
        "warnings": warnings,
    }
