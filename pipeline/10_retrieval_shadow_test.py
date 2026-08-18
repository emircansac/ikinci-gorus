"""
Gölge test: no_evidence_found iddialarda retrieval kalitesini ölçer.

Kullanım:
    ./venv/bin/python pipeline/10_retrieval_shadow_test.py
    ./venv/bin/python pipeline/10_retrieval_shadow_test.py --claim-ids 652,654,663,681,689,695
    ./venv/bin/python pipeline/10_retrieval_shadow_test.py --v2
    ./venv/bin/python pipeline/10_retrieval_shadow_test.py --cascade
    ./venv/bin/python pipeline/10_retrieval_shadow_test.py --live-serper
    ./venv/bin/python pipeline/10_retrieval_shadow_test.py --live-serper --claim-ids 752,663,745,653,746,667
"""
import argparse
import json
import os
import sys
from pathlib import Path

import requests

sys.path.append(str(Path(__file__).parent.parent))
from utils.db import get_conn
from utils.evidence_retrieval import (
    CANDIDATE_POOL_SIZE,
    ESCALATE_PACKAGE_SIZE,
    FINAL_EVIDENCE_COUNT,
    PUBMED_EFETCH,
    apply_key_term_filter,
    assess_evidence_sufficiency,
    collect_native_candidates,
    expand_query_variants,
    europepmc_candidates,
    medlineplus_candidates,
    parse_pubmed_efetch_xml,
    pubmed_search_hit_count,
    retrieve_guideline_snippets,
    retrieve_hybrid_evidence,
    retrieve_serper_evidence,
    _dense_rerank,
    _merge_candidates,
    _pubmed_candidates_from_query,
)
from utils.factcheck_calibrate import infer_source_tier
from utils.nutrition_lookup import is_nutrition_quantity_claim, lookup_nutrition_evidence

DEFAULT_SAMPLE = [652, 654, 663, 681, 689, 695]
# Bu turun no_evidence / NLI<0.75 kümesinden 8 iddia (MADDE 1 URL'leri dahil)
V2_SAMPLE = [745, 752, 653, 663, 652, 746, 667, 678]
# 11_nli_shadow_test.DEFAULT_VIDEO_IDS ile aynı — escalated kohort
NLI_COHORT_VIDEO_IDS = ("P4m9F9mykQ8", "odZgEDFDmbE", "bZsorXWeLhM")
LIVE_SERPER_DEFAULT = [662, 684, 686, 746, 752, 663]
OUT_PATH = Path(__file__).parent.parent / "data" / "retrieval_shadow_test.json"
OUT_PATH_V2 = Path(__file__).parent.parent / "data" / "retrieval_shadow_test_v2.json"
OUT_PATH_CASCADE = Path(__file__).parent.parent / "data" / "retrieval_shadow_test_cascade.json"
OUT_PATH_LIVE_SERPER = Path(__file__).parent.parent / "data" / "retrieval_shadow_test_live_serper.json"

SERPER_PROXY_LABEL = (
    "gerçek Serper sonucu değil, Claude'un geçmiş web_search seçimine dayalı "
    "yaklaşık üst sınır"
)
WEB_SEARCH_CITE_FLAGS = ("web_search_override", "web_search_only")
DEBUG_LOG = Path(__file__).parent.parent / "data" / "factcheck_debug.jsonl"


def _load_cite_sources() -> dict[int, str]:
    """Son factcheck_debug.jsonl kaydındaki cite_source (DB flags bu kohortta boş)."""
    out: dict[int, str] = {}
    if not DEBUG_LOG.exists():
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
            cite = rec.get("cite_source")
            if cite is None:
                cite = (rec.get("calibrated") or {}).get("cite_source")
            if cid is None or not cite:
                continue
            try:
                out[int(cid)] = str(cite)
            except (TypeError, ValueError):
                continue
    return out

# MADDE 2 canlı XML kanıtı: bilinen PublicationType etiketleri
M2_PROOF_PMIDS = {
    "37214237": "systematic_review",  # Meta-Analysis
    "42583491": "case_report",        # Case Reports
    "23182013": "primary_study",      # Journal Article
}


def _load_claims(conn, claim_ids: list[int]) -> list[dict]:
    placeholders = ",".join("?" * len(claim_ids))
    rows = conn.execute(f"""
        SELECT c.claim_id, c.claim_text, c.search_query_en, c.category, c.initial_risk,
               c.video_id,
               vr.source_url AS stored_source_url,
               vr.source_tier AS stored_source_tier,
               vr.final_verdict AS stored_verdict,
               vr.confidence AS stored_confidence,
               vr.nli_evidence_snippet,
               vr.nli_confidence,
               vr.calibration_flags,
               vr.escalated
        FROM claims c
        LEFT JOIN verdicts vr ON vr.claim_id = c.claim_id
        WHERE c.claim_id IN ({placeholders})
        ORDER BY c.claim_id
    """, claim_ids).fetchall()
    return [dict(r) for r in rows]


def _load_nli_cohort(conn, video_ids: tuple[str, ...] = NLI_COHORT_VIDEO_IDS) -> list[dict]:
    """11_nli_shadow_test._load_cohort ile aynı filtre: escalated=1 + final_verdict."""
    placeholders = ",".join("?" * len(video_ids))
    rows = conn.execute(f"""
        SELECT c.claim_id, c.claim_text, c.search_query_en, c.category, c.initial_risk,
               c.video_id,
               vr.source_url AS stored_source_url,
               vr.source_tier AS stored_source_tier,
               vr.final_verdict AS stored_verdict,
               vr.confidence AS stored_confidence,
               vr.nli_evidence_snippet,
               vr.nli_confidence,
               vr.calibration_flags,
               vr.escalated
        FROM claims c
        JOIN verdicts vr ON vr.claim_id = c.claim_id
        WHERE c.video_id IN ({placeholders})
          AND vr.escalated = 1 AND vr.final_verdict IS NOT NULL
        ORDER BY c.claim_id
    """, video_ids).fetchall()
    cites = _load_cite_sources()
    out = []
    for r in rows:
        d = dict(r)
        d["cite_source"] = cites.get(d["claim_id"])
        out.append(d)
    return out


def evaluate_claim(row: dict) -> dict:
    cid = row["claim_id"]
    text = row["claim_text"]
    query = row["search_query_en"] or ""
    category = row["category"]

    baseline = pubmed_search_hit_count(query) if query else {"hits": 0, "with_abstract": 0}

    mesh_best = {"hits": 0, "with_abstract": 0, "query": ""}
    for variant in expand_query_variants(query):
        hit = pubmed_search_hit_count(variant)
        if hit.get("with_abstract", 0) > mesh_best.get("with_abstract", 0):
            mesh_best = {**hit, "query": variant}

    guidelines = retrieve_guideline_snippets(query, category, claim_text=text)
    nutrition = lookup_nutrition_evidence(text) if is_nutrition_quantity_claim(text) else []

    hybrid, path, _meta = retrieve_hybrid_evidence(text, query, category)

    found = bool(hybrid)
    return {
        "claim_id": cid,
        "claim_text": text[:120],
        "search_query_en": query,
        "category": category,
        "initial_risk": row["initial_risk"],
        "baseline_pubmed": baseline,
        "best_mesh_variant": mesh_best,
        "guideline_snippets": len(guidelines),
        "nutrition_evidence": len(nutrition),
        "hybrid_path": path,
        "hybrid_evidence_count": len(hybrid),
        "retrieval_would_succeed": found,
        "sample_title": hybrid[0]["title"][:80] if hybrid else None,
    }


def _summarize(items: list[dict], n: int = 3) -> list[dict]:
    out = []
    for i in items[:n]:
        out.append({
            "title": (i.get("title") or "")[:100],
            "url": i.get("url"),
            "source_tier": i.get("source_tier") or i.get("source"),
            "provider": i.get("provider"),
            "publication_types": i.get("publication_types") or [],
        })
    return out


def _pubmed_pool(query: str) -> list[dict]:
    candidates: list[dict] = []
    if not query:
        return candidates
    for variant in expand_query_variants(query):
        batch = _pubmed_candidates_from_query(variant, retmax=CANDIDATE_POOL_SIZE)
        if batch:
            candidates = _merge_candidates(candidates, batch)
        if len(candidates) >= CANDIDATE_POOL_SIZE:
            break
    return candidates


def _rank_filtered(claim_text: str, candidates: list[dict], query: str) -> list[dict]:
    if not candidates:
        return []
    filtered, _meta = apply_key_term_filter(candidates, query, claim_text)
    return _dense_rerank(claim_text, filtered, FINAL_EVIDENCE_COUNT)


def evaluate_claim_v2(row: dict) -> dict:
    """Dört maddeyi sırayla ekleyerek before/after üretir."""
    text = row["claim_text"]
    query = row["search_query_en"] or ""
    stored_url = row.get("stored_source_url") or ""
    stored_tier = row.get("stored_source_tier")

    m1_after = infer_source_tier(stored_url) if stored_url else None
    madde1 = {
        "stored_url": stored_url,
        "stored_source_tier_before": stored_tier,
        "url_tier_after_allowlist": m1_after,
        "changed": bool(stored_url) and stored_tier != m1_after,
    }

    pubmed_cands = _pubmed_pool(query)
    ranked_m2 = _rank_filtered(text, pubmed_cands, query)
    madde2 = {
        "candidate_count": len(pubmed_cands),
        "evidence_count": len(ranked_m2),
        "tiers": [e.get("source_tier") for e in ranked_m2],
        "sample": _summarize(ranked_m2),
    }

    epmc_cands = europepmc_candidates(query, retmax=CANDIDATE_POOL_SIZE) if query else []
    merged_m3 = _merge_candidates(pubmed_cands, epmc_cands)
    ranked_m3 = _rank_filtered(text, merged_m3, query)
    madde3 = {
        "europepmc_raw_count": len(epmc_cands),
        "merged_count": len(merged_m3),
        "evidence_count": len(ranked_m3),
        "new_vs_m2": len(ranked_m3) > 0 and len(ranked_m2) == 0,
        "tiers": [e.get("source_tier") for e in ranked_m3],
        "providers": [e.get("provider") for e in ranked_m3],
        "sample": _summarize(ranked_m3),
    }

    mp_cands = medlineplus_candidates(query, retmax=5) if query else []
    merged_m4 = _merge_candidates(merged_m3, mp_cands)
    ranked_m4 = _rank_filtered(text, merged_m4, query)
    if not ranked_m4:
        guidelines = retrieve_guideline_snippets(query, row.get("category"), claim_text=text)
        ranked_m4 = guidelines[:FINAL_EVIDENCE_COUNT]
    madde4 = {
        "medlineplus_raw_count": len(mp_cands),
        "merged_count": len(merged_m4),
        "evidence_count": len(ranked_m4),
        "new_vs_m3": len(ranked_m4) > 0 and len(ranked_m3) == 0,
        "medlineplus_in_top": any(e.get("provider") == "medlineplus" for e in ranked_m4),
        "top_tier_changed": (
            ([e.get("source_tier") for e in ranked_m3[:1]] != [e.get("source_tier") for e in ranked_m4[:1]])
            if (ranked_m3 or ranked_m4) else False
        ),
        "tiers": [e.get("source_tier") for e in ranked_m4],
        "providers": [e.get("provider") for e in ranked_m4],
        "sample": _summarize(ranked_m4),
    }

    return {
        "claim_id": row["claim_id"],
        "claim_text": (text or "")[:140],
        "search_query_en": query,
        "stored_verdict": row.get("stored_verdict"),
        "stored_confidence": row.get("stored_confidence"),
        "was_no_evidence": "kanıt bulunamad" in ((row.get("nli_evidence_snippet") or "").lower()),
        "nli_confidence": row.get("nli_confidence"),
        "madde1_allowlist": madde1,
        "madde2_pubmed_pubtype": madde2,
        "madde3_europepmc": madde3,
        "madde4_medlineplus": madde4,
    }


def prove_publication_types_live() -> dict:
    """Üç gerçek PMID'nin EFetch XML PublicationType ↔ source_tier eşlemesi."""
    pmids = list(M2_PROOF_PMIDS)
    r = requests.get(
        PUBMED_EFETCH,
        params={"db": "pubmed", "id": ",".join(pmids), "retmode": "xml", "rettype": "abstract"},
        timeout=20,
    )
    r.raise_for_status()
    parsed = parse_pubmed_efetch_xml(r.text)
    proofs = []
    for pmid, expected in M2_PROOF_PMIDS.items():
        rec = parsed.get(pmid) or {}
        proofs.append({
            "pmid": pmid,
            "url": rec.get("url"),
            "title": (rec.get("title") or "")[:120],
            "publication_types_from_xml": rec.get("publication_types"),
            "source_tier": rec.get("source_tier"),
            "expected": expected,
            "match": rec.get("source_tier") == expected,
        })
    return {"efetch_pmids": pmids, "proofs": proofs}


def _v2_delta(results: list[dict]) -> dict:
    m1_changed = [r["claim_id"] for r in results if r["madde1_allowlist"]["changed"]]
    m2_found = [r["claim_id"] for r in results if r["madde2_pubmed_pubtype"]["evidence_count"] > 0]
    m3_new = [r["claim_id"] for r in results if r["madde3_europepmc"]["new_vs_m2"]]
    m3_found = [r["claim_id"] for r in results if r["madde3_europepmc"]["evidence_count"] > 0]
    m4_new = [r["claim_id"] for r in results if r["madde4_medlineplus"]["new_vs_m3"]]
    m4_found = [r["claim_id"] for r in results if r["madde4_medlineplus"]["evidence_count"] > 0]
    m4_in_top = [r["claim_id"] for r in results if r["madde4_medlineplus"].get("medlineplus_in_top")]
    m4_tier = [r["claim_id"] for r in results if r["madde4_medlineplus"].get("top_tier_changed")]
    m4_hits = [r["claim_id"] for r in results if r["madde4_medlineplus"].get("medlineplus_raw_count", 0) > 0]
    return {
        "madde1_url_tier_changed": m1_changed,
        "madde2_pubmed_evidence": m2_found,
        "madde3_newly_found_via_europepmc": m3_new,
        "madde3_has_evidence": m3_found,
        "madde4_newly_found_via_medlineplus": m4_new,
        "madde4_medlineplus_api_hits": m4_hits,
        "madde4_medlineplus_in_rerank_top": m4_in_top,
        "madde4_top_tier_changed": m4_tier,
        "madde4_has_evidence": m4_found,
        "final_with_evidence": len(m4_found),
        "total": len(results),
    }


def _sufficiency_dict(suff) -> dict:
    return {
        "sufficient": suff.sufficient,
        "relevance_ok": suff.relevance_ok,
        "quality_ok": suff.quality_ok,
        "reason": suff.reason,
        "best_tier": suff.best_tier,
        "kept_count": suff.kept_count,
        "max_rerank_score": suff.max_rerank_score,
    }


def _used_web_search(flags: str | None, cite_source: str | None = None) -> bool:
    if cite_source in WEB_SEARCH_CITE_FLAGS:
        return True
    parts = {p.strip() for p in (flags or "").split(",") if p.strip()}
    return any(f in parts for f in WEB_SEARCH_CITE_FLAGS)


def evaluate_cascade_offline(row: dict) -> dict:
    """A: native canlı + Serper katmanı DB source_url proxy (karar değil)."""
    text = row["claim_text"] or ""
    query = row.get("search_query_en") or ""
    if is_nutrition_quantity_claim(text):
        nut = lookup_nutrition_evidence(text)
        if nut:
            native = [{**x, "retrieval_tier": x.get("retrieval_tier") or "native",
                       "evidence_content_type": x.get("evidence_content_type") or "abstract"}
                      for x in nut]
            native_suff = assess_evidence_sufficiency(native, text, query)
            return _cascade_row(row, native, ["nutrition_db"], native_suff)
    native, path_parts = collect_native_candidates(text, query, row.get("category"))
    native_suff = assess_evidence_sufficiency(native, text, query)
    return _cascade_row(row, native, path_parts, native_suff)


def _cascade_row(row, native, path_parts, native_suff) -> dict:
    text = row["claim_text"] or ""
    query = row.get("search_query_en") or ""
    serper_invoked = not native_suff.sufficient

    proxy_suff = None
    proxy_url = (row.get("stored_source_url") or "").strip()
    if serper_invoked:
        if proxy_url:
            proxy_cand = [{
                "title": proxy_url,
                "abstract": (row.get("nli_evidence_snippet") or "")[:800],
                "url": proxy_url,
                "retrieval_tier": "serper",
                "evidence_content_type": "search_snippet",
            }]
            merged = _merge_candidates(native, proxy_cand)
            proxy_suff = assess_evidence_sufficiency(merged, text, query)
        else:
            proxy_suff = assess_evidence_sufficiency([], text, query)

    return {
        "claim_id": row["claim_id"],
        "video_id": row.get("video_id"),
        "claim_text": text[:140],
        "search_query_en": query,
        "native_path": "+".join(path_parts) if path_parts else "none",
        "native_candidate_count": len(native),
        "native": _sufficiency_dict(native_suff),
        "serper_invoked": serper_invoked,
        "proxy_source_url": proxy_url or None,
        "proxy": _sufficiency_dict(proxy_suff) if proxy_suff is not None else None,
        "baseline_used_web_search": _used_web_search(
            row.get("calibration_flags"), row.get("cite_source")
        ),
        "cite_source": row.get("cite_source"),
        "stored_source_tier": row.get("stored_source_tier"),
    }


def _cascade_summary(results: list[dict]) -> dict:
    n = len(results)
    native_sufficient = sum(1 for r in results if r["native"]["sufficient"])
    serper_invoked = sum(1 for r in results if r["serper_invoked"])
    proxy_sufficient = sum(
        1 for r in results
        if r["serper_invoked"] and r.get("proxy") and r["proxy"]["sufficient"]
    )
    still_need_web = sum(
        1 for r in results
        if r["serper_invoked"] and not (r.get("proxy") and r["proxy"]["sufficient"])
    )
    cites_present = sum(1 for r in results if r.get("cite_source"))
    web_from_cite = sum(
        1 for r in results
        if r.get("cite_source") in WEB_SEARCH_CITE_FLAGS
    )
    # Bu kohort escalated=1: Claude web_search aracı her çağrıda açık.
    # cite_source seyrekse baseline = 1.0 (araç-açık escalate oranı).
    if cites_present >= max(10, n // 2):
        baseline_rate = round(web_from_cite / n, 3)
        baseline_note = (
            f"cite_source debug log ({cites_present}/{n}); "
            "web_search_override|web_search_only payı"
        )
    else:
        baseline_rate = 1.0
        baseline_note = (
            "Kohort filtresi escalated=1 — Claude web_search aracı her iddiada açık "
            f"(cite_source yalnızca {cites_present}/{n} kayıtta dolu, oran için kullanılmadı)."
        )

    rescue_proxy = (proxy_sufficient / serper_invoked) if serper_invoked else None

    examples = {
        "relevance_ok_false": next(
            (r["claim_id"] for r in results if r["native"]["relevance_ok"] is False),
            None,
        ),
        "quality_ok_false": next(
            (r["claim_id"] for r in results
             if r["native"]["relevance_ok"] and not r["native"]["quality_ok"]),
            None,
        ),
        "native_sufficient_true": next(
            (r["claim_id"] for r in results if r["native"]["sufficient"]),
            None,
        ),
    }
    example_rows = []
    for key, cid in examples.items():
        if cid is None:
            continue
        row = next(r for r in results if r["claim_id"] == cid)
        example_rows.append({"kind": key, **row})

    return {
        "n": n,
        "native_sufficient": native_sufficient,
        "serper_invoked": serper_invoked,
        "serper_sufficient_offline_proxy": proxy_sufficient,
        "serper_rescue_rate_offline_proxy": (
            round(rescue_proxy, 3) if rescue_proxy is not None else None
        ),
        "serper_rescue_rate_offline_proxy_label": SERPER_PROXY_LABEL,
        "decision_note": (
            "Bu proxy karar verici değildir. Asıl serper_rescue_rate için "
            "--live-serper (B) bakın."
        ),
        "baseline_web_search_escalation_rate": baseline_rate,
        "baseline_web_search_escalation_note": baseline_note,
        "new_web_search_escalation_rate": {
            "rate": round(still_need_web / n, 3) if n else None,
            "count": still_need_web,
            "estimated_from_offline_proxy": True,
            "note": (
                "Karar verici değil; B'deki canlı serper_rescue_rate ile çelişirse B geçerli."
            ),
        },
        "sufficiency_examples": example_rows,
    }


def _print_cascade(summary: dict, out_path: Path) -> None:
    print("[cascade A] kohort (offline proxy — KARAR DEĞİL)")
    print(f"  n={summary['n']} native_sufficient={summary['native_sufficient']} "
          f"serper_invoked={summary['serper_invoked']}")
    print(f"  baseline_web_search_escalation_rate={summary['baseline_web_search_escalation_rate']}")
    if summary.get("baseline_web_search_escalation_note"):
        print(f"  baseline_note: {summary['baseline_web_search_escalation_note']}")
    print(f"  serper_rescue_rate_offline_proxy={summary['serper_rescue_rate_offline_proxy']}")
    print(f"  ETİKET: {summary['serper_rescue_rate_offline_proxy_label']}")
    print(f"  {summary['decision_note']}")
    est = summary["new_web_search_escalation_rate"]
    print(f"  new_web_search_escalation_rate={est['rate']} "
          f"(estimated_from_offline_proxy={est['estimated_from_offline_proxy']})")
    for ex in summary.get("sufficiency_examples") or []:
        nat = ex["native"]
        print(f"  örnek [{ex['kind']}] claim_id={ex['claim_id']} "
              f"relevance_ok={nat['relevance_ok']} quality_ok={nat['quality_ok']} "
              f"sufficient={nat['sufficient']} reason={nat['reason']}")
    print(f"[cascade A] rapor -> {out_path}")


def evaluate_live_serper(row: dict) -> dict:
    """B: native assess → gerçek Serper API → aynı assess. Karar verici sayı."""
    text = row["claim_text"] or ""
    query = row.get("search_query_en") or ""
    native, path_parts = collect_native_candidates(text, query, row.get("category"))
    native_suff = assess_evidence_sufficiency(native, text, query)
    serper_invoked = not native_suff.sufficient

    serper_items: list[dict] = []
    merged_suff = None
    path = list(path_parts)
    if serper_invoked:
        serper_items = retrieve_serper_evidence(query)
        merged = _merge_candidates(native, serper_items)
        merged_suff = assess_evidence_sufficiency(merged, text, query)
        if serper_items:
            path.append("serper")
        pool = merged
    else:
        pool = native

    filtered, _meta = apply_key_term_filter(pool, query, text)
    package = _dense_rerank(text, filtered or pool, ESCALATE_PACKAGE_SIZE)
    hy_path = "+".join(path) if path else "none"
    field_proof = []
    for item in package[:5]:
        field_proof.append({
            "title": (item.get("title") or "")[:100],
            "url": item.get("url"),
            "retrieval_tier": item.get("retrieval_tier"),
            "source_tier": item.get("source_tier") or item.get("source"),
            "evidence_content_type": item.get("evidence_content_type"),
            "provider": item.get("provider"),
        })

    serper_sufficient = bool(serper_invoked and merged_suff and merged_suff.sufficient)

    return {
        "claim_id": row["claim_id"],
        "video_id": row.get("video_id"),
        "claim_text": text[:140],
        "search_query_en": query,
        "native_path": "+".join(path_parts) if path_parts else "none",
        "native": _sufficiency_dict(native_suff),
        "serper_invoked": serper_invoked,
        "serper_raw_count": len(serper_items),
        "after_serper": _sufficiency_dict(merged_suff) if merged_suff is not None else None,
        "serper_sufficient": serper_sufficient,
        "hybrid_path": hy_path,
        "package_field_proof": field_proof,
        "fields_ok": all(
            p.get("retrieval_tier") in ("native", "serper")
            and p.get("source_tier")
            and p.get("evidence_content_type") in ("abstract", "search_snippet")
            for p in field_proof
        ) if field_proof else False,
    }


def _live_serper_summary(results: list[dict]) -> dict:
    n = len(results)
    native_sufficient = sum(1 for r in results if r["native"]["sufficient"])
    serper_invoked = sum(1 for r in results if r["serper_invoked"])
    serper_sufficient = sum(1 for r in results if r["serper_invoked"] and r["serper_sufficient"])
    key_present = bool((os.environ.get("SERPER_API_KEY") or "").strip())
    if not key_present:
        rescue = None
        decision_note = (
            "SERPER_API_KEY yok — serper_rescue_rate ölçülemedi (0 yazılmaz). "
            ".env'e anahtarı ekleyip --live-serper tekrar çalıştırın. "
            "A'daki offline proxy karar değildir."
        )
    else:
        rescue = (serper_sufficient / serper_invoked) if serper_invoked else None
        decision_note = (
            "Asıl karar verici sayı: canlı serper_rescue_rate. "
            "A'daki offline proxy üst sınırdır, karar değildir."
        )
    return {
        "n": n,
        "native_sufficient": native_sufficient,
        "serper_invoked": serper_invoked,
        "serper_sufficient": serper_sufficient if key_present else None,
        "serper_rescue_rate": round(rescue, 3) if rescue is not None else None,
        "serper_api_key_present": key_present,
        "decision_note": decision_note,
        "fields_ok_count": sum(1 for r in results if r.get("fields_ok")),
    }


def _print_live_serper(summary: dict, results: list[dict], out_path: Path) -> None:
    print("[live B] KARAR — canlı Serper")
    print(f"  n={summary['n']} native_sufficient={summary['native_sufficient']} "
          f"serper_invoked={summary['serper_invoked']} "
          f"serper_sufficient={summary['serper_sufficient']}")
    print(f"  serper_rescue_rate={summary['serper_rescue_rate']}"
          f"{'' if summary.get('serper_api_key_present') else '  (ölçülemedi, anahtar yok)'}")
    print(f"  {summary['decision_note']}")
    for r in results:
        nat = r["native"]
        print(
            f"  [{r['claim_id']}] native_suff={nat['sufficient']} "
            f"rel={nat['relevance_ok']} qual={nat['quality_ok']} "
            f"reason={nat['reason']} serper_invoked={r['serper_invoked']} "
            f"serper_suff={r['serper_sufficient']} path={r['hybrid_path']}"
        )
        for p in (r.get("package_field_proof") or [])[:3]:
            print(
                f"      tier={p.get('retrieval_tier')} source={p.get('source_tier')} "
                f"content={p.get('evidence_content_type')} url={p.get('url')}"
            )
    print(f"[live B] rapor -> {out_path}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--claim-ids", default="")
    ap.add_argument("--from-no-evidence", action="store_true",
                    help="odZg no_evidence snippet'li tüm iddiaları test et")
    ap.add_argument("--v2", action="store_true",
                    help="4 maddeyi sırayla ölç, data/retrieval_shadow_test_v2.json yaz")
    ap.add_argument("--cascade", action="store_true",
                    help="NLI kohortu (~104) native+offline Serper proxy (A, karar değil)")
    ap.add_argument("--live-serper", action="store_true",
                    help="5-6 iddiada canlı Serper zinciri (B, karar verici serper_rescue_rate)")
    args = ap.parse_args()

    conn = get_conn()

    if args.cascade:
        claims = _load_nli_cohort(conn)
        conn.close()
        print(f"[cascade A] kohort: {len(claims)} escalated iddia "
              f"({', '.join(NLI_COHORT_VIDEO_IDS)})")
        results = [evaluate_cascade_offline(c) for c in claims]
        summary = _cascade_summary(results)
        report = {
            "mode": "cascade_offline_proxy",
            "video_ids": list(NLI_COHORT_VIDEO_IDS),
            "cohort_filter": "escalated=1 AND final_verdict IS NOT NULL",
            "serper_layer": "offline_proxy_from_stored_source_url",
            "serper_rescue_rate_offline_proxy_label": SERPER_PROXY_LABEL,
            "summary": summary,
            "results": results,
        }
        OUT_PATH_CASCADE.parent.mkdir(parents=True, exist_ok=True)
        OUT_PATH_CASCADE.write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        _print_cascade(summary, OUT_PATH_CASCADE)
        if not args.live_serper:
            return
        conn = get_conn()

    if args.live_serper:
        if args.claim_ids:
            claim_ids = [int(x.strip()) for x in args.claim_ids.split(",") if x.strip()]
        else:
            claim_ids = list(LIVE_SERPER_DEFAULT)
        claims = _load_claims(conn, claim_ids)
        conn.close()
        print(f"[live B] canlı Serper: {len(claims)} iddia {claim_ids}")
        results = [evaluate_live_serper(c) for c in claims]
        summary = _live_serper_summary(results)
        report = {
            "mode": "live_serper",
            "claim_ids": claim_ids,
            "summary": summary,
            "results": results,
        }
        OUT_PATH_LIVE_SERPER.parent.mkdir(parents=True, exist_ok=True)
        OUT_PATH_LIVE_SERPER.write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        _print_live_serper(summary, results, OUT_PATH_LIVE_SERPER)
        return

    if args.from_no_evidence:
        rows = conn.execute("""
            SELECT c.claim_id FROM claims c
            JOIN verdicts vr ON vr.claim_id = c.claim_id
            WHERE c.video_id = 'odZgEDFDmbE' AND c.archived_at IS NULL
              AND vr.nli_evidence_snippet LIKE '%PubMed%bulunamad%'
            ORDER BY c.claim_id
        """).fetchall()
        claim_ids = [r["claim_id"] for r in rows]
    elif args.claim_ids:
        claim_ids = [int(x.strip()) for x in args.claim_ids.split(",") if x.strip()]
    elif args.v2:
        claim_ids = list(V2_SAMPLE)
    else:
        claim_ids = list(DEFAULT_SAMPLE)

    claims = _load_claims(conn, claim_ids)
    conn.close()

    if args.v2:
        results = [evaluate_claim_v2(c) for c in claims]
        pubtype_proof = prove_publication_types_live()
        report = {
            "sample_claim_ids": claim_ids,
            "selection_note": (
                "745/752: bu tur other URL'leri (MADDE 1); "
                "kalanı no_evidence_found veya NLI<0.75"
            ),
            "publication_type_live_proof": pubtype_proof,
            "delta": _v2_delta(results),
            "results": results,
        }
        OUT_PATH_V2.parent.mkdir(parents=True, exist_ok=True)
        OUT_PATH_V2.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[shadow_test_v2] delta: {json.dumps(report['delta'], ensure_ascii=False)}")
        print("[shadow_test_v2] PublicationType kanıtı:")
        for p in pubtype_proof["proofs"]:
            ok = "OK" if p["match"] else "FAIL"
            print(f"  [{ok}] PMID {p['pmid']} types={p['publication_types_from_xml']} "
                  f"→ {p['source_tier']} (beklenen {p['expected']})")
        print(f"[shadow_test_v2] rapor -> {OUT_PATH_V2}")
        for r in results:
            m1 = "TIER↑" if r["madde1_allowlist"]["changed"] else "—"
            n2 = r["madde2_pubmed_pubtype"]["evidence_count"]
            n3 = r["madde3_europepmc"]["evidence_count"]
            n4 = r["madde4_medlineplus"]["evidence_count"]
            print(f"  [{r['claim_id']}] m1={m1} m2={n2} m3={n3} m4={n4} "
                  f"no_ev={r['was_no_evidence']}")
        return

    results = [evaluate_claim(c) for c in claims]
    success = sum(1 for r in results if r["retrieval_would_succeed"])
    total = len(results)

    report = {
        "sample_claim_ids": claim_ids,
        "success_count": success,
        "total": total,
        "success_rate": round(success / total, 2) if total else 0,
        "recommendation": (
            "proceed_hybrid_retrieval" if success >= max(4, total * 0.67)
            else "guideline_only" if success >= 2
            else "accept_current_pipeline"
        ),
        "results": results,
    }

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"[shadow_test] {success}/{total} iddiada hibrit retrieval kanıt buldu")
    print(f"[shadow_test] öneri: {report['recommendation']}")
    print(f"[shadow_test] rapor -> {OUT_PATH}")
    for r in results:
        flag = "OK" if r["retrieval_would_succeed"] else "MISS"
        print(f"  [{r['claim_id']}] {flag} path={r['hybrid_path']} "
              f"baseline={r['baseline_pubmed'].get('with_abstract', 0)} "
              f"mesh={r['best_mesh_variant'].get('with_abstract', 0)}")


if __name__ == "__main__":
    main()
