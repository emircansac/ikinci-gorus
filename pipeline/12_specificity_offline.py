"""
Smoke paketlerini PMID ile sulandırıp specificity_ok / strong_match hesabı.

Anthropic/Serper yok — PubMed EFetch (ücretsiz) + yerel nli_check.
Paket URL'leri data/factcheck_debug.jsonl'deki son kayıttan alınır.

Kullanım:
    python pipeline/12_specificity_offline.py
    python pipeline/12_specificity_offline.py --claim-ids 1243,1248,1284,745,752,663
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent))

from utils.db import get_conn
from utils.evidence_retrieval import (
    assess_evidence_sufficiency,
    _pubmed_fetch_abstracts,
    _specificity_nli_result,
    _top_candidate,
    filter_candidates_by_key_terms,
)

ROOT = Path(__file__).parent.parent
DEBUG_LOG = ROOT / "data" / "factcheck_debug.jsonl"
OUT_PATH = ROOT / "data" / "smoke_jP5XF06OLbo" / "specificity_offline.json"

DEFAULT_IDS = (1243, 1248, 1284, 745, 752, 663)
EXPECTED_STRONG = {
    1243: False,
    1248: False,
    1284: True,
    745: False,
    752: False,
    663: False,
}

_PMID_RE = re.compile(
    r"(?:pubmed\.ncbi\.nlm\.nih\.gov/|europepmc\.org/article/MED/)(\d+)",
    re.I,
)


def _pmid_from_url(url: str) -> str | None:
    m = _PMID_RE.search(url or "")
    return m.group(1) if m else None


def _latest_debug_records(claim_ids: list[int]) -> dict[int, dict]:
    wanted = set(claim_ids)
    latest: dict[int, dict] = {}
    if not DEBUG_LOG.exists():
        raise SystemExit(f"debug log yok: {DEBUG_LOG}")
    with DEBUG_LOG.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            cid = rec.get("claim_id")
            if cid in wanted and rec.get("package_urls"):
                latest[cid] = rec
    return latest


def _search_queries(claim_ids: list[int]) -> dict[int, str]:
    out: dict[int, str] = {}
    try:
        conn = get_conn()
    except Exception as e:
        print(f"[offline] DB açılamadı ({e}); search_query_en boş")
        return out
    try:
        placeholders = ",".join("?" * len(claim_ids))
        rows = conn.execute(
            f"SELECT claim_id, search_query_en FROM claims WHERE claim_id IN ({placeholders})",
            claim_ids,
        ).fetchall()
        for row in rows:
            out[row["claim_id"]] = row["search_query_en"] or ""
    finally:
        conn.close()
    return out


def _hydrate_package(urls: list[str]) -> list[dict]:
    pmids = []
    pmid_for_url: dict[str, str] = {}
    for url in urls:
        pmid = _pmid_from_url(url)
        if pmid:
            pmid_for_url[url] = pmid
            if pmid not in pmids:
                pmids.append(pmid)
    fetched = _pubmed_fetch_abstracts(pmids) if pmids else {}
    candidates: list[dict] = []
    n = len(urls)
    for i, url in enumerate(urls):
        pmid = pmid_for_url.get(url)
        meta = fetched.get(pmid) if pmid else None
        item = dict(meta) if meta else {
            "url": url,
            "pmid": pmid,
            "title": "",
            "abstract": "",
        }
        item.setdefault("url", url)
        item["rerank_score"] = float(n - i)
        candidates.append(item)
    return candidates


def evaluate_claim(rec: dict, search_query_en: str) -> dict:
    claim_id = rec["claim_id"]
    claim_text = rec.get("claim_text") or ""
    urls = list(rec.get("package_urls") or [])
    candidates = _hydrate_package(urls)
    suff = assess_evidence_sufficiency(candidates, claim_text, search_query_en or None)
    kept, _meta = filter_candidates_by_key_terms(
        candidates, search_query_en or "", claim_text
    )
    top = _top_candidate(kept) if kept else None
    nli = _specificity_nli_result(claim_text, top) if top else None
    top_pmid = (top or {}).get("pmid") if top else None
    top_title = ((top or {}).get("title") or "")[:160] if top else ""
    nli_label = (nli or {}).get("nli_label")
    nli_conf = (nli or {}).get("nli_confidence")
    if not suff.sufficient:
        reason = f"kapı {suff.reason} (sufficient=False) — NLI üst kademeye çıkmadı / specificity_ok=False"
    elif nli is None:
        reason = "top adayda abstract/title yok"
    elif suff.specificity_ok:
        reason = (
            f"top PMID {top_pmid} NLI {nli_label} conf={nli_conf} ≥ 0.75 "
            f"— spesifik önerme ele alınıyor"
        )
    else:
        reason = (
            f"top PMID {top_pmid} NLI {nli_label} conf={nli_conf} "
            f"(SUPPORTS/REFUTES + ≥0.75 değil) — konu/kademe geçti, spesifiklik yok"
        )
    expected = EXPECTED_STRONG.get(claim_id)
    return {
        "claim_id": claim_id,
        "claim_text": claim_text,
        "package_n": len(candidates),
        "sufficient": suff.sufficient,
        "relevance_ok": suff.relevance_ok,
        "quality_ok": suff.quality_ok,
        "specificity_ok": suff.specificity_ok,
        "strong_match": suff.strong_match,
        "reason_code": suff.reason,
        "nli_label": nli_label,
        "nli_confidence": nli_conf,
        "top_pmid": top_pmid,
        "top_title": top_title,
        "justification": reason,
        "expected_strong_match": expected,
        "matches_expectation": None if expected is None else (suff.strong_match == expected),
        "prev_cite_source": rec.get("cite_source"),
        "prev_verdict": (rec.get("calibrated") or rec.get("raw") or {}).get("final_verdict"),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--claim-ids", default=",".join(str(i) for i in DEFAULT_IDS))
    parser.add_argument("--out", default=str(OUT_PATH))
    args = parser.parse_args()
    claim_ids = [int(x.strip()) for x in args.claim_ids.split(",") if x.strip()]

    latest = _latest_debug_records(claim_ids)
    missing = [cid for cid in claim_ids if cid not in latest]
    if missing:
        print(f"[offline] paket URL'si olmayan iddialar: {missing}")

    queries = _search_queries([cid for cid in claim_ids if cid in latest])
    rows = []
    for cid in claim_ids:
        rec = latest.get(cid)
        if not rec:
            continue
        print(f"[offline] {cid} PubMed EFetch + NLI …")
        row = evaluate_claim(rec, queries.get(cid, ""))
        rows.append(row)
        print(
            f"  [{cid}] specificity_ok={row['specificity_ok']} "
            f"strong_match={row['strong_match']} "
            f"nli={row['nli_label']}/{row['nli_confidence']} "
            f"top={row['top_pmid']}"
        )
        print(f"           {row['justification']}")

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"claim_ids": claim_ids, "results": rows}
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[offline] yazıldı: {out_path}")
    print(
        f"{'id':>6} {'spec':>5} {'strong':>6} {'expect':>6} {'ok?':>4}  nli"
    )
    for row in rows:
        exp = row["expected_strong_match"]
        print(
            f"{row['claim_id']:>6} "
            f"{str(row['specificity_ok']):>5} "
            f"{str(row['strong_match']):>6} "
            f"{str(exp):>6} "
            f"{str(row['matches_expectation']):>4}  "
            f"{row['nli_label']} {row['nli_confidence']}"
        )


if __name__ == "__main__":
    main()
