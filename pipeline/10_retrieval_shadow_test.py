"""
Gölge test: no_evidence_found iddialarda retrieval kalitesini ölçer.

Kullanım:
    ./venv/bin/python pipeline/10_retrieval_shadow_test.py
    ./venv/bin/python pipeline/10_retrieval_shadow_test.py --claim-ids 652,654,663,681,689,695
    ./venv/bin/python pipeline/10_retrieval_shadow_test.py --v2
"""
import argparse
import json
import sys
from pathlib import Path

import requests

sys.path.append(str(Path(__file__).parent.parent))
from utils.db import get_conn
from utils.evidence_retrieval import (
    CANDIDATE_POOL_SIZE,
    FINAL_EVIDENCE_COUNT,
    PUBMED_EFETCH,
    apply_key_term_filter,
    expand_query_variants,
    europepmc_candidates,
    medlineplus_candidates,
    parse_pubmed_efetch_xml,
    pubmed_search_hit_count,
    retrieve_guideline_snippets,
    retrieve_hybrid_evidence,
    _dense_rerank,
    _merge_candidates,
    _pubmed_candidates_from_query,
)
from utils.factcheck_calibrate import infer_source_tier
from utils.nutrition_lookup import is_nutrition_quantity_claim, lookup_nutrition_evidence

DEFAULT_SAMPLE = [652, 654, 663, 681, 689, 695]
# Bu turun no_evidence / NLI<0.75 kümesinden 8 iddia (MADDE 1 URL'leri dahil)
V2_SAMPLE = [745, 752, 653, 663, 652, 746, 667, 678]
OUT_PATH = Path(__file__).parent.parent / "data" / "retrieval_shadow_test.json"
OUT_PATH_V2 = Path(__file__).parent.parent / "data" / "retrieval_shadow_test_v2.json"

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
               vr.source_url AS stored_source_url,
               vr.source_tier AS stored_source_tier,
               vr.final_verdict AS stored_verdict,
               vr.confidence AS stored_confidence,
               vr.nli_evidence_snippet,
               vr.nli_confidence
        FROM claims c
        LEFT JOIN verdicts vr ON vr.claim_id = c.claim_id
        WHERE c.claim_id IN ({placeholders})
        ORDER BY c.claim_id
    """, claim_ids).fetchall()
    return [dict(r) for r in rows]


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

    hybrid, path = retrieve_hybrid_evidence(text, query, category)

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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--claim-ids", default="")
    ap.add_argument("--from-no-evidence", action="store_true",
                    help="odZg no_evidence snippet'li tüm iddiaları test et")
    ap.add_argument("--v2", action="store_true",
                    help="4 maddeyi sırayla ölç, data/retrieval_shadow_test_v2.json yaz")
    args = ap.parse_args()

    conn = get_conn()
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
