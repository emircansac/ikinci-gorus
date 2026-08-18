"""
Maliyet optimizasyonu Faz 0 — mevcut log + batch re-retrieve ile ölçüm.

A) web_search tool_use sayımı (batch kesin, sync proxy)
B) no_direct_evidence_expected davranışı
C) Kanıt paketi snippet simülasyonu (6 referans iddia)

Kullanım:
    python pipeline/17_cost_phase0_report.py
    python pipeline/17_cost_phase0_report.py --skip-batch-retrieve
    python pipeline/17_cost_phase0_report.py --out data/ops_reports/2026-08-18-cost-phase0.md
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import re
import statistics
import sys
from collections import Counter
from datetime import date
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent))

from utils.claude_client import iter_batch_results, count_web_search_calls
from utils.db import get_conn
from utils.evidence_retrieval import (
    EPISTEMIC_NO_DIRECT,
    assess_evidence_sufficiency,
    best_evidence_snippet,
    _get_embedder,
    _specificity_nli_result,
    _top_candidate,
    filter_candidates_by_key_terms,
)

ROOT = Path(__file__).parent.parent
DEBUG_LOG = ROOT / "data" / "factcheck_debug.jsonl"
PENDING_BATCHES = ROOT / "data" / "pending_batches.json"
JP5_LOG = ROOT / "data" / "smoke_jP5XF06OLbo" / "factcheck_20.log"
OUT_JSON = ROOT / "data" / "ops_reports" / "2026-08-18-cost-phase0.json"
BATCH_SEARCH_CACHE = ROOT / "data" / "ops_reports" / "batch_search_counts_cache.json"

SCOPE_TEST_VIDEOS = ("odZgEDFDmbE", "bZsorXWeLhM", "jP5XF06OLbo")
REFERENCE_IDS = (752, 1284, 745, 663, 1243, 1248)
EXPENSIVE_IDS = (1243, 1265, 1255, 1267, 1247, 1262)
EXPECTED_TIERS = {
    752: "supportive",
    1284: "direct",
    745: "supportive",
    663: "background",
    1243: "background",
    1248: "background",
}
WEB_SEARCH_CITE = frozenset({"web_search_override", "web_search_only"})

PRICE_BATCH_IN = 2.0 / 1_000_000 * 0.5
PRICE_BATCH_OUT = 10.0 / 1_000_000 * 0.5
PRICE_CACHE_WRITE = 2.5 / 1_000_000 * 0.5
PRICE_CACHE_READ = 0.20 / 1_000_000
PRICE_SYNC_IN = 2.0 / 1_000_000
PRICE_SYNC_OUT = 10.0 / 1_000_000


def _load_shadow12():
    path = Path(__file__).parent / "12_specificity_offline.py"
    spec = importlib.util.spec_from_file_location("specificity_offline_mod", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _estimate_cost_usd(usage: dict | None, *, batch: bool = True) -> float | None:
    if not usage:
        return None
    inp = int(usage.get("input_tokens") or 0)
    out = int(usage.get("output_tokens") or 0)
    cw = int(usage.get("cache_creation_input_tokens") or 0)
    cr = int(usage.get("cache_read_input_tokens") or 0)
    if inp == 0 and out == 0 and cw == 0 and cr == 0:
        return None
    if batch or cw or cr:
        return (
            inp * PRICE_BATCH_IN
            + out * PRICE_BATCH_OUT
            + cw * PRICE_CACHE_WRITE
            + cr * PRICE_CACHE_READ
        )
    return inp * PRICE_SYNC_IN + out * PRICE_SYNC_OUT


def _cohort_claim_ids() -> set[int]:
    ids: set[int] = set()
    for sel in (
        ROOT / "data" / "measurement_50" / "selection.json",
        ROOT / "data" / "measurement_nli_30" / "selection.json",
    ):
        if sel.is_file():
            ids.update(json.loads(sel.read_text(encoding="utf-8")).get("claim_ids") or [])
    conn = get_conn()
    try:
        for vid in SCOPE_TEST_VIDEOS:
            rows = conn.execute(
                "SELECT claim_id FROM claims WHERE video_id=?", (vid,)
            ).fetchall()
            ids.update(r[0] for r in rows)
    finally:
        conn.close()
    return ids


def _load_escalated_debug() -> dict[int, dict]:
    latest: dict[int, dict] = {}
    if not DEBUG_LOG.is_file():
        return latest
    with DEBUG_LOG.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            cid = rec.get("claim_id")
            raw = rec.get("raw") or {}
            if cid is not None and raw.get("final_verdict") is not None:
                latest[int(cid)] = rec
    return latest


def _flags(rec: dict) -> set[str]:
    raw = (rec.get("calibrated") or {}).get("calibration_flags") or rec.get("calibration_flags") or ""
    return {f.strip() for f in raw.split(",") if f.strip()}


def _web_search_used(rec: dict) -> bool:
    cite = rec.get("cite_source") or (rec.get("calibrated") or {}).get("cite_source")
    if cite in WEB_SEARCH_CITE:
        return True
    return bool(_flags(rec) & WEB_SEARCH_CITE)


def _block_field(block, key: str):
    val = getattr(block, key, None)
    if val is None and isinstance(block, dict):
        val = block.get(key)
    return val


def _usage_from_message(message) -> dict:
    usage = getattr(message, "usage", None)
    if usage is None and isinstance(message, dict):
        usage = message.get("usage")
    if usage is None:
        return {}
    if isinstance(usage, dict):
        return usage
    return {
        "input_tokens": getattr(usage, "input_tokens", 0) or 0,
        "output_tokens": getattr(usage, "output_tokens", 0) or 0,
        "cache_creation_input_tokens": getattr(usage, "cache_creation_input_tokens", 0) or 0,
        "cache_read_input_tokens": getattr(usage, "cache_read_input_tokens", 0) or 0,
    }


def _parse_jp5_log_tokens() -> dict[int, int]:
    if not JP5_LOG.is_file():
        return {}
    text = JP5_LOG.read_text(encoding="utf-8")
    out: dict[int, int] = {}
    lines = text.splitlines()
    pending_input: int | None = None
    for line in lines:
        m = re.search(r"input=(\d+)", line)
        if m:
            pending_input = int(m.group(1))
            continue
        m2 = re.search(r"\[(\d+)\]", line)
        if m2 and pending_input is not None and "claude" not in line.lower():
            out[int(m2.group(1))] = pending_input
            pending_input = None
    return out


def _load_batch_search_cache() -> dict[int, int]:
    if not BATCH_SEARCH_CACHE.is_file():
        return {}
    try:
        payload = json.loads(BATCH_SEARCH_CACHE.read_text(encoding="utf-8"))
        return {int(k): int(v) for k, v in (payload.get("web_search_calls_by_claim_id") or {}).items()}
    except (json.JSONDecodeError, TypeError, ValueError):
        return {}


def _save_batch_search_cache(per_claim: list[dict]) -> None:
    by_id = {
        r["claim_id"]: r["web_search_calls"]
        for r in per_claim
        if r.get("web_search_calls") is not None
    }
    if not by_id:
        return
    BATCH_SEARCH_CACHE.parent.mkdir(parents=True, exist_ok=True)
    BATCH_SEARCH_CACHE.write_text(
        json.dumps({"web_search_calls_by_claim_id": by_id}, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def analyze_batch_searches(skip_retrieve: bool) -> dict:
    pb = json.loads(PENDING_BATCHES.read_text(encoding="utf-8"))
    usage_by_id: dict[int, dict] = {}
    batch_claim_ids: set[int] = set()
    for batch in pb.get("batches") or []:
        batch_claim_ids.update(int(x) for x in batch.get("claim_ids") or [])
        for k, v in (batch.get("usage_by_custom_id") or {}).items():
            if str(k).isdigit():
                usage_by_id[int(k)] = v

    per_claim: list[dict] = []
    content_types_seen: Counter = Counter()

    if not skip_retrieve:
        for batch in pb.get("batches") or []:
            batch_id = batch.get("batch_id")
            if not batch_id:
                continue
            print(f"[phase0] batch re-retrieve: {batch_id}")
            try:
                for result in iter_batch_results(batch_id):
                    cid_raw = getattr(result, "custom_id", None)
                    if cid_raw is None and isinstance(result, dict):
                        cid_raw = result.get("custom_id")
                    if not str(cid_raw or "").isdigit():
                        continue
                    cid = int(cid_raw)
                    message = getattr(getattr(result, "result", None), "message", None)
                    if message is None:
                        inner = getattr(result, "result", None)
                        if isinstance(inner, dict):
                            message = (inner.get("message") or inner)
                    if message is None:
                        continue
                    n_search = count_web_search_calls(message)
                    usage = _usage_from_message(message)
                    content = getattr(message, "content", None)
                    if content is None and isinstance(message, dict):
                        content = message.get("content") or []
                    for block in content or []:
                        content_types_seen[_block_field(block, "type") or "?"] += 1
                    per_claim.append({
                        "claim_id": cid,
                        "web_search_calls": n_search,
                        "usage": usage,
                        "source": "batch_retrieve",
                    })
            except Exception as e:
                print(f"[phase0] batch retrieve hata ({batch_id}): {e}")

    cached_counts = _load_batch_search_cache() if skip_retrieve else {}

    # Fallback: usage from pending_batches if retrieve failed/partial
    retrieved_ids = {r["claim_id"] for r in per_claim}
    for cid in batch_claim_ids:
        if cid in retrieved_ids:
            continue
        u = usage_by_id.get(cid) or {}
        n_search = cached_counts.get(cid) if skip_retrieve else None
        per_claim.append({
            "claim_id": cid,
            "web_search_calls": n_search,
            "usage": u,
            "source": "cache" if n_search is not None else "usage_only",
        })

    if not skip_retrieve and any(r.get("web_search_calls") is not None for r in per_claim):
        _save_batch_search_cache(per_claim)

    counts = [r["web_search_calls"] for r in per_claim if r["web_search_calls"] is not None]
    hist = Counter()
    for n in counts:
        if n <= 0:
            hist["0"] += 1
        elif n == 1:
            hist["1"] += 1
        elif n == 2:
            hist["2"] += 1
        else:
            hist["3+"] += 1

    inputs = [
        int(r["usage"].get("input_tokens") or 0)
        for r in per_claim
        if r["usage"].get("input_tokens")
    ]
    return {
        "per_claim": per_claim,
        "histogram": dict(hist),
        "n_with_search_count": len(counts),
        "n_batch_claims": len(batch_claim_ids),
        "content_block_types": dict(content_types_seen),
        "input_tokens_median": statistics.median(inputs) if inputs else None,
        "input_tokens_p90": sorted(inputs)[int(len(inputs) * 0.9) - 1] if inputs else None,
    }


def analyze_sync_proxy(debug: dict[int, dict], jp5_tokens: dict[int, int]) -> dict:
    cohort = _cohort_claim_ids()
    batch_ids = set()
    pb = json.loads(PENDING_BATCHES.read_text(encoding="utf-8"))
    for batch in pb.get("batches") or []:
        batch_ids.update(int(x) for x in batch.get("claim_ids") or [])

    sync_rows = []
    for cid, rec in debug.items():
        if cid not in cohort or cid in batch_ids:
            continue
        sync_rows.append({
            "claim_id": cid,
            "web_search_proxy": _web_search_used(rec),
            "input_tokens": jp5_tokens.get(cid),
            "cite_source": rec.get("cite_source"),
        })

    proxy_rate = (
        sum(1 for r in sync_rows if r["web_search_proxy"]) / len(sync_rows)
        if sync_rows else 0.0
    )
    expensive = [
        {
            "claim_id": cid,
            "input_tokens": jp5_tokens.get(cid),
            "web_search_proxy": _web_search_used(debug[cid]) if cid in debug else None,
            "cite_source": (debug.get(cid) or {}).get("cite_source"),
        }
        for cid in EXPENSIVE_IDS
    ]
    return {
        "n_sync": len(sync_rows),
        "web_search_proxy_rate": proxy_rate,
        "expensive_six": expensive,
        "sync_rows": sync_rows,
    }


def analyze_no_direct(
    debug: dict[int, dict],
    batch_search: dict[int, int],
    usage_by_id: dict[int, dict],
) -> dict:
    no_direct: list[dict] = []
    other: list[dict] = []

    for cid, rec in debug.items():
        row = {
            "claim_id": cid,
            "web_search_proxy": _web_search_used(rec),
            "final_verdict": (rec.get("calibrated") or rec.get("raw") or {}).get("final_verdict"),
            "retrieval_path": rec.get("retrieval_path") or "",
            "force_package_only": rec.get("force_package_only"),
            "web_search_calls": batch_search.get(cid),
            "input_tokens": int((rec.get("usage") or usage_by_id.get(cid) or {}).get("input_tokens") or 0) or None,
        }
        if EPISTEMIC_NO_DIRECT in _flags(rec):
            no_direct.append(row)
        else:
            other.append(row)

    def _rate(rows, key):
        if not rows:
            return 0.0, 0, 0
        n = sum(1 for r in rows if r.get(key))
        return n / len(rows), n, len(rows)

    nd_rate, nd_n, nd_total = _rate(no_direct, "web_search_proxy")
    ot_rate, ot_n, ot_total = _rate(other, "web_search_proxy")

    nd_inputs = [r["input_tokens"] for r in no_direct if r["input_tokens"]]
    ot_inputs = [r["input_tokens"] for r in other if r["input_tokens"]]

    verdicts = Counter(r["final_verdict"] for r in no_direct)
    serper_nd = sum(1 for r in no_direct if "serper" in (r["retrieval_path"] or "").lower())
    serper_ot = sum(1 for r in other if "serper" in (r["retrieval_path"] or "").lower())

    nd_searches = [r["web_search_calls"] for r in no_direct if r["web_search_calls"] is not None]
    ot_searches = [r["web_search_calls"] for r in other if r["web_search_calls"] is not None]

    return {
        "no_direct_n": nd_total,
        "other_n": ot_total,
        "no_direct_web_search_rate": nd_rate,
        "other_web_search_rate": ot_rate,
        "no_direct_verdicts": dict(verdicts),
        "no_direct_dogrulanmis_rate": verdicts.get("doğrulanmış", 0) / nd_total if nd_total else 0,
        "no_direct_input_median": statistics.median(nd_inputs) if nd_inputs else None,
        "other_input_median": statistics.median(ot_inputs) if ot_inputs else None,
        "no_direct_serper_n": serper_nd,
        "other_serper_n": serper_ot,
        "no_direct_batch_search_median": statistics.median(nd_searches) if nd_searches else None,
        "other_batch_search_median": statistics.median(ot_searches) if ot_searches else None,
        "rows_no_direct": no_direct,
    }


def _package_chars(evidence: list[dict]) -> int:
    total = 0
    for e in evidence[:5]:
        total += len((e.get("abstract") or e.get("title") or ""))
    return total


def simulate_snippet_packages(shadow) -> dict:
    latest = shadow._latest_debug_records(list(REFERENCE_IDS))
    queries = shadow._search_queries(list(REFERENCE_IDS))
    results = []
    char_before = []
    char_after = []

    for cid in REFERENCE_IDS:
        rec = latest.get(cid)
        if not rec:
            continue
        urls = list(rec.get("package_urls") or [])
        candidates = shadow._hydrate_package(urls)
        sq = queries.get(cid, "")
        before = assess_evidence_sufficiency(candidates, rec.get("claim_text") or "", sq or None)

        shrunk = []
        for c in candidates:
            nc = dict(c)
            full_abs = (c.get("abstract") or "").strip()
            nc["abstract"] = best_evidence_snippet(rec.get("claim_text") or "", full_abs)
            shrunk.append(nc)
        after = assess_evidence_sufficiency(shrunk, rec.get("claim_text") or "", sq or None)

        char_before.append(_package_chars(candidates))
        char_after.append(_package_chars(shrunk))

        kept, _ = filter_candidates_by_key_terms(candidates, sq, rec.get("claim_text"))
        top_b = _top_candidate(kept) if kept else None
        nli_b = _specificity_nli_result(rec.get("claim_text") or "", top_b) if top_b else None

        kept_a, _ = filter_candidates_by_key_terms(shrunk, sq, rec.get("claim_text"))
        top_a = _top_candidate(kept_a) if kept_a else None
        nli_a = _specificity_nli_result(rec.get("claim_text") or "", top_a) if top_a else None

        expected = EXPECTED_TIERS.get(cid)
        tier_changed = before.specificity_tier != after.specificity_tier
        results.append({
            "claim_id": cid,
            "expected_tier": expected,
            "before_tier": before.specificity_tier,
            "after_tier": after.specificity_tier,
            "before_strong_match": before.strong_match,
            "after_strong_match": after.strong_match,
            "tier_changed": tier_changed,
            "tier_ok": (after.specificity_tier == expected),
            "nli_before": nli_b,
            "nli_after": nli_a,
            "chars_before": _package_chars(candidates),
            "chars_after": _package_chars(shrunk),
        })
        print(
            f"[phase0] snippet sim {cid}: {before.specificity_tier} → {after.specificity_tier} "
            f"(expected {expected})"
        )

    # Geniş kohort paket dağılımı
    pb = json.loads(PENDING_BATCHES.read_text(encoding="utf-8"))
    pkg_counts = []
    pkg_chars = []
    for batch in pb.get("batches") or []:
        for job in batch.get("jobs") or []:
            ev = job.get("evidence") or []
            pkg_counts.append(len(ev))
            pkg_chars.append(_package_chars(ev))

    changed_critical = [
        r for r in results
        if r["claim_id"] in (752, 1284)
        and r["tier_changed"]
        and not r["tier_ok"]
    ]
    any_change = [r for r in results if r["tier_changed"]]

    return {
        "reference_results": results,
        "n_tier_changed": len(any_change),
        "critical_broken": changed_critical,
        "package_count_dist": dict(Counter(pkg_counts)),
        "package_chars_median": statistics.median(pkg_chars) if pkg_chars else None,
        "package_chars_p90": sorted(pkg_chars)[int(len(pkg_chars) * 0.9) - 1] if pkg_chars else None,
        "snippet_chars_median": statistics.median(char_after) if char_after else None,
    }


def _decide_a(batch_a: dict, sync_a: dict) -> tuple[str, str]:
    hist = batch_a.get("histogram") or {}
    n = int(batch_a.get("n_with_search_count") or 0)
    hist_total = sum(int(v) for v in hist.values())
    if hist_total and hist_total != n:
        n = hist_total
    one = int(hist.get("1", 0))
    three_plus = int(hist.get("3+", 0))
    pct_one = one / n if n else 0.0
    pct_three = three_plus / n if n else 0.0
    hist_line = f"Batch histogram: 0={hist.get('0',0)} 1={one} 2={hist.get('2',0)} 3+={three_plus} (n={n})"

    expensive_high = sum(
        1 for r in sync_a.get("expensive_six") or []
        if (r.get("input_tokens") or 0) >= 70000
    )

    if n == 0:
        if expensive_high >= 3:
            return (
                "YAP (sync outlier odaklı)",
                f"{hist_line}; batch sayım yok — sync outlier {expensive_high}/6 ≥70K token.",
            )
        return ("YAPMA", f"{hist_line}; batch arama sayımı yok.")

    if pct_one >= 0.80 and pct_three < 0.05:
        return (
            "YAPMA (batch)",
            f"{hist_line}; tek arama {pct_one:.0%}, 3+ {pct_three:.0%} ({three_plus}/{n}) — bütçe=1 batch'te düşük etki.",
        )
    if pct_three >= 0.15 or expensive_high >= 3:
        return (
            "YAP (sync outlier odaklı)",
            f"{hist_line}; 3+ arama {pct_three:.0%} ({three_plus}/{n}); sync outlier {expensive_high}/6 ≥70K token — "
            "max_search_calls=1 outlier maliyetini keser.",
        )
    return (
        "YAPMA (genel)",
        f"{hist_line}; tek arama {pct_one:.0%}, 3+ {pct_three:.0%} ({three_plus}/{n}); genel kohortta sınırlı kazanç.",
    )


def _decide_b(b: dict) -> tuple[str, str]:
    nd_rate = b.get("no_direct_web_search_rate") or 0
    dog_rate = b.get("no_direct_dogrulanmis_rate") or 0
    if nd_rate >= 0.70 and dog_rate <= 0.20:
        return "YAP", f"Override {nd_rate:.0%}, doğrulanmış {dog_rate:.0%} — durdurma tasarruf sağlar."
    if nd_rate >= 0.70 and dog_rate >= 0.30:
        return (
            "YAPMA",
            f"Override {nd_rate:.0%} yüksek ama doğrulanmış {dog_rate:.0%} — web_search değer üretiyor, kör durdurma riskli.",
        )
    return "YAPMA", f"Override {nd_rate:.0%} — eşik altında veya belirsiz."


def _decide_c(c: dict) -> tuple[str, str]:
    critical = c.get("critical_broken") or []
    if critical:
        ids = [r["claim_id"] for r in critical]
        return "YAPMA", f"Referans iddialar {ids} tier bozuldu — RİSKLİ."
    if c.get("n_tier_changed", 0) == 0:
        return "YAP", "6/6 referans iddiada tier değişmedi — güvenli."
    changed = c.get("reference_results") or []
    ids = [r["claim_id"] for r in changed if r.get("tier_changed")]
    return "YAPMA", f"Tier değişen iddialar: {ids} — sınırda kayma riski."


def render_report(payload: dict) -> str:
    a = payload["analysis_a"]
    b = payload["analysis_b"]
    c = payload["analysis_c"]
    da, ja = payload["decision_a"]
    db, jb = payload["decision_b"]
    dc, jc = payload["decision_c"]
    today = date.today().isoformat()

    hist = a["batch"]["histogram"]
    lines = [
        f"# Maliyet Faz 0 raporu — {today}",
        "",
        "**Kapsam:** odZg + bZsor + jP5 + measurement_50 + measurement_nli_30",
        "",
        "---",
        "",
        "## A — Arama sayısı dağılımı",
        "",
        f"Batch re-retrieve (n={a['batch']['n_with_search_count']} kesin sayım):",
        "",
        "| Arama sayısı | İddia |",
        "|---|---:|",
    ]
    for k in ("0", "1", "2", "3+"):
        if k in hist:
            lines.append(f"| {k} | {hist[k]} |")
    lines.extend([
        "",
        f"- Batch input_tokens medyan: **{a['batch'].get('input_tokens_median')}**",
        f"- Content block türleri (örnek): `{a['batch'].get('content_block_types')}`",
        "",
        "**Sync proxy** (cite_source, n={}):".format(a["sync"]["n_sync"]),
        f"- web_search_override/only oranı: **{a['sync']['web_search_proxy_rate']:.1%}**",
        "",
        "**6 pahalı jP5 iddiası:**",
        "",
        "| claim_id | input_tokens | cite_source | web_search_proxy |",
        "|---|---:|---|---|",
    ])
    for row in a["sync"]["expensive_six"]:
        lines.append(
            f"| {row['claim_id']} | {row.get('input_tokens') or '—'} | "
            f"{row.get('cite_source') or '—'} | {row.get('web_search_proxy')} |"
        )
    lines.extend([
        "",
        f"**Karar A: {da}**",
        "",
        ja,
        "",
        "---",
        "",
        "## B — no_direct_evidence_expected davranışı",
        "",
        f"| Metrik | no_direct (n={b['no_direct_n']}) | diğer (n={b['other_n']}) |",
        "|---|---:|---:|",
        f"| web_search proxy | {b['no_direct_web_search_rate']:.1%} | {b['other_web_search_rate']:.1%} |",
        f"| input_tokens medyan | {b.get('no_direct_input_median') or '—'} | {b.get('other_input_median') or '—'} |",
        f"| batch arama medyan | {b.get('no_direct_batch_search_median') or '—'} | {b.get('other_batch_search_median') or '—'} |",
        "",
        f"**Verdict dağılımı (no_direct):** `{b.get('no_direct_verdicts')}`",
        "",
        f"**Karar B: {db}**",
        "",
        jb,
        "",
        "---",
        "",
        "## C — Kanıt paketi snippet simülasyonu",
        "",
        f"- Paket evidence_count: `{c.get('package_count_dist')}`",
        f"- Tam abstract chars medyan/p90: **{c.get('package_chars_median')}** / **{c.get('package_chars_p90')}**",
        f"- Snippet sonrası chars medyan: **{c.get('snippet_chars_median')}**",
        "",
        "| claim_id | expected | before | after | changed |",
        "|---|---|---|---|---|",
    ])
    for r in c.get("reference_results") or []:
        lines.append(
            f"| {r['claim_id']} | {r['expected_tier']} | {r['before_tier']} | "
            f"{r['after_tier']} | {'evet' if r['tier_changed'] else 'hayır'} |"
        )
    lines.extend([
        "",
        f"**Karar C: {dc}**",
        "",
        jc,
        "",
        "---",
        "",
        "## Özet — Faz 1 adayları",
        "",
        "| Madde | Karar |",
        "|---|---|",
        f"| Arama bütçesi | {da} |",
        f"| Epistemik durdurma | {db} |",
        f"| Paket küçültme | {dc} |",
        "",
    ])
    faz1 = []
    if da.startswith("YAP"):
        faz1.append("max_search_calls")
    if db == "YAP":
        faz1.append("no_direct early-exit")
    if dc == "YAP":
        faz1.append("snippet evidence package")
    if faz1:
        lines.append("**Faz 1'de uygulanacak:** " + ", ".join(faz1))
    else:
        lines.append("**Faz 1'de uygulanacak:** hiçbiri (Faz 0 sayıları yeterli gerekçe vermedi)")
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--out",
        default=str(ROOT / "data" / "ops_reports" / "2026-08-18-cost-phase0.md"),
    )
    parser.add_argument(
        "--skip-batch-retrieve",
        action="store_true",
        help="Anthropic batch re-retrieve atla (yalnızca usage proxy)",
    )
    parser.add_argument("--skip-snippet", action="store_true")
    args = parser.parse_args()

    debug = _load_escalated_debug()
    jp5_tokens = _parse_jp5_log_tokens()

    print("[phase0] A — batch arama sayımı …")
    batch_a = analyze_batch_searches(skip_retrieve=args.skip_batch_retrieve)
    sync_a = analyze_sync_proxy(debug, jp5_tokens)

    batch_search_map = {
        r["claim_id"]: r["web_search_calls"]
        for r in batch_a["per_claim"]
        if r["web_search_calls"] is not None
    }
    pb = json.loads(PENDING_BATCHES.read_text(encoding="utf-8"))
    usage_by_id: dict[int, dict] = {}
    for batch in pb.get("batches") or []:
        for k, v in (batch.get("usage_by_custom_id") or {}).items():
            if str(k).isdigit():
                usage_by_id[int(k)] = v

    print("[phase0] B — no_direct_evidence_expected …")
    analysis_b = analyze_no_direct(debug, batch_search_map, usage_by_id)

    analysis_c = {"reference_results": [], "n_tier_changed": 0, "critical_broken": []}
    if not args.skip_snippet:
        print("[phase0] C — snippet simülasyonu …")
        shadow = _load_shadow12()
        analysis_c = simulate_snippet_packages(shadow)

    decision_a = _decide_a(batch_a, sync_a)
    decision_b = _decide_b(analysis_b)
    decision_c = _decide_c(analysis_c)

    payload = {
        "analysis_a": {"batch": batch_a, "sync": sync_a},
        "analysis_b": analysis_b,
        "analysis_c": analysis_c,
        "decision_a": decision_a[0],
        "decision_a_reason": decision_a[1],
        "decision_b": decision_b[0],
        "decision_b_reason": decision_b[1],
        "decision_c": decision_c[0],
        "decision_c_reason": decision_c[1],
        "decision_a_tuple": decision_a,
        "decision_b_tuple": decision_b,
        "decision_c_tuple": decision_c,
    }

    report = render_report({
        "analysis_a": {"batch": batch_a, "sync": sync_a},
        "analysis_b": analysis_b,
        "analysis_c": analysis_c,
        "decision_a": decision_a,
        "decision_b": decision_b,
        "decision_c": decision_c,
    })

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(report, encoding="utf-8")
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[phase0] rapor: {out_path}")
    print(f"[phase0] json:  {OUT_JSON}")
    print(f"[phase0] A={decision_a[0]}  B={decision_b[0]}  C={decision_c[0]}")


if __name__ == "__main__":
    main()
