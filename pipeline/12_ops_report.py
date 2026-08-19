"""
Üretim izleme özeti — mevcut DB / debug / batch / dedup artifact'lerinden okur.

Yeni ölçüm sistemi kurmaz; davranış veya eşik değiştirmez.

Kullanım:
    ./venv/bin/python pipeline/12_ops_report.py
    ./venv/bin/python pipeline/12_ops_report.py --scope test
    ./venv/bin/python pipeline/12_ops_report.py --video-ids odZgEDFDmbE,jP5XF06OLbo
    ./venv/bin/python pipeline/12_ops_report.py --claim-ids 357,810,901
    ./venv/bin/python pipeline/12_ops_report.py --since 2026-08-01 --until 2026-08-18
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from datetime import date, datetime
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent))

from utils.db import get_conn
from utils.factcheck_review import compute_needs_human, security_risk_triggers
from utils.dedup_status import has_full_dedup_pipeline, offline_dedup_path
from utils.ops_report_parse import metrics_from_report_file, parse_cost_spread, parse_report_metric_value

ROOT = Path(__file__).parent.parent
DEBUG_LOG = ROOT / "data" / "factcheck_debug.jsonl"
PENDING_BATCHES = ROOT / "data" / "pending_batches.json"
CHUNK_DIR = ROOT / "data" / "extraction_chunks"
OUT_DIR = ROOT / "data" / "ops_reports"

WEB_SEARCH_CITE = frozenset({"web_search_override", "web_search_only"})
SOURCE_TIER_ORDER = (
    "guideline",
    "primary_study",
    "systematic_review",
    "other",
    "static_reference",
    "nutrition_db",
    "usda_cache_static",
    "encyclopedia",
    "(boş)",
)
SPECIFICITY_ORDER = ("direct", "supportive", "background", "(yok)")

# Sonnet 5 — taban (Anthropic, Ağu 2026): $2/M in + $10/M out
# Batch %50; cache write 1.25× taban girdi (batch'te %50); cache read 0.10× taban girdi
PRICE_SYNC_IN = 2.0 / 1_000_000
PRICE_SYNC_OUT = 10.0 / 1_000_000
PRICE_BATCH_IN = PRICE_SYNC_IN * 0.5
PRICE_BATCH_OUT = PRICE_SYNC_OUT * 0.5
PRICE_CACHE_WRITE = 2.5 / 1_000_000   # 1.25 × $2/M
PRICE_CACHE_READ = 0.20 / 1_000_000   # 0.10 × $2/M

SCOPE_TEST_VIDEOS = ("odZgEDFDmbE", "bZsorXWeLhM", "jP5XF06OLbo")
SCOPE_TEST_SELECTIONS = (
    ROOT / "data" / "measurement_50" / "selection.json",
    ROOT / "data" / "measurement_nli_30" / "selection.json",
)


def _parse_ids(raw: str | None) -> list[int]:
    if not raw:
        return []
    return [int(x.strip()) for x in raw.split(",") if x.strip()]


def _load_selection_claim_ids(path: Path) -> list[int]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return [int(x) for x in data.get("claim_ids") or []]


def _resolve_scope(args) -> tuple[list[str], list[int], str]:
    """(video_ids, claim_ids, scope_label)"""
    videos = [v.strip() for v in (args.video_ids or "").split(",") if v.strip()]
    claim_ids = _parse_ids(args.claim_ids)

    if args.scope == "test":
        videos = list(SCOPE_TEST_VIDEOS)
        for sel in SCOPE_TEST_SELECTIONS:
            if sel.is_file():
                claim_ids.extend(_load_selection_claim_ids(sel))
        claim_ids = sorted(set(claim_ids))
        label = "test (measurement_50 + measurement_nli_30 + odZg/bZsor/jP5)"
    else:
        label = args.scope or "custom"
        if not videos and not claim_ids:
            label = "all_verdicted"

    return videos, claim_ids, label


def _latest_debug_by_claim() -> dict[int, dict]:
    out: dict[int, dict] = {}
    if not DEBUG_LOG.is_file():
        return out
    with DEBUG_LOG.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            cid = rec.get("claim_id")
            if cid is not None:
                out[int(cid)] = rec
    return out


def _batch_usage_by_claim() -> dict[int, dict]:
    """pending_batches.json — custom_id başına son usage (batch retrieve)."""
    out: dict[int, dict] = {}
    if not PENDING_BATCHES.is_file():
        return out
    data = json.loads(PENDING_BATCHES.read_text(encoding="utf-8"))
    for batch in data.get("batches") or []:
        usage_map = batch.get("usage_by_custom_id") or {}
        for cid_str, usage in usage_map.items():
            try:
                out[int(cid_str)] = dict(usage)
            except (TypeError, ValueError):
                continue
    return out


def _cite_source(rec: dict | None, flags: str | None) -> str | None:
    if rec:
        cite = rec.get("cite_source")
        if cite:
            return cite
        cite = (rec.get("calibrated") or {}).get("cite_source")
        if cite:
            return cite
    flag_set = {f.strip() for f in (flags or "").split(",") if f.strip()}
    for key in ("web_search_override", "web_search_only", "retrieval_cited"):
        if key in flag_set:
            return key
    return None


def _specificity_tier(rec: dict | None, flags: str | None) -> str | None:
    if rec and rec.get("specificity_tier"):
        return rec["specificity_tier"]
    for part in (flags or "").split(","):
        part = part.strip()
        if part.startswith("specificity_tier:"):
            return part.split(":", 1)[1]
    return None


def _estimate_cost_usd(usage: dict | None, *, batch: bool) -> float | None:
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
            + cw * PRICE_CACHE_WRITE * 0.5
            + cr * PRICE_CACHE_READ
        )
    return inp * PRICE_SYNC_IN + out * PRICE_SYNC_OUT


def _percentile(xs: list[float], p: float) -> float | None:
    """Doğrusal interpolasyon; p in [0, 1]."""
    if not xs:
        return None
    s = sorted(xs)
    if len(s) == 1:
        return float(s[0])
    k = (len(s) - 1) * p
    lo = int(k)
    hi = min(lo + 1, len(s) - 1)
    if lo == hi:
        return float(s[lo])
    frac = k - lo
    return float(s[lo] + (s[hi] - s[lo]) * frac)


def _embedding_clustering_status() -> str:
    sidecar = OUT_DIR / "embedding_clustering_status.txt"
    if sidecar.is_file():
        text = sidecar.read_text(encoding="utf-8").strip()
        if text:
            return text
    try:
        import sentence_transformers  # noqa: F401
    except ImportError as exc:
        return f"failed: {exc}"
    return "ok (probe: sentence-transformers import; sidecar yok — 06_claim_index çalıştırın)"


def _dedup_stats(video_id: str, db_claim_count: int) -> dict:
    """chunk-içi + global pencere birleşen / ham toplam."""
    full_pipeline = has_full_dedup_pipeline(video_id)
    offline = offline_dedup_path(video_id)
    if offline:
        data = json.loads(offline.read_text(encoding="utf-8"))
        raw_total = int(data.get("raw_count") or 0)
        local_total = int(data.get("local_dedup_total") or raw_total)
        pipeline = int(data.get("pipeline_count") or data.get("db_after_count") or db_claim_count)
        chunk_local = 0
        for ch in data.get("chunks") or []:
            chunk_local += max(0, int(ch.get("raw_count") or 0) - int(ch.get("local_dedup_count") or 0))
        global_merged = max(0, local_total - pipeline)
        merged = chunk_local + global_merged
        return {
            "full_pipeline": True,
            "raw_total": raw_total,
            "merged": merged,
            "ratio": (merged / raw_total) if raw_total else None,
            "source": str(offline.relative_to(ROOT)),
        }

    chunk_path = CHUNK_DIR / f"{video_id}.json"
    if full_pipeline and chunk_path.is_file():
        data = json.loads(chunk_path.read_text(encoding="utf-8"))
        raw_total = 0
        local_total = 0
        chunk_local = 0
        for ch in data.get("chunks") or []:
            raw = int(ch.get("raw_count") or 0)
            loc = int(ch.get("local_dedup_count") or raw)
            raw_total += raw
            local_total += loc
            chunk_local += max(0, raw - loc)
        global_merged = max(0, local_total - db_claim_count)
        merged = chunk_local + global_merged
        return {
            "full_pipeline": True,
            "raw_total": raw_total,
            "merged": merged,
            "ratio": (merged / raw_total) if raw_total else None,
            "source": str(chunk_path.relative_to(ROOT)),
        }

    return {
        "full_pipeline": False,
        "raw_total": 0,
        "merged": 0,
        "ratio": None,
        "source": "partial_sample",
    }


def _fetch_claim_rows(
    conn,
    *,
    video_ids: list[str],
    claim_ids: list[int],
    since: str | None,
    until: str | None,
) -> list[dict]:
    clauses = ["c.archived_at IS NULL"]
    params: list = []

    id_filters = []
    if video_ids:
        ph = ",".join("?" * len(video_ids))
        id_filters.append(f"c.video_id IN ({ph})")
        params.extend(video_ids)
    if claim_ids:
        ph = ",".join("?" * len(claim_ids))
        id_filters.append(f"c.claim_id IN ({ph})")
        params.extend(claim_ids)

    if id_filters:
        clauses.append("(" + " OR ".join(id_filters) + ")")
    else:
        clauses.append("vr.claim_id IS NOT NULL")

    if since:
        clauses.append("date(COALESCE(vr.verified_at, c.extracted_at)) >= date(?)")
        params.append(since)
    if until:
        clauses.append("date(COALESCE(vr.verified_at, c.extracted_at)) <= date(?)")
        params.append(until)

    sql = f"""
        SELECT
            c.claim_id, c.video_id, c.claim_text, c.category, c.initial_risk,
            c.extracted_at,
            vr.escalated, vr.final_verdict, vr.confidence, vr.source_directness,
            vr.evidence_stance, vr.source_tier, vr.calibration_flags,
            vr.human_reviewed, vr.auto_accepted, vr.library_match,
            vr.would_auto_accept_v1, vr.would_auto_accept_reason,
            vr.would_require_human_verdict_gate, vr.would_require_human_confidence_gate,
            vr.would_require_human_compound_gate, vr.would_auto_accept_after_all_gates,
            vr.verified_at, vr.nli_label, vr.nli_confidence, vr.reasoning
        FROM claims c
        LEFT JOIN verdicts vr ON vr.claim_id = c.claim_id
        WHERE {" AND ".join(clauses)}
        ORDER BY c.video_id, c.claim_id
    """
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def _compute_metrics(rows: list[dict], debug: dict[int, dict], batch_usage: dict[int, dict]) -> dict:
    verdicted = [r for r in rows if r.get("verified_at")]
    escalated_rows = [r for r in verdicted if int(r.get("escalated") or 0) == 1]
    n_verdict = len(verdicted)
    n_esc = len(escalated_rows)

    claims_by_video: Counter[str] = Counter()
    for r in rows:
        claims_by_video[r["video_id"]] += 1

    chunk_local = global_merged = raw_total = 0
    dedup_video_n = 0
    for vid, cnt in claims_by_video.items():
        if not has_full_dedup_pipeline(vid):
            continue
        dedup_video_n += 1
        d = _dedup_stats(vid, cnt)
        raw_total += d["raw_total"]
        offline = offline_dedup_path(vid)
        if offline:
            data = json.loads(offline.read_text(encoding="utf-8"))
            lt = int(data.get("local_dedup_total") or data.get("raw_count") or 0)
            pl = int(data.get("pipeline_count") or data.get("db_after_count") or cnt)
            cl = sum(
                max(0, int(ch.get("raw_count") or 0) - int(ch.get("local_dedup_count") or 0))
                for ch in (data.get("chunks") or [])
            )
            gl = max(0, lt - pl)
        else:
            chunk_path = CHUNK_DIR / f"{vid}.json"
            data = json.loads(chunk_path.read_text(encoding="utf-8"))
            loc = cl = 0
            for ch in data.get("chunks") or []:
                raw = int(ch.get("raw_count") or 0)
                loc += int(ch.get("local_dedup_count") or raw)
                cl += max(0, raw - int(ch.get("local_dedup_count") or raw))
            gl = max(0, loc - cnt)
        chunk_local += cl
        global_merged += gl

    dedup_merged = chunk_local + global_merged
    dedup_ratio = dedup_merged / raw_total if raw_total else None

    web_search_n = retrieval_cited_n = 0
    parse_fail_n = parse_retry_n = parse_retry_ok = 0
    specificity = Counter()
    needs_human_n = 0
    esc0_n = 0
    auto_accept = Counter()
    source_tier = Counter()
    costs: list[float] = []
    cost_sources = Counter()
    cache_retrieval_n = cache_hit_n = 0
    search_counts: list[float] = []
    relevance_scores: list[float] = []
    relevance_basis = Counter()
    retrieval_failed_n = 0
    compound_mismatch_n = 0
    shadow_accept_n = 0
    shadow_verdict_n = 0
    shadow_conf_n = 0
    shadow_compound_n = 0

    for r in rows:
        cid = int(r["claim_id"])
        rec = debug.get(cid)
        if rec and rec.get("retrieval_failed"):
            retrieval_failed_n += 1

    for r in verdicted:
        cid = int(r["claim_id"])
        rec = debug.get(cid)
        flags = r.get("calibration_flags") or ""
        cite = _cite_source(rec, flags)
        esc = int(r.get("escalated") or 0)

        if esc == 1:
            if cite in WEB_SEARCH_CITE:
                web_search_n += 1
            if cite == "retrieval_cited" or "retrieval_cited" in flags:
                retrieval_cited_n += 1

        if esc == 0:
            esc0_n += 1

        pf = bool(rec.get("parse_failed")) if rec else False
        if not pf and r.get("final_verdict") is None:
            reasoning = r.get("reasoning") or ""
            if "parse edilemedi" in reasoning.lower():
                pf = True
        if pf:
            parse_fail_n += 1

        if rec and rec.get("parse_retry"):
            parse_retry_n += 1
            if rec.get("parse_retry_succeeded"):
                parse_retry_ok += 1

        tier = _specificity_tier(rec, flags)
        specificity[tier or "(yok)"] += 1

        library_hit = None
        if r.get("library_match"):
            try:
                library_hit = json.loads(r["library_match"]) if isinstance(r["library_match"], str) else r["library_match"]
            except json.JSONDecodeError:
                library_hit = {"raw": r["library_match"]}

        parse_failed = bool(rec.get("parse_failed")) if rec else pf
        nh = compute_needs_human(
            category=r.get("category"),
            initial_risk=r.get("initial_risk"),
            claim_text=r.get("claim_text") or "",
            parse_failed=parse_failed,
            final_verdict=r.get("final_verdict"),
            escalated_flag=esc,
            calibrated={
                # Pipeline'da calibrate_factcheck sonucu auto_accepted'a yansır;
                # escalated=1 dalında needs_human bayrağını geri yükle.
                "needs_human": esc == 1 and int(r.get("auto_accepted") or 0) == 0,
            },
            source_directness=r.get("source_directness"),
            library_review_hit=library_hit if isinstance(library_hit, dict) else None,
            calibration_flags=flags,
        )
        if nh:
            needs_human_n += 1

        prov = (rec or {}).get("evidence_provenance") or {}
        if prov.get("topic_key"):
            cache_retrieval_n += 1
            if int(prov.get("cache_in_final") or 0) > 0:
                cache_hit_n += 1

        st = (r.get("source_tier") or "").strip() or "(boş)"
        source_tier[st] += 1

        wa = int(r.get("would_auto_accept_v1") or 0)
        auto_accept["true" if wa else "false"] += 1
        if int(r.get("would_auto_accept_after_all_gates") or 0):
            shadow_accept_n += 1
        if int(r.get("would_require_human_verdict_gate") or 0):
            shadow_verdict_n += 1
        if int(r.get("would_require_human_confidence_gate") or 0):
            shadow_conf_n += 1
        if int(r.get("would_require_human_compound_gate") or 0):
            shadow_compound_n += 1
        flag_set = {f.strip() for f in flags.split(",") if f.strip()}
        if "compound_tier_mismatch" in flag_set:
            compound_mismatch_n += 1

        if rec and rec.get("web_search_call_count") is not None:
            try:
                search_counts.append(float(rec["web_search_call_count"]))
            except (TypeError, ValueError):
                pass

        if esc == 1 and rec and rec.get("relevance_score") is not None:
            try:
                relevance_scores.append(float(rec["relevance_score"]))
                basis = rec.get("relevance_basis") or "(yok)"
                relevance_basis[basis] += 1
            except (TypeError, ValueError):
                pass

        usage = batch_usage.get(cid) or (rec or {}).get("usage")
        is_batch = cid in batch_usage or bool(
            usage and (int(usage.get("cache_creation_input_tokens") or 0)
                       or int(usage.get("cache_read_input_tokens") or 0))
        )
        cost = _estimate_cost_usd(usage, batch=is_batch)
        if cost is not None:
            costs.append(cost)
            cost_sources["batch" if is_batch else "sync"] += 1

    avg_claims_per_video = (
        sum(claims_by_video.values()) / len(claims_by_video) if claims_by_video else 0
    )

    return {
        "n_claims": len(rows),
        "n_verdicts": n_verdict,
        "n_videos": len(claims_by_video),
        "avg_claims_per_video": avg_claims_per_video,
        "claims_by_video": dict(claims_by_video),
        "dedup_merged": dedup_merged,
        "dedup_raw": raw_total,
        "dedup_chunk_local": chunk_local,
        "dedup_global": global_merged,
        "dedup_ratio": dedup_ratio,
        "dedup_video_n": dedup_video_n,
        "escalation_rate": n_esc / n_verdict if n_verdict else None,
        "n_escalated": n_esc,
        "web_search_rate": web_search_n / n_esc if n_esc else None,
        "n_web_search": web_search_n,
        "retrieval_cited_rate": retrieval_cited_n / n_esc if n_esc else None,
        "n_retrieval_cited": retrieval_cited_n,
        "cache_hit_rate": cache_hit_n / cache_retrieval_n if cache_retrieval_n else None,
        "n_cache_hit": cache_hit_n,
        "n_topic_retrieval": cache_retrieval_n,
        "specificity_tier": dict(specificity),
        "parse_fail_n": parse_fail_n,
        "parse_retry_n": parse_retry_n,
        "parse_retry_ok": parse_retry_ok,
        "parse_retry_rate": parse_retry_ok / parse_retry_n if parse_retry_n else None,
        "needs_human_rate": needs_human_n / n_verdict if n_verdict else None,
        "n_needs_human": needs_human_n,
        "avg_cost_usd": sum(costs) / len(costs) if costs else None,
        "n_cost_samples": len(costs),
        "cost_sources": dict(cost_sources),
        "cost_p50": _percentile(costs, 0.50),
        "cost_p90": _percentile(costs, 0.90),
        "cost_p95": _percentile(costs, 0.95),
        "cost_max": max(costs) if costs else None,
        "search_p50": _percentile(search_counts, 0.50),
        "search_p95": _percentile(search_counts, 0.95),
        "search_max": max(search_counts) if search_counts else None,
        "n_search_samples": len(search_counts),
        "relevance_p25": _percentile(relevance_scores, 0.25),
        "relevance_p50": _percentile(relevance_scores, 0.50),
        "relevance_p75": _percentile(relevance_scores, 0.75),
        "n_relevance_scores": len(relevance_scores),
        "relevance_basis": dict(relevance_basis),
        "retrieval_failed_n": retrieval_failed_n,
        "compound_tier_mismatch_n": compound_mismatch_n,
        "would_auto_accept_after_all_gates_n": shadow_accept_n,
        "would_require_human_verdict_gate_n": shadow_verdict_n,
        "would_require_human_confidence_gate_n": shadow_conf_n,
        "would_require_human_compound_gate_n": shadow_compound_n,
        "embedding_clustering_status": _embedding_clustering_status(),
        "escalated_0_n": esc0_n,
        "would_auto_accept_v1": dict(auto_accept),
        "source_tier": dict(source_tier),
    }


def _pct(x: float | None, digits: int = 1) -> str:
    if x is None:
        return "—"
    return f"{x * 100:.{digits}f}%"


def _num(x: float | None, digits: int = 2) -> str:
    if x is None:
        return "—"
    return f"{x:.{digits}f}"


def _fmt_delta(cur, prev, *, is_rate: bool = False, is_money: bool = False) -> str:
    if prev is None:
        return "baseline"
    if cur is None:
        return "—"
    d = cur - prev
    if is_rate:
        return f"{d * 100:+.1f} pp"
    if is_money:
        return f"${d:+.4f}"
    if isinstance(cur, float) and isinstance(prev, float):
        return f"{d:+.2f}"
    return f"{d:+d}" if isinstance(d, int) else f"{d:+.2f}"


def _metrics_from_report_file(path: Path) -> dict[str, float]:
    return metrics_from_report_file(path)


def _parse_report_metric_value(raw_value: str) -> float | None:
    return parse_report_metric_value(raw_value)


def _parse_cost_spread(raw_value: str) -> dict[str, float]:
    return parse_cost_spread(raw_value)


def _find_previous_report_file(report_dir: Path, before: date) -> Path | None:
    """before tarihinden önceki en son rapor dosyası."""
    if not report_dir.is_dir():
        return None
    prev_file = None
    prev_date: date | None = None
    for f in report_dir.glob("*.md"):
        try:
            d = datetime.strptime(f.stem, "%Y-%m-%d").date()
        except ValueError:
            continue
        if d < before and (prev_date is None or d > prev_date):
            prev_file = f
            prev_date = d
    return prev_file


def _claim_ids_scope_lines(scope_label: str, claim_ids: list[int]) -> list[str]:
    """claim_ids başlık satırları — yalnızca test kohortunda measurement etiketi."""
    if not claim_ids:
        return []
    n = len(claim_ids)
    sorted_ids = sorted(claim_ids)
    if scope_label.startswith("test"):
        return [
            f"**Measurement kohortları:** {n} id "
            f"(measurement_50 + measurement_nli_30)",
        ]
    id_range = f"{sorted_ids[0]}–{sorted_ids[-1]}" if n > 1 else str(sorted_ids[0])
    return [
        f"**Claim ID filtresi:** {n} id (--claim-ids)",
        f"**Claim ID aralığı:** {id_range}",
    ]


def _load_previous_metrics(report_dir: Path, before: date) -> dict[str, float]:
    """Önceki rapor tablosundan sayısal metrikleri çıkar."""
    prev_file = _find_previous_report_file(report_dir, before)
    if not prev_file:
        return {}
    return _metrics_from_report_file(prev_file)


def _parse_failed_for_row(r: dict, rec: dict | None) -> bool:
    pf = bool(rec.get("parse_failed")) if rec else False
    if not pf and r.get("final_verdict") is None:
        reasoning = r.get("reasoning") or ""
        if "parse edilemedi" in reasoning.lower():
            pf = True
    return pf


def _security_risk_triggers(r: dict) -> list[str]:
    return security_risk_triggers(
        category=r.get("category"),
        initial_risk=r.get("initial_risk"),
        claim_text=r.get("claim_text") or "",
        calibration_flags=r.get("calibration_flags"),
    )


def _collect_warning_signals(
    rows: list[dict],
    debug: dict[int, dict],
    metrics: dict,
) -> dict:
    """Uyarı koşulları için ek sinyaller (mevcut metrik hesabına dokunmaz)."""
    verdicted = [r for r in rows if r.get("verified_at")]
    parse_fail_ids: list[int] = []
    risky_auto_accept: list[tuple[int, list[str]]] = []

    for r in verdicted:
        cid = int(r["claim_id"])
        rec = debug.get(cid)
        if _parse_failed_for_row(r, rec):
            parse_fail_ids.append(cid)

        if int(r.get("auto_accepted") or 0) != 1:
            continue
        triggers = _security_risk_triggers(r)
        if triggers:
            risky_auto_accept.append((cid, triggers))

    return {
        "parse_fail_ids": sorted(parse_fail_ids),
        "risky_auto_accept": sorted(risky_auto_accept, key=lambda x: x[0]),
    }


def _build_warnings(
    metrics: dict,
    signals: dict,
    prev: dict[str, float],
    *,
    has_previous_report: bool,
) -> list[str]:
    """Eşik-tabanlı uyarı satırları. İlk raporda boş döner."""
    if not has_previous_report:
        return []

    warnings: list[str] = []
    prev_cost = prev.get("$/claim (tahmini)")
    cur_cost = metrics.get("avg_cost_usd")
    if prev_cost and cur_cost and prev_cost > 0:
        increase = (cur_cost - prev_cost) / prev_cost
        if increase > 0.5:
            warnings.append(
                f"⚠️ $/claim: ${_num(cur_cost, 4)} (önceki ${_num(prev_cost, 4)}, "
                f"+{increase * 100:.0f}%) — claim başına maliyet belirgin arttı; "
                f"web search gibi daha pahalı adımların payı yükselmiş olabilir."
            )

    esc_rate = metrics.get("escalation_rate")
    if esc_rate is not None and esc_rate > 0.95:
        warnings.append(
            f"⚠️ Escalation oranı: {_pct(esc_rate)} — iddiaların neredeyse tamamı "
            f"ikinci aşamaya gidiyor; kaynak bulma veya ilk tur kalitesinde gerileme olabilir."
        )

    parse_fail_ids = signals.get("parse_fail_ids") or []
    if parse_fail_ids:
        id_list = ", ".join(str(i) for i in parse_fail_ids)
        warnings.append(
            f"⚠️ Parse fail: {len(parse_fail_ids)} adet (id: {id_list}) — "
            f"model yanıtı okunamadı; bu iddiaların sonucu güvenilir olmayabilir."
        )

    risky = signals.get("risky_auto_accept") or []
    if risky:
        detail = "; ".join(f"{cid} ({', '.join(triggers)})" for cid, triggers in risky)
        warnings.append(
            f"⚠️ Riskli otomasyon: {len(risky)} iddia ({detail}) — "
            f"otomatik kabul edilmiş ama yüksek riskli veya hassas görünüyor; "
            f"insan onayı atlanmış olabilir."
        )

    prev_retrieval = prev.get("retrieval_cited oranı (escalated)")
    cur_retrieval = metrics.get("retrieval_cited_rate")
    if prev_retrieval is not None and cur_retrieval is not None and cur_retrieval < prev_retrieval:
        drop_pp = (prev_retrieval - cur_retrieval) * 100
        warnings.append(
            f"⚠️ retrieval_cited oranı: {_pct(cur_retrieval)} (önceki {_pct(prev_retrieval)}, "
            f"-{drop_pp:.1f} pp) — kaynaklı yanıtların payı düştü; "
            f"kaynak eşleme mekanizması zayıflamış olabilir."
        )

    prev_p95 = prev.get("$/claim p95")
    cur_p95 = metrics.get("cost_p95")
    if prev_p95 and cur_p95 and prev_p95 > 0 and cur_p95 > prev_p95 * 1.5:
        ratio = cur_p95 / prev_p95
        warnings.append(
            f"⚠️ $/claim p95: ${_num(cur_p95, 4)} (baseline ${_num(prev_p95, 4)}, "
            f"×{ratio:.1f}) — kuyruk maliyeti belirgin yükseldi; birkaç pahalı iddia "
            f"ortalamayı değil uç değerleri etkiliyor olabilir."
        )

    prev_max = prev.get("$/claim max")
    cur_max = metrics.get("cost_max")
    if prev_max and cur_max and prev_max > 0 and cur_max > prev_max * 3:
        ratio = cur_max / prev_max
        warnings.append(
            f"⚠️ $/claim max: ${_num(cur_max, 4)} (baseline ${_num(prev_max, 4)}, "
            f"×{ratio:.1f}) — tek iddia maliyetinde uç sapma; web arama token "
            f"şişmesi veya cache miss kontrol edilmeli."
        )

    return warnings


def _render_distribution(dist: dict, order: tuple[str, ...]) -> str:
    parts = []
    for key in order:
        if key in dist:
            parts.append(f"{key} {dist[key]}")
    for key, val in sorted(dist.items()):
        if key not in order:
            parts.append(f"{key} {val}")
    return ", ".join(parts) if parts else "—"


def _render_report(
    *,
    metrics: dict,
    scope_label: str,
    video_ids: list[str],
    claim_ids: list[int],
    since: str | None,
    until: str | None,
    report_date: date,
    prev: dict[str, float],
    warnings: list[str] | None = None,
    is_first_report: bool = False,
) -> str:
    lines = [
        f"# Üretim izleme raporu — {report_date.isoformat()}",
        "",
    ]
    if is_first_report:
        lines.append("_Karşılaştırma yapılamadı, bu ilk rapor._")
        lines.append("")
    elif warnings:
        lines += [
            "## ⚠️ Uyarılar",
            "",
            *[f"- {w}" for w in warnings],
            "",
        ]
    lines.append(f"**Kapsam:** {scope_label}")
    if video_ids:
        lines.append(f"**Videolar:** {', '.join(video_ids)}")
    lines.extend(_claim_ids_scope_lines(scope_label, claim_ids))
    if since or until:
        lines.append(f"**Tarih aralığı:** {since or '…'} → {until or '…'}")
    lines += [
        "",
        f"- Toplam iddia (aktif): **{metrics['n_claims']}**",
        f"- Verdict almış: **{metrics['n_verdicts']}**",
        f"- Video sayısı: **{metrics['n_videos']}**",
        "",
        "## Özet metrikler",
        "",
        "| Metrik | Değer | Δ (önceki rapor) |",
        "|--------|------:|------------------|",
    ]

    def row(name: str, value: str, key: str | None = None, *, is_rate=False, is_money=False):
        cur = metrics.get(key) if key else None
        delta = _fmt_delta(cur, prev.get(name), is_rate=is_rate, is_money=is_money)
        lines.append(f"| {name} | {value} | {delta} |")

    row(
        "Claim sayısı / video (ort.)",
        _num(metrics["avg_claims_per_video"], 1),
        "avg_claims_per_video",
    )
    dedup_val = (
        f"{metrics['dedup_merged']}/{metrics['dedup_raw']} "
        f"({_pct(metrics['dedup_ratio'])}; {metrics['dedup_video_n']} tam-pipeline video)"
        if metrics["dedup_raw"]
        else "—"
    )
    lines.append(
        f"| Dedup merge oranı (chunk+global / ham) | {dedup_val} | "
        f"{_fmt_delta(metrics.get('dedup_ratio'), prev.get('Dedup merge oranı (chunk+global / ham)'), is_rate=True)} |"
    )
    row("Escalation oranı", _pct(metrics["escalation_rate"]), "escalation_rate", is_rate=True)
    row(
        "Web search oranı (escalated)",
        _pct(metrics["web_search_rate"]),
        "web_search_rate",
        is_rate=True,
    )
    row(
        "retrieval_cited oranı (escalated)",
        _pct(metrics["retrieval_cited_rate"]),
        "retrieval_cited_rate",
        is_rate=True,
    )
    row(
        "topic cache hit oranı (final paket)",
        _pct(metrics["cache_hit_rate"]),
        "cache_hit_rate",
        is_rate=True,
    )
    spec_val = _render_distribution(metrics["specificity_tier"], SPECIFICITY_ORDER)
    lines.append(f"| specificity_tier dağılımı | {spec_val} | — |")
    retry_val = (
        f"{metrics['parse_fail_n']} fail; retry {metrics['parse_retry_ok']}/{metrics['parse_retry_n']}"
        f" ({_pct(metrics['parse_retry_rate'])})"
        if metrics["parse_retry_n"]
        else f"{metrics['parse_fail_n']} fail; retry yok"
    )
    lines.append(f"| Parse fail + retry başarı | {retry_val} | — |")
    row("needs_human oranı", _pct(metrics["needs_human_rate"]), "needs_human_rate", is_rate=True)
    cost_val = (
        f"${metrics['avg_cost_usd']:.4f} (n={metrics['n_cost_samples']}, "
        f"{metrics['cost_sources']})"
        if metrics["avg_cost_usd"] is not None
        else "—"
    )
    lines.append(
        f"| $/claim (tahmini) | {cost_val} | "
        f"{_fmt_delta(metrics.get('avg_cost_usd'), prev.get('$/claim (tahmini)'), is_money=True)} |"
    )
    def _money(v):
        return f"${v:.4f}" if v is not None else "—"

    cost_spread = (
        f"p50 {_money(metrics.get('cost_p50'))} / p90 {_money(metrics.get('cost_p90'))} / "
        f"p95 {_money(metrics.get('cost_p95'))} / max {_money(metrics.get('cost_max'))}"
        if metrics.get("n_cost_samples")
        else "—"
    )
    lines.append(f"| $/claim p50/p90/p95/max | {cost_spread} | — |")
    search_spread = (
        f"p50 {_num(metrics.get('search_p50'), 1)} / p95 {_num(metrics.get('search_p95'), 1)} / "
        f"max {_num(metrics.get('search_max'), 1)} (n={metrics.get('n_search_samples')})"
        if metrics.get("n_search_samples")
        else "—"
    )
    lines.append(f"| web_search_call_count p50/p95/max | {search_spread} | — |")
    rel_n = metrics.get("n_relevance_scores") or 0
    if rel_n:
        basis = metrics.get("relevance_basis") or {}
        cited_n = basis.get("cited_package_item", 0)
        proxy_n = basis.get("proxy_relevance_exact_cited_not_tracked", 0)
        rel_spread = (
            f"p25 {_num(metrics.get('relevance_p25'), 3)} / "
            f"p50 {_num(metrics.get('relevance_p50'), 3)} / "
            f"p75 {_num(metrics.get('relevance_p75'), 3)} "
            f"(n={rel_n}; cited={cited_n} proxy={proxy_n})"
        )
    else:
        rel_spread = "— (henüz skor yok)"
    lines.append(f"| relevance_score p25/p50/p75 (shadow) | {rel_spread} | — |")
    lines.append(f"| processed (verdict almış) | {metrics['n_verdicts']} | — |")
    lines.append(f"| parse_failed | {metrics['parse_fail_n']} | — |")
    lines.append(f"| retrieval_failed | {metrics.get('retrieval_failed_n', 0)} | — |")
    lines.append(
        f"| compound_tier_mismatch sayısı | {metrics.get('compound_tier_mismatch_n', 0)} | — |"
    )
    lines.append(
        f"| would_auto_accept_after_all_gates | "
        f"{metrics.get('would_auto_accept_after_all_gates_n', 0)} | — |"
    )
    lines.append(
        f"| shadow gates (verdict/conf/compound) | "
        f"{metrics.get('would_require_human_verdict_gate_n', 0)} / "
        f"{metrics.get('would_require_human_confidence_gate_n', 0)} / "
        f"{metrics.get('would_require_human_compound_gate_n', 0)} | — |"
    )
    lines.append(
        f"| embedding_clustering_status | {metrics.get('embedding_clustering_status') or '—'} | — |"
    )
    row("escalated=0 (NLI-only) sayısı", str(metrics["escalated_0_n"]), "escalated_0_n")
    wa = metrics["would_auto_accept_v1"]
    wa_val = f"true {wa.get('true', 0)}, false {wa.get('false', 0)}"
    lines.append(f"| would_auto_accept_v1 | {wa_val} | — |")
    st_val = _render_distribution(metrics["source_tier"], SOURCE_TIER_ORDER)
    lines.append(f"| source_tier dağılımı | {st_val} | — |")

    if metrics["claims_by_video"]:
        lines += [
            "",
            "## Video bazında",
            "",
            "| video_id | full_pipeline | claim | dedup merge | verdict | escalated |",
            "|----------|:-------------:|------:|-------------|--------:|----------:|",
        ]
        for vid in sorted(metrics["claims_by_video"]):
            cnt = metrics["claims_by_video"][vid]
            d = _dedup_stats(vid, cnt)
            v_rows = [r for r in metrics.get("_rows", []) if r["video_id"] == vid and r.get("verified_at")]
            n_v = len(v_rows)
            n_e = sum(1 for r in v_rows if int(r.get("escalated") or 0) == 1)
            fp = "evet" if d["full_pipeline"] else "hayır"
            if d["full_pipeline"] and d["raw_total"]:
                dedup_s = f"{d['merged']}/{d['raw_total']} ({_pct(d['ratio'])})"
            else:
                dedup_s = "n/a"
            lines.append(f"| {vid} | {fp} | {cnt} | {dedup_s} | {n_v} | {n_e} |")

    notes: list[str] = []
    # Dedup notu: video tablosundaki full_pipeline ayrımının açıklaması.
    # Hücre zaten koşullu (evet → oran, hayır → n/a); not da yalnızca
    # n/a satırı varken gösterilir — aksi halde yanıltıcı "kısmi örnekleme" iddiası.
    has_partial_dedup = any(
        not has_full_dedup_pipeline(vid)
        for vid in (metrics.get("claims_by_video") or {})
    )
    if has_partial_dedup:
        notes.append(
            "- **Dedup merge:** Yalnızca `full_pipeline=evet` satırları gerçek ölçümdür "
            "(extraction_chunks veya smoke offline_dedup). `hayır` = measurement kohortundan "
            "kısmi örnekleme; dedup hücresi **n/a** (0% anlamına gelmez)."
        )
    spec_missing = metrics["specificity_tier"].get("(yok)", 0)
    if spec_missing > 0:
        notes.append(
            f"- **specificity_tier=(yok) {spec_missing} iddia** "
            f"({_pct(spec_missing / metrics['n_verdicts']) if metrics['n_verdicts'] else '—'}): "
            "bu mekanizma eklenmeden önce fact-check edilmiş — kapsam metriği yalnızca "
            "bundan sonraki turlar için anlamlı."
        )
    notes.append(
        "- **relevance_score (shadow):** eşik yok, gate yok. Escalated iddialarda "
        "cosine kaydı; cited evidence izlenirse o, yoksa proxy top-evidence. "
        "should_escalate / needs_human / calibration_flags değişmez."
    )
    if notes:
        lines += ["", "## Notlar", "", *notes]

    lines += [
        "",
        "## Kaynaklar",
        "",
        f"- DB: `data/monitor.db` (claims + verdicts)",
        f"- Debug: `data/factcheck_debug.jsonl`",
        f"- Batch usage: `data/pending_batches.json` (custom_id)",
        f"- Dedup: `data/extraction_chunks/` veya `data/smoke_*/offline_dedup.json`",
        "",
        "Maliyet tahmini: Sonnet 5 $2/M in + $10/M out; batch %50; "
        "cache write $2.50/M (batch %50); cache read $0.20/M.",
    ]
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="Üretim izleme özeti (mevcut veriden)")
    parser.add_argument(
        "--scope",
        default="test",
        help="test = measurement_50 + nli_30 + odZg/bZsor/jP5; all_verdicted = tüm verdict'lı iddialar",
    )
    parser.add_argument("--video-ids", default="", help="Virgülle video_id listesi")
    parser.add_argument("--claim-ids", default="", help="Virgülle claim_id listesi")
    parser.add_argument("--since", default=None, help="YYYY-MM-DD (verified_at / extracted_at)")
    parser.add_argument("--until", default=None, help="YYYY-MM-DD")
    parser.add_argument("--date", default=None, help="Rapor dosya tarihi YYYY-MM-DD (varsayılan: bugün)")
    parser.add_argument("--stdout", action="store_true", help="Dosyaya yazmadan stdout'a bas")
    parser.add_argument("--out", default="", help="Rapor yazılacak yol (varsayılan: data/ops_reports/YYYY-MM-DD.md)")
    parser.add_argument(
        "--compare-to",
        default="",
        help="Karşılaştırılacak önceki rapor dosyası (varsayılan: önceki YYYY-MM-DD.md)",
    )
    args = parser.parse_args()

    video_ids, claim_ids, scope_label = _resolve_scope(args)
    if args.scope == "all_verdicted":
        video_ids = []
        claim_ids = []

    report_date = (
        datetime.strptime(args.date, "%Y-%m-%d").date()
        if args.date
        else date.today()
    )

    conn = get_conn()
    try:
        rows = _fetch_claim_rows(
            conn,
            video_ids=video_ids,
            claim_ids=claim_ids,
            since=args.since,
            until=args.until,
        )
    finally:
        conn.close()

    debug = _latest_debug_by_claim()
    batch_usage = _batch_usage_by_claim()
    metrics = _compute_metrics(rows, debug, batch_usage)
    metrics["_rows"] = rows

    prev_file = None
    if args.compare_to:
        prev_file = Path(args.compare_to)
        has_previous_report = prev_file.is_file()
        prev = _metrics_from_report_file(prev_file) if has_previous_report else {}
    else:
        prev_file = _find_previous_report_file(OUT_DIR, report_date)
        has_previous_report = prev_file is not None
        prev = _load_previous_metrics(OUT_DIR, report_date)
    signals = _collect_warning_signals(rows, debug, metrics)
    warnings = _build_warnings(
        metrics,
        signals,
        prev,
        has_previous_report=has_previous_report,
    )
    body = _render_report(
        metrics=metrics,
        scope_label=scope_label,
        video_ids=video_ids,
        claim_ids=claim_ids,
        since=args.since,
        until=args.until,
        report_date=report_date,
        prev=prev,
        warnings=warnings,
        is_first_report=not has_previous_report,
    )

    if args.stdout:
        print(body, end="")
        return

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = Path(args.out) if args.out else OUT_DIR / f"{report_date.isoformat()}.md"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(body, encoding="utf-8")
    print(f"[ops_report] yazıldı: {out_path} (n_claims={metrics['n_claims']}, n_verdicts={metrics['n_verdicts']})")


if __name__ == "__main__":
    main()
