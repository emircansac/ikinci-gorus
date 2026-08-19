"""
NLI Phase 2 — 6 offline ölçüm (yeni API/model yok, production davranışı değişmez).

Kullanım:
    ./venv/bin/python pipeline/19_nli_phase2.py
    ./venv/bin/python pipeline/19_nli_phase2.py --skip-m6
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import statistics
import sys
from collections import Counter
from pathlib import Path

# Yerel cache: yeni indirme yok.
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")

sys.path.append(str(Path(__file__).parent.parent))

from utils.db import get_conn
from utils.evidence_retrieval import _get_embedder, best_evidence_snippet
from utils.factcheck_calibrate import (
    _cite_ids_from_evidence_item,
    _cite_ids_from_url,
    classify_cite_source,
)
from utils.reasoning_patterns import (
    evidence_has_partial_caveat,
    locate_partial_caveat_in_pieces,
)
from utils.reviewer_summary import is_compound_claim

ROOT = Path(__file__).parent.parent
PENDING = ROOT / "data" / "pending_batches.json"
DEBUG_LOG = ROOT / "data" / "factcheck_debug.jsonl"
CLOSE_JSON = ROOT / "data" / "ops_reports" / "2026-08-19-554-close.json"
OUT_DIR = ROOT / "data" / "measurement_nli_phase2"

SLICE_ID_FILES = (
    ROOT / "data" / "ops_reports" / "2026-08-18-slice100-ids.txt",
    ROOT / "data" / "ops_reports" / "2026-08-18-slice100b-ids.txt",
    ROOT / "data" / "ops_reports" / "2026-08-18-slice100c-ids.txt",
    ROOT / "data" / "ops_reports" / "2026-08-18-slice100d-ids.txt",
    ROOT / "data" / "ops_reports" / "2026-08-19-slice154e-ids.txt",
)

NLI_THRESHOLD = 0.75
BINARY_VERDICTS = frozenset({"doğrulanmış", "yanlış"})
NLI_TO_VERDICT = {
    "SUPPORTS": "doğrulanmış",
    "REFUTES": "yanlış",
    "NOT_ENOUGH_INFO": "belirsiz",
}
MISSING = "missing/not_available"

# 554 kayıtlarında bu kolonlar yok (turdan sonra eklendi). Yeniden hesaplanıp
# DB'ye yazılmaz. #1282 escalate nedeni statik.
STATIC_PARTIAL_CAVEAT = {
    1282: {
        "partial_caveat_matched_index": 1,
        "partial_caveat_matched_phrase": "however",
        "note": (
            "Kanıtın 2. parçasında 'However' — evidence_has_partial_caveat() "
            "escalate'i tetikledi. DB alanı boş olması hata değil (554 sonrası eklendi)."
        ),
    },
}

PRIORITY_GOLDEN = (865, 905, 961, 1282)
PRIORITY_NOTES = {
    865: (
        "NLI SUPPORTS@0.746 (eşik ALTI) → current-threshold would_skip değil. "
        "Known dangerous NLI disagreement; alakasız kanıt (tekerlekli sandalye). "
        "dangerous_false_support SAYISINA KATILMAZ."
    ),
    905: "NLI SUPPORTS@0.687, Claude tartışmalı — partial evidence → binary collapse.",
    961: "NLI SUPPORTS@0.679, Claude tartışmalı — complex/qualified evidence → binary collapse.",
    1282: (
        "NLI SUPPORTS@0.808 (eşik ÜSTÜ) ama partial_caveat (parça 2 'however') "
        "escalate etti → would_skip değil. Başarılı regresyon-önleme; kaçırılmadı. "
        "Confidence tek başına yeterli değil."
    ),
}

# Claim-strength: iki ayrı flag, birleştirilmez.
STRONG_ABSOLUTE = ("tamamen", "kesinlikle", "mutlaka", "yok eder", "bitirir")
STRONG_UNIVERSAL = ("her hasta", "herkeste", "daima")
STRONG_SPEED = ("anında", "saniyeler içinde", "bir gecede")
STRONG_DURABILITY = ("kalıcı çözüm",)
CAUSAL_PHRASES = ("neden olur", "tedavi eder", "önler")
DURABILITY_BIR_DAHA_RE = re.compile(
    r"bir daha.{0,24}(tekrarlamaz|olmaz)",
    re.IGNORECASE,
)


def _parse_ids_file(path: Path) -> list[int]:
    text = path.read_text(encoding="utf-8").replace("\n", ",")
    return [int(x.strip()) for x in text.split(",") if x.strip()]


def load_554_ids() -> list[int]:
    ids: list[int] = []
    for path in SLICE_ID_FILES:
        if not path.is_file():
            raise SystemExit(f"554 dilim ID dosyası yok: {path}")
        ids.extend(_parse_ids_file(path))
    uniq = sorted(set(ids))
    if len(uniq) != 554:
        raise SystemExit(f"554 kohort bekleniyordu, {len(uniq)} unique ID bulundu")
    return uniq


def _specificity_tier(flags: str | None, job: dict | None) -> str | None:
    if job and (job.get("specificity_tier") or "").strip():
        return str(job["specificity_tier"]).strip()
    for part in (flags or "").split(","):
        part = part.strip()
        if part.startswith("specificity_tier:"):
            return part.split(":", 1)[1] or None
    return None


def _cite_from_flags(flags: str | None) -> str | None:
    flag_set = {p.strip() for p in (flags or "").split(",") if p.strip()}
    for key in ("retrieval_cited", "web_search_override", "web_search_only"):
        if key in flag_set:
            return key
    return None


def _load_latest_jobs(claim_ids: set[int]) -> dict[int, dict]:
    latest: dict[int, dict] = {}
    if not PENDING.is_file():
        return latest
    data = json.loads(PENDING.read_text(encoding="utf-8"))
    for rec in data.get("batches") or []:
        for job in rec.get("jobs") or []:
            cid = job.get("claim_id")
            if cid in claim_ids:
                latest[int(cid)] = job
    return latest


def _load_latest_debug(claim_ids: set[int]) -> dict[int, dict]:
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
            if cid in claim_ids:
                latest[int(cid)] = rec
    return latest


def wilson_ci(k: int, n: int, z: float = 1.96) -> tuple[float, float] | None:
    if n <= 0:
        return None
    p = k / n
    z2 = z * z
    den = 1.0 + z2 / n
    center = (p + z2 / (2.0 * n)) / den
    margin = (z * math.sqrt((p * (1.0 - p) + z2 / (4.0 * n)) / n)) / den
    lo = max(0.0, center - margin)
    hi = min(1.0, center + margin)
    return lo, hi


def _pct(k: int | None, n: int | None) -> str:
    if k is None or n is None or n <= 0:
        return MISSING
    return f"{k}/{n} = {100.0 * k / n:.1f}%"


def _pct_ci(k: int | None, n: int | None) -> str:
    if k is None or n is None:
        return MISSING
    if n <= 0:
        return f"{MISSING} (denominator=0)"
    base = _pct(k, n)
    ci = wilson_ci(k, n)
    if ci is None:
        return base
    return f"{base} (Wilson 95% CI {100 * ci[0]:.1f}–{100 * ci[1]:.1f}%)"


def _rate_obj(k: int, n: int) -> dict:
    ci = wilson_ci(k, n)
    return {
        "numerator": k,
        "denominator": n,
        "rate": (k / n) if n else None,
        "display": _pct_ci(k, n),
        "wilson_95": (
            {"low": round(ci[0], 4), "high": round(ci[1], 4)} if ci else None
        ),
    }


def scan_strong_language(text: str) -> dict:
    raw = text or ""
    low = raw.lower()
    hits: list[str] = []
    buckets: dict[str, list[str]] = {
        "absolute": [],
        "universal": [],
        "speed": [],
        "durability": [],
    }
    for phrase in STRONG_ABSOLUTE:
        if phrase in low:
            buckets["absolute"].append(phrase)
            hits.append(phrase)
    for phrase in STRONG_UNIVERSAL:
        if phrase in low:
            buckets["universal"].append(phrase)
            hits.append(phrase)
    for phrase in STRONG_SPEED:
        if phrase in low:
            buckets["speed"].append(phrase)
            hits.append(phrase)
    for phrase in STRONG_DURABILITY:
        if phrase in low:
            buckets["durability"].append(phrase)
            hits.append(phrase)
    m = DURABILITY_BIR_DAHA_RE.search(raw)
    if m:
        buckets["durability"].append(m.group(0))
        hits.append(m.group(0))
    return {
        "strong_language": bool(hits),
        "strong_hits": hits,
        "strong_buckets": {k: v for k, v in buckets.items() if v},
    }


def scan_causal_language(text: str) -> dict:
    low = (text or "").lower()
    hits = [p for p in CAUSAL_PHRASES if p in low]
    return {"causal_language": bool(hits), "causal_hits": hits}


def nli_piece_texts(evidence: list[dict] | None) -> list[str]:
    pieces = []
    for item in (evidence or [])[:3]:
        title = (item.get("title") or "").strip()
        abstract = (item.get("abstract") or "").strip()
        text = f"{title} {abstract}".strip()
        if text:
            pieces.append(text)
    return pieces


def top_package_item(evidence: list[dict] | None) -> dict | None:
    items = [e for e in (evidence or []) if isinstance(e, dict)]
    if not items:
        return None

    def _score(item: dict) -> float:
        val = item.get("rerank_score")
        try:
            return float(val)
        except (TypeError, ValueError):
            return float("-inf")

    ranked = sorted(items, key=_score, reverse=True)
    if _score(ranked[0]) == float("-inf"):
        return items[0]
    return ranked[0]


def match_cited_package_item(source_url: str | None, evidence: list[dict] | None) -> dict | None:
    if not source_url or not evidence:
        return None
    cited = _cite_ids_from_url(source_url)
    if not cited:
        return None
    for item in evidence:
        if cited & _cite_ids_from_evidence_item(item):
            return item
    return None


def evidence_text_of(item: dict | None) -> str:
    if not item:
        return ""
    return f"{(item.get('title') or '').strip()} {(item.get('abstract') or '').strip()}".strip()


def cosine_sim(embedder, a: str, b: str) -> float | None:
    if embedder is None:
        return None
    a = (a or "").strip()
    b = (b or "").strip()
    if not a or not b:
        return None
    import numpy as np

    va, vb = embedder.encode([a, b])
    na = float(np.linalg.norm(va))
    nb = float(np.linalg.norm(vb))
    if na <= 0 or nb <= 0:
        return None
    return float(np.dot(va, vb) / (na * nb))


def _median(vals: list[float]) -> float | None:
    if not vals:
        return None
    return float(statistics.median(vals))


def load_rows(claim_ids: list[int]) -> list[dict]:
    id_set = set(claim_ids)
    jobs = _load_latest_jobs(id_set)
    debug = _load_latest_debug(id_set)
    conn = get_conn()
    try:
        ph = ",".join("?" * len(claim_ids))
        db_rows = conn.execute(
            f"""
            SELECT c.claim_id, c.claim_text, c.category, c.initial_risk,
                   c.search_query_en, c.video_id,
                   v.nli_label, v.nli_confidence, v.nli_evidence_snippet,
                   v.escalated, v.final_verdict, v.confidence AS claude_confidence,
                   v.source_url, v.reasoning, v.calibration_flags,
                   v.evidence_stance, v.source_directness, v.source_tier,
                   v.would_require_human_compound_gate
            FROM claims c
            JOIN verdicts v ON v.claim_id = c.claim_id
            WHERE c.claim_id IN ({ph})
            ORDER BY c.claim_id
            """,
            claim_ids,
        ).fetchall()
    finally:
        conn.close()

    rows: list[dict] = []
    for raw in db_rows:
        r = dict(raw)
        cid = int(r["claim_id"])
        job = jobs.get(cid)
        dbg = debug.get(cid)
        flags = r.get("calibration_flags") or ""
        evidence = list((job or {}).get("evidence") or [])
        pieces = nli_piece_texts(evidence)
        joined = " ".join(pieces)
        runtime_loc = locate_partial_caveat_in_pieces(pieces) if pieces else None
        static = STATIC_PARTIAL_CAVEAT.get(cid)

        nli_label = (r.get("nli_label") or "").strip() or None
        nli_conf = r.get("nli_confidence")
        if nli_conf is not None:
            try:
                nli_conf = float(nli_conf)
            except (TypeError, ValueError):
                nli_conf = None

        nli_available = nli_label is not None and nli_conf is not None
        nli_binary = nli_label in ("SUPPORTS", "REFUTES")
        nli_threshold_pass = bool(
            nli_available and nli_binary and nli_conf is not None and nli_conf >= NLI_THRESHOLD
        )

        if joined:
            caveat_runtime = evidence_has_partial_caveat(joined)
        elif r.get("nli_evidence_snippet"):
            caveat_runtime = evidence_has_partial_caveat(r.get("nli_evidence_snippet"))
        else:
            caveat_runtime = None  # missing, not False

        caveat_effective = bool(caveat_runtime) or bool(static)
        would_skip_current = bool(nli_threshold_pass and not caveat_effective)

        verdict = r.get("final_verdict")
        mapped = NLI_TO_VERDICT.get(nli_label or "")
        same_direction = bool(
            mapped in BINARY_VERDICTS
            and verdict in BINARY_VERDICTS
            and mapped == verdict
        )

        strong = scan_strong_language(r.get("claim_text") or "")
        causal = scan_causal_language(r.get("claim_text") or "")
        compound = is_compound_claim(r.get("claim_text") or "", r.get("reasoning"))
        comp_map = (job or {}).get("component_evidence_map")
        comp_n = None
        if isinstance(comp_map, dict) and comp_map.get("components") is not None:
            comp_n = len(comp_map.get("components") or [])

        source_url = (r.get("source_url") or "").strip() or None
        cite_flags = _cite_from_flags(flags)
        cite_debug = (dbg or {}).get("cite_source")
        cite_recomputed = None
        if evidence:
            cite_recomputed = classify_cite_source(source_url or "", evidence)
        elif source_url:
            cite_recomputed = classify_cite_source(source_url, evidence)

        cited_item = match_cited_package_item(source_url, evidence) if source_url else None
        proxy_item = top_package_item(evidence)
        if cited_item is not None:
            used_item = cited_item
            evidence_basis = "cited_package_item"
        elif proxy_item is not None:
            used_item = proxy_item
            evidence_basis = "proxy_relevance_exact_cited_not_tracked"
        else:
            used_item = None
            evidence_basis = MISSING

        db_caveat_index = MISSING
        db_caveat_phrase = MISSING
        if dbg and dbg.get("partial_caveat_matched_index") is not None:
            db_caveat_index = dbg.get("partial_caveat_matched_index")
            db_caveat_phrase = dbg.get("partial_caveat_matched_phrase") or MISSING

        rows.append({
            "claim_id": cid,
            "claim_text": r.get("claim_text") or "",
            "category": r.get("category") or MISSING,
            "initial_risk": r.get("initial_risk") or MISSING,
            "video_id": r.get("video_id"),
            "escalated": int(r.get("escalated") or 0),
            "nli_label": nli_label if nli_label else MISSING,
            "nli_confidence": nli_conf if nli_conf is not None else MISSING,
            "nli_available": nli_available,
            "nli_evidence_snippet": r.get("nli_evidence_snippet") or "",
            "final_verdict": verdict if verdict else MISSING,
            "claude_confidence": r.get("claude_confidence"),
            "specificity_tier": _specificity_tier(flags, job) or MISSING,
            "compound_candidate": compound,
            "compound_source": "is_compound_claim(claim_text, reasoning) — şemada compound_candidate yok",
            "compound_tier_mismatch": "compound_tier_mismatch" in {
                p.strip() for p in flags.split(",") if p.strip()
            },
            "component_n": comp_n if comp_n is not None else MISSING,
            "calibration_flags": flags,
            "source_url": source_url or MISSING,
            "cite_source_flags": cite_flags or MISSING,
            "cite_source_debug": cite_debug or MISSING,
            "cite_source_recomputed": cite_recomputed or MISSING,
            "nli_threshold_pass": nli_threshold_pass,
            "partial_caveat_runtime": (
                caveat_runtime if caveat_runtime is not None else MISSING
            ),
            "partial_caveat_runtime_loc": runtime_loc or MISSING,
            "partial_caveat_db_index": db_caveat_index,
            "partial_caveat_db_phrase": db_caveat_phrase,
            "partial_caveat_static": static or None,
            "partial_caveat_effective": caveat_effective,
            "would_skip_nli": nli_threshold_pass,
            "would_skip": would_skip_current,
            "same_direction_binary": same_direction,
            "package_n": len(evidence) if job is not None else MISSING,
            "package_present": bool(job is not None and evidence),
            "nli_pieces_n": len(pieces) if job is not None else MISSING,
            "evidence_basis": evidence_basis,
            "used_evidence_title": (used_item or {}).get("title") if used_item else MISSING,
            "used_evidence_url": (used_item or {}).get("url") if used_item else MISSING,
            "used_evidence_text": evidence_text_of(used_item),
            "proxy_title": (proxy_item or {}).get("title") if proxy_item else MISSING,
            "proxy_url": (proxy_item or {}).get("url") if proxy_item else MISSING,
            "cited_package_url": (cited_item or {}).get("url") if cited_item else MISSING,
            **strong,
            **causal,
        })
    return rows


def binary_reverse(row: dict) -> bool:
    label = row.get("nli_label")
    verdict = row.get("final_verdict")
    return (
        (label == "SUPPORTS" and verdict == "yanlış")
        or (label == "REFUTES" and verdict == "doğrulanmış")
    )


def skip_metrics(rows: list[dict], *, skip_key: str, eligible_n: int | None = None) -> dict:
    eligible = eligible_n if eligible_n is not None else len(rows)
    skipped = [r for r in rows if r.get(skip_key)]
    would_skip_n = len(skipped)
    safe = [
        r for r in skipped
        if r.get("same_direction_binary") and r.get("final_verdict") in BINARY_VERDICTS
    ]
    dfs = [r for r in skipped if r.get("nli_label") == "SUPPORTS" and r.get("final_verdict") == "yanlış"]
    dfr = [r for r in skipped if r.get("nli_label") == "REFUTES" and r.get("final_verdict") == "doğrulanmış"]
    mixed = [r for r in skipped if r.get("final_verdict") == "tartışmalı"]
    uncertain = [r for r in skipped if r.get("final_verdict") == "belirsiz"]
    collapse_n = len(mixed) + len(uncertain)
    return {
        "eligible_n": eligible,
        "would_skip_n": would_skip_n,
        "skip_rate": _rate_obj(would_skip_n, eligible),
        "safe_skip_n": len(safe),
        "safe_skip_precision": _rate_obj(len(safe), would_skip_n),
        "dangerous_false_support_count": len(dfs),
        "dangerous_false_support_ids": [r["claim_id"] for r in dfs],
        "dangerous_false_support_rate": _rate_obj(len(dfs), would_skip_n),
        "dangerous_false_refute_count": len(dfr),
        "dangerous_false_refute_ids": [r["claim_id"] for r in dfr],
        "dangerous_false_refute_rate": _rate_obj(len(dfr), would_skip_n),
        "mixed_collapse_n": len(mixed),
        "mixed_collapse_ids": [r["claim_id"] for r in mixed],
        "uncertain_collapse_n": len(uncertain),
        "uncertain_collapse_ids": [r["claim_id"] for r in uncertain],
        "collapse_rate": _rate_obj(collapse_n, would_skip_n),
        "would_skip_ids": [r["claim_id"] for r in skipped],
    }


def _tier_conf_subset(rows: list[dict], tier: str, thresh: float) -> list[dict]:
    out = []
    for r in rows:
        if r.get("specificity_tier") != tier:
            continue
        if not r.get("nli_available"):
            continue
        conf = r.get("nli_confidence")
        if not isinstance(conf, (int, float)):
            continue
        if conf >= thresh:
            out.append(r)
    return out


def audit_evidence_tracking(rows: list[dict]) -> dict:
    n = len(rows)
    source_present = sum(1 for r in rows if r.get("source_url") not in (None, "", MISSING))
    cited_match = sum(1 for r in rows if r.get("evidence_basis") == "cited_package_item")
    proxy = sum(
        1 for r in rows if r.get("evidence_basis") == "proxy_relevance_exact_cited_not_tracked"
    )
    missing_ev = sum(1 for r in rows if r.get("evidence_basis") == MISSING)
    cite_flags = Counter(
        r.get("cite_source_flags") if r.get("cite_source_flags") != MISSING else MISSING
        for r in rows
    )
    return {
        "tracked_fields": {
            "verdicts.source_url": "var — Claude'un yazdığı URL",
            "verdicts.reasoning": "var — gerekçe metni; evidence ID yok",
            "verdicts.nli_evidence_snippet": "var — NLI'nın gördüğü 500 karakter; Claude cite'ı değil",
            "evidence_id": "yok",
            "cited_snippet_of_web_search": "yok — web_search içeriği saklanmıyor",
            "pending_batches.jobs[].evidence": "var — retrieval paketi (title/url/abstract)",
            "factcheck_debug.cite_source": "var (kayıt varsa) — retrieval_cited / web_search_*",
            "partial_caveat_matched_index": "554 kayıtlarında yok (sonradan eklendi)",
        },
        "conclusion": (
            "Claude'un final kararında hangi paketi kullandığı URL eşlemesiyle "
            "kısmen izlenebiliyor (source_url ∩ paket). Eşleşen kayıtta paket "
            "title+abstract kullanılır. Eşleşmezse (web_search veya boş URL) "
            "alıntılanan metin YOK — uydurulmaz; retrieval en üst sırası PROXY."
        ),
        "n_eligible": n,
        "source_url_present": _rate_obj(source_present, n),
        "cited_package_item": _rate_obj(cited_match, n),
        "proxy_relevance": _rate_obj(proxy, n),
        "evidence_missing": _rate_obj(missing_ev, n),
        "cite_source_flags": dict(cite_flags),
    }


def conf_summary(vals: list[float]) -> dict:
    if not vals:
        return {"n": 0, "p50": MISSING, "mean": MISSING, "min": MISSING, "max": MISSING}
    return {
        "n": len(vals),
        "p50": round(statistics.median(vals), 3),
        "mean": round(statistics.mean(vals), 3),
        "min": round(min(vals), 3),
        "max": round(max(vals), 3),
    }


def select_m6(rows: list[dict], regression_ids: set[int]) -> list[int]:
    esc = [r for r in rows if r.get("escalated") == 1]
    supports = sorted(
        [r for r in esc if r.get("nli_label") == "SUPPORTS" and isinstance(r.get("nli_confidence"), (int, float))],
        key=lambda r: r["nli_confidence"],
        reverse=True,
    )[:10]
    refutes = sorted(
        [r for r in esc if r.get("nli_label") == "REFUTES" and isinstance(r.get("nli_confidence"), (int, float))],
        key=lambda r: r["nli_confidence"],
        reverse=True,
    )[:10]
    direct = sorted(
        [r for r in esc if r.get("specificity_tier") == "direct"],
        key=lambda r: (
            isinstance(r.get("nli_confidence"), (int, float)),
            r.get("nli_confidence") if isinstance(r.get("nli_confidence"), (int, float)) else -1,
        ),
        reverse=True,
    )[:10]
    mixed = sorted(
        [
            r for r in esc
            if r.get("final_verdict") in ("tartışmalı", "belirsiz")
            and isinstance(r.get("nli_confidence"), (int, float))
        ],
        key=lambda r: r["nli_confidence"],
        reverse=True,
    )[:10]
    gold = [r for r in esc if r["claim_id"] in regression_ids]
    # Öncelikli 4 her zaman
    priority = [r for r in esc if r["claim_id"] in PRIORITY_GOLDEN]
    chosen: dict[int, str] = {}
    for tag, group in (
        ("top_supports", supports),
        ("top_refutes", refutes),
        ("direct", direct),
        ("mixed_uncertain", mixed),
        ("golden", gold),
        ("priority", priority),
    ):
        for r in group:
            chosen.setdefault(r["claim_id"], tag)
    return sorted(chosen)


def run_m6(rows_by_id: dict[int, dict], sample_ids: list[int]) -> dict:
    from utils.nli import nli_check

    results = []
    for i, cid in enumerate(sample_ids, 1):
        row = rows_by_id[cid]
        print(f"[m6] {i}/{len(sample_ids)} claim_id={cid}", flush=True)
        text = row.get("used_evidence_text") or ""
        title = row.get("used_evidence_title") if row.get("used_evidence_title") != MISSING else ""
        # used_evidence_text zaten title+abstract. Abstract'ı ayırmak için paket metnini böl.
        abstract = text
        if title and text.startswith(str(title)):
            abstract = text[len(str(title)):].strip()
        snippet = MISSING
        full = MISSING
        snip_nli = MISSING
        full_nli = MISSING
        if not text:
            rec = {
                "claim_id": cid,
                "evidence_basis": row.get("evidence_basis"),
                "snippet_status": MISSING,
                "full_status": MISSING,
            }
            results.append(rec)
            continue
        snippet = best_evidence_snippet(row["claim_text"], abstract or text)
        full = text
        if not snippet:
            snip_nli = MISSING
        else:
            sn = nli_check(row["claim_text"], snippet)
            snip_nli = {
                "nli_label": sn["nli_label"],
                "nli_confidence": sn["nli_confidence"],
                "partial_caveat": evidence_has_partial_caveat(snippet),
            }
        fn = nli_check(row["claim_text"], full)
        full_nli = {
            "nli_label": fn["nli_label"],
            "nli_confidence": fn["nli_confidence"],
            "partial_caveat": evidence_has_partial_caveat(full),
        }
        results.append({
            "claim_id": cid,
            "final_verdict": row.get("final_verdict"),
            "stored_nli": row.get("nli_label"),
            "stored_conf": row.get("nli_confidence"),
            "evidence_basis": row.get("evidence_basis"),
            "snippet_nli": snip_nli,
            "full_nli": full_nli,
            "snippet_len": len(snippet) if snippet != MISSING else 0,
            "full_len": len(full),
            "specificity_tier": row.get("specificity_tier"),
        })

    raw_path = ROOT / "data" / "measurement_nli_phase2" / "m6_raw.json"
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_text(json.dumps({
        "sample_ids": sample_ids,
        "results": results,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    return finalize_m6(sample_ids, results)


def finalize_m6(sample_ids: list[int], results: list[dict]) -> dict:
    def _skip_flags(nli_obj) -> tuple[bool, bool]:
        if nli_obj in (None, MISSING) or not isinstance(nli_obj, dict):
            return False, False
        label = nli_obj.get("nli_label")
        conf = nli_obj.get("nli_confidence")
        nli_pass = label in ("SUPPORTS", "REFUTES") and isinstance(conf, (int, float)) and conf >= NLI_THRESHOLD
        caveat = bool(nli_obj.get("partial_caveat"))
        return nli_pass, bool(nli_pass and not caveat)

    def _attach(cid: int, skip_nli: bool, skip_cur: bool, verdict: str, label: str) -> dict:
        same = NLI_TO_VERDICT.get(label) == verdict and verdict in BINARY_VERDICTS
        return {
            "claim_id": cid,
            "would_skip_nli": skip_nli,
            "would_skip": skip_cur,
            "nli_label": label,
            "final_verdict": verdict,
            "same_direction_binary": same,
        }

    def _agg(mode: str) -> dict:
        tagged = []
        confs = []
        for rec in results:
            nli_obj = rec.get(f"{mode}_nli")
            verdict = rec.get("final_verdict")
            if nli_obj in (None, MISSING) or not isinstance(nli_obj, dict):
                continue
            skip_nli, skip_cur = _skip_flags(nli_obj)
            tagged.append(_attach(rec["claim_id"], skip_nli, skip_cur, verdict, nli_obj["nli_label"]))
            if isinstance(nli_obj.get("nli_confidence"), (int, float)):
                confs.append(float(nli_obj["nli_confidence"]))
        return {
            "n_ran": len(tagged),
            "n_sample": len(results),
            "n_missing_evidence": sum(
                1 for rec in results if rec.get(f"{mode}_nli") in (None, MISSING)
            ),
            "current_threshold": skip_metrics(tagged, skip_key="would_skip"),
            "nli_only_threshold": skip_metrics(tagged, skip_key="would_skip_nli"),
            "confidence": conf_summary(confs),
        }

    return {
        "sample_ids": sample_ids,
        "n_sample": len(sample_ids),
        "note": (
            "Stratified/biased örneklem — skip_rate production prevalence DEĞİL. "
            "Yalnızca snippet vs full-text ablasyonu."
        ),
        "snippet": _agg("snippet"),
        "full": _agg("full"),
        "results": results,
    }


def golden_view(row: dict, relevance: float | None) -> dict:
    cid = row["claim_id"]
    return {
        "claim_id": cid,
        "priority": cid in PRIORITY_GOLDEN,
        "note": PRIORITY_NOTES.get(cid, ""),
        "nli_label": row.get("nli_label"),
        "nli_confidence": row.get("nli_confidence"),
        "final_verdict": row.get("final_verdict"),
        "specificity_tier": row.get("specificity_tier"),
        "nli_threshold_pass": row.get("nli_threshold_pass"),
        "would_skip_nli": row.get("would_skip_nli"),
        "would_skip_current": row.get("would_skip"),
        "partial_caveat_effective": row.get("partial_caveat_effective"),
        "partial_caveat_runtime": row.get("partial_caveat_runtime"),
        "partial_caveat_db_index": row.get("partial_caveat_db_index"),
        "partial_caveat_static": bool(row.get("partial_caveat_static")),
        "strong_language": row.get("strong_language"),
        "strong_hits": row.get("strong_hits"),
        "causal_language": row.get("causal_language"),
        "causal_hits": row.get("causal_hits"),
        "compound_candidate": row.get("compound_candidate"),
        "evidence_basis": row.get("evidence_basis"),
        "relevance": relevance if relevance is not None else MISSING,
        "used_evidence_title": row.get("used_evidence_title"),
        "binary_reverse": binary_reverse(row),
    }


def _md_metrics_table(title: str, m: dict) -> list[str]:
    lines = [
        f"**{title}**",
        "",
        "| Metrik | Sayı | Oran |",
        "|---|---:|---|",
        f"| eligible_n | {m['eligible_n']} | — |",
        f"| would_skip_n | {m['would_skip_n']} | {m['skip_rate']['display']} |",
        f"| safe_skip_n | {m['safe_skip_n']} | {m['safe_skip_precision']['display']} |",
        f"| dangerous_false_support | {m['dangerous_false_support_count']} | {m['dangerous_false_support_rate']['display']} |",
        f"| dangerous_false_refute | {m['dangerous_false_refute_count']} | {m['dangerous_false_refute_rate']['display']} |",
        f"| mixed_collapse | {m['mixed_collapse_n']} | — |",
        f"| uncertain_collapse | {m['uncertain_collapse_n']} | — |",
        f"| collapse (mixed+uncertain) | {m['mixed_collapse_n'] + m['uncertain_collapse_n']} | {m['collapse_rate']['display']} |",
        "",
    ]
    return lines


def _md_golden_table(views: list[dict], extra_cols: list[tuple[str, str]]) -> list[str]:
    headers = ["id", "NLI", "conf", "Claude"] + [h for h, _ in extra_cols]
    lines = [
        "| " + " | ".join(headers) + " |",
        "|" + "|".join(["---"] * len(headers)) + "|",
    ]
    for v in views:
        cells = [
            f"#{v['claim_id']}" + (" ★" if v["priority"] else ""),
            str(v["nli_label"]),
            str(v["nli_confidence"]),
            str(v["final_verdict"]),
        ]
        for _, key in extra_cols:
            val = v.get(key, "")
            if isinstance(val, float):
                cells.append(f"{val:.3f}")
            elif isinstance(val, list):
                cells.append(", ".join(str(x) for x in val) or "—")
            else:
                cells.append(str(val))
        lines.append("| " + " | ".join(cells) + " |")
    lines.append("")
    return lines


def render_report(payload: dict) -> str:
    a = payload["audit"]
    m1c = payload["m1"]["current"]
    m1n = payload["m1"]["nli_only"]
    lines = [
        "# NLI Phase 2 — 6 offline ölçüm",
        "",
        "Kod / eşik / production kuralı **değişmedi**. Yeni model yok. "
        "`final_verdict` bu raporda **ground truth değil** — mevcut pahalı aşamanın "
        "referans verdict'i. Metrikler **NLI accuracy against truth değil**, "
        "**NLI agreement / safe-skip against current second-stage verdict**.",
        "",
        "## Kohort ve skip tanımları",
        "",
        f"- Kohort: 554 Dilim 1–5 ID listesi. eligible = `escalated=1` → "
        f"**n={payload['eligible_n']}** (3 NLI-only, escalated=0, eligible dışı).",
        f"- NLI kaydı yok (Dilim 1 `--skip-nli`): **{payload['nli_missing_n']}/{payload['eligible_n']}** "
        f"— sessizce False/0 sayılmadı; `{MISSING}`.",
        f"- Production NLI eşiği: **{NLI_THRESHOLD}** (bu turda değiştirilmedi).",
        "",
        "| Skip tanımı | Kural | Bu raporda |",
        "|---|---|---|",
        "| `nli_threshold_pass` / would_skip_nli | SUPPORTS/REFUTES **ve** conf≥0.75 | "
        "Ölçüm 1 formül satırı; #1282 dahildir |",
        "| `would_skip` (current-threshold) | aynı **ve** `evidence_has_partial_caveat` yok | "
        "dangerous_false_support paydası; #1282 **dahil değil** |",
        "",
        "### #865 vs #1282 (karıştırma)",
        "",
        f"- **#865** NLI SUPPORTS@0.746 — eşik **altı** → şu an would_skip **değil**. "
        f"Known dangerous NLI disagreement / regression case. "
        f"**current-threshold dangerous_false_support SAYISINA KATILMAZ.**",
        f"- **#1282** NLI SUPPORTS@0.808 — eşik **üstü** ama parça 2'deki `however` "
        f"`evidence_has_partial_caveat()` ile escalate etti → would_skip **değil**. "
        f"**Başarılı regresyon-önleme**, kaçırılma değil. Confidence tek başına yeterli değil.",
        "",
        f"Binary ters (SUPPORTS→yanlış veya REFUTES→doğrulanmış) 554 kapanış metni **14** dedi; "
        f"aynı tanımla DB'de **{payload['binary_reverse_n']}** bulundu "
        f"(kapanış JSON `binary=true` de {payload['close_binary_n']}). "
        f"14. kayıt uydurulmadı. Regresyon seti = binary ters ∪ öncelikli 4 "
        f"(#865/#905/#961/#1282) → **n={payload['regression_n']}**.",
        "",
        "## Ölçüm 3 ön-denetim — Claude'un kullandığı kanıt izleniyor mu?",
        "",
        a["conclusion"],
        "",
        "| Alan | Durum |",
        "|---|---|",
    ]
    for k, v in a["tracked_fields"].items():
        lines.append(f"| `{k}` | {v} |")
    lines += [
        "",
        f"- source_url dolu: {a['source_url_present']['display']}",
        f"- paket URL eşleşmesi (cited_package_item): {a['cited_package_item']['display']}",
        f"- proxy relevance (exact cited not tracked): {a['proxy_relevance']['display']}",
        f"- paket/kanıt yok: {a['evidence_missing']['display']}",
        f"- cite_source (flags): `{json.dumps(a['cite_source_flags'], ensure_ascii=False)}`",
        "",
        "---",
        "",
        "## Ölçüm 1 — Mevcut model benchmark",
        "",
        "safe_skip = NLI→verdict aynı yön **ve** final_verdict binary (doğrulanmış/yanlış). "
        "tartışmalı/belirsiz would_skip içindeyse paydadan **çıkarılmaz** — başarısız skip / collapse.",
        "",
    ]
    lines += _md_metrics_table(
        "A. Current-threshold would_skip (conf≥0.75 **ve** caveat yok) — dangerous_false_support paydası",
        m1c,
    )
    lines += _md_metrics_table(
        "B. NLI-only threshold pass (conf≥0.75, caveat kapısı **hariç**) — tanı kesiti",
        m1n,
    )
    lines += [
        f"nli_threshold_pass id: {m1n['would_skip_ids']}",
        f"current would_skip id: {m1c['would_skip_ids'] or '∅'}",
        "",
        "Yorum (karar değil): Escalated kohortta current-threshold skip **neredeyse/hiç yok** — "
        "0.75 üstü SUPPORTS/REFUTES kayıtların hepsi caveat kapısından geçiyor. "
        "Bu, eşiği düşürmenin tek başına yetmeyeceğini (#1282) ve mevcut kapının "
        "yüksek-güven NLI-only'yi zaten kestiğini gösterir. #865 eşik altında kaldığı "
        "için current-threshold false-support **0** — bu 'iyileşti' değil, eşiğin "
        "o vakayı henüz skip etmemesi.",
        "",
        "### Öncelikli golden + regresyon seti",
        "",
    ]
    lines += _md_golden_table(
        payload["golden_views"],
        [
            ("tier", "specificity_tier"),
            ("nli_pass", "nli_threshold_pass"),
            ("skip_now", "would_skip_current"),
            ("caveat", "partial_caveat_effective"),
            ("binary_ters", "binary_reverse"),
        ],
    )
    for v in payload["golden_views"]:
        if v["priority"] and v.get("note"):
            lines.append(f"- **#{v['claim_id']}:** {v['note']}")
    lines += ["", "---", "", "## Ölçüm 2 — specificity_tier × confidence", ""]
    lines.append(
        "Her satır: eligible = escalated ∩ tier ∩ nli kayıtlı ∩ conf≥eşik. "
        "would_skip current = o kümede SUPPORTS/REFUTES ∩ caveat yok "
        "(eşik zaten eligible filtresinde)."
    )
    lines.append("")
    lines.append(
        "| Koşul | eligible_n | would_skip_n (current) | safe_skip_precision | "
        "dangerous_false_support | collapse_rate |"
    )
    lines.append("|---|---:|---:|---|---|---|")
    for row in payload["m2"]:
        m = row["current"]
        lines.append(
            f"| {row['label']} | {m['eligible_n']} | {m['would_skip_n']} | "
            f"{m['safe_skip_precision']['display']} | "
            f"{m['dangerous_false_support_count']} ({m['dangerous_false_support_rate']['display']}) | "
            f"{m['collapse_rate']['display']} |"
        )
    lines.append("")
    lines.append("NLI-only (caveat hariç) aynı kesitler:")
    lines.append("")
    lines.append(
        "| Koşul | eligible_n | would_skip_n (nli-only) | safe_skip_precision | "
        "dangerous_false_support | collapse_rate |"
    )
    lines.append("|---|---:|---:|---|---|---|")
    for row in payload["m2"]:
        m = row["nli_only"]
        lines.append(
            f"| {row['label']} | {m['eligible_n']} | {m['would_skip_n']} | "
            f"{m['safe_skip_precision']['display']} | "
            f"{m['dangerous_false_support_count']} ({m['dangerous_false_support_rate']['display']}) | "
            f"{m['collapse_rate']['display']} |"
        )
    lines.append("")
    lines.append("Satır kimlikleri / etiket dağılımı (eligible küçük olduğu için):")
    lines.append("")
    for row in payload["m2"]:
        ids = row.get("eligible_ids") or []
        counts = row.get("nli_label_counts") or {}
        id_s = ",".join(f"#{i}" for i in ids) if ids else "∅"
        lines.append(f"- {row['label']}: n={len(ids)} labels={counts} ids={id_s}")
    lines += [
        "",
        "Yorum (karar değil): Direct + 0.75'te skip adayı 2 kayıt (#1282, #1250); "
        "ikisi de caveat ile escalate. Eşik 0.70/0.65'e inince aday artar ama "
        "çoğu tartışmalı collapse — precision/collapse trade-off. Eşik değiştirilmedi.",
        "",
        "Golden tier/conf:",
        "",
    ]
    lines += _md_golden_table(
        payload["golden_views"],
        [("tier", "specificity_tier"), ("conf", "nli_confidence"), ("caveat", "partial_caveat_effective")],
    )
    rel = payload["m3"]
    lines += [
        "---",
        "",
        "## Ölçüm 3 — Evidence relevance (cosine)",
        "",
        f"Embedder: `paraphrase-multilingual-MiniLM-L12-v2` (zaten yüklü). "
        f"Etiket: **{rel['label']}**.",
        "",
        f"| Grup | n | medyan cosine |",
        f"|---|---:|---:|",
        f"| false_skip (would_skip current ∧ ¬safe_skip) | {rel['false_skip']['n']} | {rel['false_skip']['median_display']} |",
        f"| safe_skip (would_skip current ∧ safe) | {rel['safe_skip']['n']} | {rel['safe_skip']['median_display']} |",
        f"| nli_threshold_pass ∧ ¬safe (caveat-öncesi false) | {rel['nli_pass_false']['n']} | {rel['nli_pass_false']['median_display']} |",
        f"| nli_threshold_pass ∧ safe | {rel['nli_pass_safe']['n']} | {rel['nli_pass_safe']['median_display']} |",
        f"| relevance hesaplanamayan | {rel['n_relevance_missing']} | {MISSING} |",
        "",
        rel["recommendation"],
        "",
        "Golden relevance:",
        "",
    ]
    lines += _md_golden_table(
        payload["golden_views"],
        [("basis", "evidence_basis"), ("relevance", "relevance"), ("kanıt", "used_evidence_title")],
    )
    lines += [
        "---",
        "",
        "## Ölçüm 4 — Claim-strength / abartı (iki ayrı flag)",
        "",
        "Causal kelimeler otomatik abartı **sayılmadı**.",
        "",
        f"- strong_language=True: **{payload['m4']['strong_n']}/{payload['eligible_n']}**",
        f"- causal_language=True: **{payload['m4']['causal_n']}/{payload['eligible_n']}** (ayrı)",
        f"- her iki flag: **{payload['m4']['both_n']}**",
        "",
        "| Grup | eligible_n | would_skip current | dangerous_false_support_rate | collapse_rate |",
        "|---|---:|---:|---|---|",
    ]
    for name, block in payload["m4"]["groups"].items():
        mc = block["current"]
        lines.append(
            f"| {name} | {mc['eligible_n']} | {mc['would_skip_n']} | "
            f"{mc['dangerous_false_support_rate']['display']} | {mc['collapse_rate']['display']} |"
        )
    lines += [
        "",
        "NLI-only threshold (caveat hariç) — enrichment hangi flag'den geliyor:",
        "",
        "| Grup | eligible_n | nli_pass | dangerous_false_support_rate | collapse_rate |",
        "|---|---:|---:|---|---|",
    ]
    for name, block in payload["m4"]["groups"].items():
        mn = block["nli_only"]
        lines.append(
            f"| {name} | {mn['eligible_n']} | {mn['would_skip_n']} | "
            f"{mn['dangerous_false_support_rate']['display']} | {mn['collapse_rate']['display']} |"
        )
    lines += [
        "",
        payload["m4"]["comment"],
        "",
        "Golden strength flags:",
        "",
    ]
    lines += _md_golden_table(
        payload["golden_views"],
        [("strong", "strong_language"), ("strong_hits", "strong_hits"),
         ("causal", "causal_language"), ("causal_hits", "causal_hits")],
    )
    lines += [
        "---",
        "",
        "## Ölçüm 5 — Compound / atomicity",
        "",
        "Şemada `compound_candidate` yok. Eşdeğer: `is_compound_claim(claim_text, reasoning)`.",
        f"compound_tier_mismatch flag: **{payload['m5']['mismatch_n']}** kayıt.",
        "",
        "| Grup | n | safe_skip_precision (current) | dangerous_false_support | mixed_collapse |",
        "|---|---:|---|---:|---:|",
    ]
    for name, block in payload["m5"]["groups"].items():
        mc = block["current"]
        lines.append(
            f"| {name} | {mc['eligible_n']} | {mc['safe_skip_precision']['display']} | "
            f"{mc['dangerous_false_support_count']} | {mc['mixed_collapse_n']} |"
        )
    lines += [
        "",
        "NLI-only:",
        "",
        "| Grup | n | safe_skip_precision | dangerous_false_support | mixed_collapse |",
        "|---|---:|---|---:|---:|",
    ]
    for name, block in payload["m5"]["groups"].items():
        mn = block["nli_only"]
        lines.append(
            f"| {name} | {mn['eligible_n']} | {mn['safe_skip_precision']['display']} | "
            f"{mn['dangerous_false_support_count']} | {mn['mixed_collapse_n']} |"
        )
    lines += [
        "",
        payload["m5"]["comment"],
        "",
        "Golden compound:",
        "",
    ]
    lines += _md_golden_table(
        payload["golden_views"],
        [("compound", "compound_candidate")],
    )
    m6 = payload.get("m6")
    lines += ["---", "", "## Ölçüm 6 — Snippet vs tam metin (yerel NLI ×2)", ""]
    if not m6:
        lines.append(f"Çalıştırılmadı (`--skip-m6`). {MISSING}.")
    else:
        lines += [
            m6["note"],
            "",
            f"Örneklem n={m6['n_sample']} (en yüksek 10 SUPPORTS + 10 REFUTES + 10 direct + "
            f"10 mixed/uncertain + golden; overlap birleşti). **skip_rate production prevalence değil.**",
            "",
            f"IDs: {m6['sample_ids']}",
            "",
            "| Kanıt | n_ran | n_missing | would_skip current | safe_skip_precision | dangerous_false_support | conf p50 |",
            "|---|---:|---:|---:|---|---|---:|",
        ]
        for mode in ("snippet", "full"):
            block = m6[mode]
            cur = block["current_threshold"]
            lines.append(
                f"| {mode} | {block['n_ran']} | {block['n_missing_evidence']} | "
                f"{cur['would_skip_n']} ({cur['skip_rate']['display']}) | "
                f"{cur['safe_skip_precision']['display']} | "
                f"{cur['dangerous_false_support_count']} | {block['confidence']['p50']} |"
            )
        lines += [
            "",
            "NLI-only (caveat hariç) aynı ablasyon:",
            "",
            "| Kanıt | would_skip_nli | safe_skip_precision | dangerous_false_support | collapse | conf mean |",
            "|---|---:|---|---|---|---:|",
        ]
        for mode in ("snippet", "full"):
            block = m6[mode]
            nli = block["nli_only_threshold"]
            lines.append(
                f"| {mode} | {nli['would_skip_n']} ({nli['skip_rate']['display']}) | "
                f"{nli['safe_skip_precision']['display']} | "
                f"{nli['dangerous_false_support_count']} | {nli['collapse_rate']['display']} | "
                f"{block['confidence']['mean']} |"
            )
        lines += ["", payload["m6_comment"], "", "Golden M6 (snippet vs full):", ""]
        lines.append("| id | stored | snippet | full |")
        lines.append("|---|---|---|---|")
        by_id = {r["claim_id"]: r for r in m6["results"]}
        for cid in PRIORITY_GOLDEN:
            rec = by_id.get(cid)
            if not rec:
                lines.append(f"| #{cid} | — | {MISSING} | {MISSING} |")
                continue
            def _fmt(obj):
                if obj in (None, MISSING) or not isinstance(obj, dict):
                    return MISSING
                return f"{obj['nli_label']}@{obj['nli_confidence']} caveat={obj['partial_caveat']}"
            stored = f"{rec.get('stored_nli')}@{rec.get('stored_conf')}"
            lines.append(f"| #{cid} | {stored} | {_fmt(rec.get('snippet_nli'))} | {_fmt(rec.get('full_nli'))} |")
        lines.append("")
    lines += [
        "---",
        "",
        "## Ne değişmedi",
        "",
        "- NLI eşiği 0.75 aynı",
        "- Yeni gate/model önerisi **uygulanmadı**",
        "- Bu rapor bir sonraki kararın girdisi; kendisi karar değil",
        "",
        "## Kaynaklar",
        "",
        "- DB `claims`+`verdicts`",
        "- `data/pending_batches.json` retrieval paketleri",
        "- `data/factcheck_debug.jsonl` cite_source",
        "- Dilim ID: `data/ops_reports/2026-08-18-slice100*-ids.txt`, `2026-08-19-slice154e-ids.txt`",
    ]
    return "\n".join(lines) + "\n"


def _group_metrics(rows: list[dict]) -> dict:
    return {
        "current": skip_metrics(rows, skip_key="would_skip"),
        "nli_only": skip_metrics(rows, skip_key="would_skip_nli"),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", default=str(OUT_DIR))
    parser.add_argument("--skip-m6", action="store_true", help="Yerel NLI ×2 ablasyonunu atla")
    parser.add_argument(
        "--reuse-m6-raw",
        action="store_true",
        help="data/measurement_nli_phase2/m6_raw.json varsa NLI'yı tekrar çalıştırma",
    )
    args = parser.parse_args()
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    print("[nli_phase2] 554 ID yükleniyor…", flush=True)
    ids_554 = load_554_ids()
    print("[nli_phase2] DB + paketler…", flush=True)
    all_rows = load_rows(ids_554)
    eligible = [r for r in all_rows if r.get("escalated") == 1]
    nli_missing = [r for r in eligible if not r.get("nli_available")]
    print(f"[nli_phase2] eligible escalated={len(eligible)} nli_missing={len(nli_missing)}", flush=True)

    close_binary = []
    if CLOSE_JSON.is_file():
        close = json.loads(CLOSE_JSON.read_text(encoding="utf-8"))
        close_binary = [
            d["claim_id"]
            for d in (close.get("all") or {}).get("disagreements") or []
            if d.get("binary")
        ]

    binary_ids = [r["claim_id"] for r in eligible if binary_reverse(r)]
    regression_ids = sorted(set(binary_ids) | set(PRIORITY_GOLDEN))
    regression_rows = [r for r in eligible if r["claim_id"] in regression_ids]

    audit = audit_evidence_tracking(eligible)
    m1 = {
        "current": skip_metrics(eligible, skip_key="would_skip"),
        "nli_only": skip_metrics(eligible, skip_key="would_skip_nli"),
        "nli_missing_n": len(nli_missing),
    }

    m2_specs = [
        ("direct + confidence≥0.75", "direct", 0.75),
        ("direct + confidence≥0.70", "direct", 0.70),
        ("direct + confidence≥0.65", "direct", 0.65),
        ("supportive + confidence≥0.75", "supportive", 0.75),
        ("background + confidence≥0.75", "background", 0.75),
    ]
    m2 = []
    for label, tier, thresh in m2_specs:
        subset = _tier_conf_subset(eligible, tier, thresh)
        tagged = []
        for r in subset:
            copy = dict(r)
            nli_pass = copy.get("nli_label") in ("SUPPORTS", "REFUTES")
            # conf already >= thresh via subset filter
            copy["would_skip_nli"] = nli_pass
            copy["would_skip"] = bool(nli_pass and not copy.get("partial_caveat_effective"))
            tagged.append(copy)
        m2.append({
            "label": label,
            "tier": tier,
            "threshold": thresh,
            "eligible_ids": [r["claim_id"] for r in tagged],
            "nli_label_counts": dict(Counter(r.get("nli_label") for r in tagged)),
            **_group_metrics(tagged),
        })

    print("[nli_phase2] embedder (ölçüm 3)…", flush=True)
    embedder = _get_embedder()
    relevance: dict[int, float | None] = {}
    n_rel_missing = 0
    if embedder is None:
        n_rel_missing = len(eligible)
        for r in eligible:
            relevance[r["claim_id"]] = None
        rel_label = f"proxy relevance (exact cited evidence not tracked); embedder {MISSING}"
    else:
        cited_n = sum(1 for r in eligible if r["evidence_basis"] == "cited_package_item")
        proxy_n = sum(
            1 for r in eligible if r["evidence_basis"] == "proxy_relevance_exact_cited_not_tracked"
        )
        rel_label = (
            f"proxy relevance (exact cited evidence not tracked) "
            f"— cited_package_item={cited_n}, proxy={proxy_n}"
        )
        # Tamamı cited olsa bile etiket: eşleşmeyenler proxy; karışık kohort.
        if proxy_n or cited_n < len(eligible):
            rel_label = (
                "proxy relevance (exact cited evidence not tracked) "
                f"karışık: cited_package_item={cited_n}/{len(eligible)}, "
                f"proxy={proxy_n}/{len(eligible)}"
            )
        else:
            rel_label = "cited_package_item cosine (source_url paketle eşleşti)"
        for r in eligible:
            text = r.get("used_evidence_text") or ""
            if not text or r.get("evidence_basis") == MISSING:
                relevance[r["claim_id"]] = None
                n_rel_missing += 1
                continue
            relevance[r["claim_id"]] = cosine_sim(embedder, r["claim_text"], text)
            if relevance[r["claim_id"]] is None:
                n_rel_missing += 1

    def _group_rel(pred) -> dict:
        vals = [
            relevance[r["claim_id"]]
            for r in eligible
            if pred(r) and relevance.get(r["claim_id"]) is not None
        ]
        med = _median(vals)
        return {
            "n": len(vals),
            "median": med,
            "median_display": f"{med:.3f}" if med is not None else MISSING,
        }

    def _is_safe(r, key="would_skip"):
        return bool(r.get(key)) and r.get("same_direction_binary") and r.get("final_verdict") in BINARY_VERDICTS

    false_skip_cur = _group_rel(lambda r: r.get("would_skip") and not _is_safe(r, "would_skip"))
    safe_skip_cur = _group_rel(lambda r: _is_safe(r, "would_skip"))
    nli_false = _group_rel(lambda r: r.get("would_skip_nli") and not _is_safe(r, "would_skip_nli"))
    nli_safe = _group_rel(lambda r: _is_safe(r, "would_skip_nli"))

    rec_txt = (
        "Current-threshold would_skip boş/çok küçük olduğu için relevance eşiği R "
        "önerisi **veri-tabanlı olarak kurulamaz** (false_skip vs safe_skip ayrımı yok). "
        "Uydurulmadı."
    )
    fs_med = nli_false.get("median")
    ss_med = nli_safe.get("median")
    if (
        nli_false["n"] >= 1
        and nli_safe["n"] >= 1
        and fs_med is not None
        and ss_med is not None
        and (ss_med - fs_med) >= 0.20
    ):
        rec_txt = (
            f"nli_threshold_pass kesitinde false medyan={fs_med:.3f}, safe medyan={ss_med:.3f}. "
            f"Net ayrım var; bir sonraki kararda relevance eşiği R ≈ "
            f"{(fs_med + ss_med) / 2:.2f} civarı **tartışılabilir** "
            f"(uygulanmadı). Current-threshold grupları hâlâ boş — R production'a girmez."
        )
    elif nli_false["n"] >= 1 and nli_safe["n"] >= 1:
        rec_txt = (
            f"nli_threshold_pass false medyan={nli_false['median_display']}, "
            f"safe medyan={nli_safe['median_display']}. Ayrım net değil; R önerilmedi."
        )

    m3 = {
        "label": rel_label,
        "embedder": "paraphrase-multilingual-MiniLM-L12-v2" if embedder else MISSING,
        "false_skip": false_skip_cur,
        "safe_skip": safe_skip_cur,
        "nli_pass_false": nli_false,
        "nli_pass_safe": nli_safe,
        "n_relevance_missing": n_rel_missing,
        "recommendation": rec_txt,
        "per_claim": {str(k): v for k, v in relevance.items()},
    }

    strong_yes = [r for r in eligible if r.get("strong_language")]
    strong_no = [r for r in eligible if not r.get("strong_language")]
    causal_yes = [r for r in eligible if r.get("causal_language")]
    causal_no = [r for r in eligible if not r.get("causal_language")]
    both = [r for r in eligible if r.get("strong_language") and r.get("causal_language")]
    m4 = {
        "strong_n": len(strong_yes),
        "causal_n": len(causal_yes),
        "both_n": len(both),
        "groups": {
            "strong_language=True": _group_metrics(strong_yes),
            "strong_language=False": _group_metrics(strong_no),
            "causal_language=True": _group_metrics(causal_yes),
            "causal_language=False": _group_metrics(causal_no),
        },
        "comment": (
            "Current-threshold skip neredeyse boş olduğu için rate karşılaştırması "
            f"{MISSING} / anlamsız kalabilir. Enrichment nli-only kesitinde ayrı ayrı "
            "okunmalı; causal≠strong."
        ),
    }

    compound_yes = [r for r in eligible if r.get("compound_candidate")]
    compound_no = [r for r in eligible if not r.get("compound_candidate")]
    m5 = {
        "field": "is_compound_claim(claim_text, reasoning)",
        "mismatch_n": sum(1 for r in eligible if r.get("compound_tier_mismatch")),
        "groups": {
            "compound": _group_metrics(compound_yes),
            "atomic": _group_metrics(compound_no),
        },
        "comment": (
            "compound_candidate kolonu yok; heuristic kullanıldı. "
            "Current-threshold skip boşsa mixed_collapse karşılaştırması nli-only kesitine bakılır."
        ),
    }

    golden_views = [
        golden_view(r, relevance.get(r["claim_id"]))
        for r in sorted(regression_rows, key=lambda x: (x["claim_id"] not in PRIORITY_GOLDEN, x["claim_id"]))
    ]

    m6 = None
    m6_comment = ""
    raw_m6_path = out_dir / "m6_raw.json"
    if not args.skip_m6:
        sample_ids = select_m6(eligible, set(regression_ids))
        by_id = {r["claim_id"]: r for r in eligible}
        reused = False
        if args.reuse_m6_raw and raw_m6_path.is_file():
            raw = json.loads(raw_m6_path.read_text(encoding="utf-8"))
            if raw.get("sample_ids") == sample_ids and raw.get("results"):
                print("[nli_phase2] ölçüm 6 ham kayıt yeniden kullanılıyor", flush=True)
                m6 = finalize_m6(raw["sample_ids"], raw["results"])
                reused = True
        if not reused:
            print(f"[nli_phase2] ölçüm 6 stratified n={len(sample_ids)} — yerel NLI iki kez", flush=True)
            m6 = run_m6(by_id, sample_ids)
        sn = m6["snippet"]["current_threshold"]
        fu = m6["full"]["current_threshold"]
        by_m6 = {r["claim_id"]: r for r in m6["results"]}
        g1282 = by_m6.get(1282) or {}
        g905 = by_m6.get(905) or {}
        m6_comment = (
            f"Snippet vs full current-threshold would_skip {sn['would_skip_n']} vs {fu['would_skip_n']}; "
            f"precision {sn['safe_skip_precision']['display']} vs {fu['safe_skip_precision']['display']}. "
            "Bu fark prevalence değil; aynı biased örneklemde kanıt kesiti etkisi. "
            f"#1282 tek parça (cited item) rerun'da caveat=False "
            f"(snippet {((g1282.get('snippet_nli') or {}).get('partial_caveat'))}, "
            f"full {((g1282.get('full_nli') or {}).get('partial_caveat'))}) — "
            "production caveat parça 2'dedir; best-snippet-of-top-item kapısı #1282'yi "
            "NLI-only'e sokardı. #905 snippet SUPPORTS@0.946 caveat=False: Claude tartışmalı "
            "iken tek-parça NLI yüksek güvenle skip ederdi. Eşik/kural değiştirilmedi."
        )
    else:
        m6_comment = f"M6 atlandı. {MISSING}."

    payload = {
        "methodology": {
            "final_verdict_role": "expensive-stage reference, not ground truth",
            "metrics": "NLI agreement/safe-skip against current second-stage verdict",
            "threshold": NLI_THRESHOLD,
            "would_skip_current": "SUPPORTS/REFUTES AND conf>=0.75 AND NOT partial_caveat",
            "would_skip_nli": "SUPPORTS/REFUTES AND conf>=0.75",
            "claim_865": "conf 0.746 < 0.75 → not would_skip; not in dangerous_false_support",
            "claim_1282": "conf 0.808 but caveat → not would_skip; successful catch",
            "partial_caveat_db_fields": "absent on 554 rows; not an error",
            "compound_field": "is_compound_claim — no compound_candidate column",
        },
        "eligible_n": len(eligible),
        "nli_missing_n": len(nli_missing),
        "binary_reverse_n": len(binary_ids),
        "binary_reverse_ids": binary_ids,
        "close_binary_n": len(close_binary),
        "close_binary_ids": close_binary,
        "regression_n": len(regression_ids),
        "regression_ids": regression_ids,
        "audit": audit,
        "m1": m1,
        "m2": m2,
        "m3": m3,
        "m4": m4,
        "m5": m5,
        "m6": m6,
        "m6_comment": m6_comment,
        "golden_views": golden_views,
        "cohort_extract": [
            {
                "claim_id": r["claim_id"],
                "nli_label": r["nli_label"],
                "nli_confidence": r["nli_confidence"],
                "final_verdict": r["final_verdict"],
                "initial_risk": r["initial_risk"],
                "category": r["category"],
                "specificity_tier": r["specificity_tier"],
                "compound_candidate": r["compound_candidate"],
                "would_skip_nli": r["would_skip_nli"],
                "would_skip": r["would_skip"],
                "nli_threshold_pass": r["nli_threshold_pass"],
            }
            for r in eligible
        ],
    }

    report = render_report(payload)
    md_path = out_dir / "report.md"
    json_path = out_dir / "metrics.json"
    md_path.write_text(report, encoding="utf-8")

    def _default(o):
        if isinstance(o, float) and (math.isnan(o) or math.isinf(o)):
            return None
        raise TypeError(type(o).__name__)

    json_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=_default),
        encoding="utf-8",
    )
    print(f"[nli_phase2] rapor -> {md_path}", flush=True)
    print(f"[nli_phase2] json  -> {json_path}", flush=True)
    print(
        f"[nli_phase2] would_skip current={m1['current']['would_skip_n']}/{len(eligible)} "
        f"nli_only={m1['nli_only']['would_skip_n']}/{len(eligible)}",
        flush=True,
    )


if __name__ == "__main__":
    main()
