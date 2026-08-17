"""
Kayıtlı smoke NLI + tüm-aday yerel NLI ile specificity_tier / epistemic_class.

Anthropic/Serper yok.
  1) Tier: data/smoke_jP5XF06OLbo/specificity_offline.json top-1 NLI
  2) Epistemik: paket adaylarını PubMed EFetch ile sulandırıp yerel nli_check
     (yalnızca SUPPORTS/REFUTES conf >= 0.5 var mı)

Kullanım:
    python pipeline/14_specificity_tier_offline.py
    python pipeline/14_specificity_tier_offline.py --claim-ids 1243,1248,1284,745,752,663
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent))

from utils.evidence_retrieval import (
    classify_evidence_expectation,
    classify_specificity_tier,
    collect_specificity_nli_scores,
    EPISTEMIC_NO_DIRECT,
    SPECIFICITY_SUPPORTIVE_MIN_CONF,
)

ROOT = Path(__file__).parent.parent
SAVED_NLI = ROOT / "data" / "smoke_jP5XF06OLbo" / "specificity_offline.json"
OUT_PATH = ROOT / "data" / "smoke_jP5XF06OLbo" / "specificity_tier_offline.json"
DEFAULT_IDS = (1243, 1248, 1284, 745, 752, 663)


def _load_shadow12():
    path = Path(__file__).parent / "12_specificity_offline.py"
    spec = importlib.util.spec_from_file_location("specificity_offline_mod", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _nli_from_saved(row: dict) -> dict | None:
    if row.get("nli_label") is None:
        return None
    return {
        "nli_label": row["nli_label"],
        "nli_confidence": row.get("nli_confidence"),
    }


def _tier_from_saved(row: dict) -> str:
    return classify_specificity_tier(
        row.get("reason_code") or "ok",
        bool(row.get("relevance_ok")),
        bool(row.get("quality_ok")),
        _nli_from_saved(row),
    )


def _max_direct_score(scores: list[dict]) -> dict:
    best_label = None
    best_conf = 0.0
    for nli in scores:
        label = nli.get("nli_label")
        try:
            conf = float(nli.get("nli_confidence") or 0.0)
        except (TypeError, ValueError):
            conf = 0.0
        if label in ("SUPPORTS", "REFUTES") and conf >= best_conf:
            best_label, best_conf = label, conf
    return {"max_sr_label": best_label, "max_sr_confidence": best_conf}


def _justification(
    *,
    tier: str,
    epistemic: str | None,
    saved: dict,
    pool: dict,
) -> str:
    top = (
        f"top-1 {saved.get('nli_label')}/{saved.get('nli_confidence')} "
        f"→ tier={tier}"
    )
    max_sr = pool.get("max_sr_confidence")
    max_lab = pool.get("max_sr_label")
    n_scored = pool.get("n_scored")
    if epistemic == EPISTEMIC_NO_DIRECT:
        epi = (
            f"havuz n={n_scored}: hiçbir SUPPORTS/REFUTES ≥ "
            f"{SPECIFICITY_SUPPORTIVE_MIN_CONF} "
            f"(max {max_lab}/{max_sr}) → {EPISTEMIC_NO_DIRECT}"
        )
    else:
        epi = (
            f"havuz n={n_scored}: en az bir aday {max_lab}/{max_sr} ≥ "
            f"{SPECIFICITY_SUPPORTIVE_MIN_CONF} → epistemik yok"
        )
    return f"{top}; {epi}"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--claim-ids", default=",".join(str(i) for i in DEFAULT_IDS))
    parser.add_argument("--saved", default=str(SAVED_NLI))
    parser.add_argument("--out", default=str(OUT_PATH))
    parser.add_argument(
        "--skip-pool-nli",
        action="store_true",
        help="yalnızca kayıtlı top-1; PubMed/yerel NLI yok",
    )
    args = parser.parse_args()
    claim_ids = [int(x.strip()) for x in args.claim_ids.split(",") if x.strip()]

    saved_path = Path(args.saved)
    if not saved_path.exists():
        raise SystemExit(f"kayıtlı NLI yok: {saved_path}")
    payload = json.loads(saved_path.read_text(encoding="utf-8"))
    by_id = {row["claim_id"]: row for row in payload.get("results") or []}

    shadow = None if args.skip_pool_nli else _load_shadow12()
    latest = shadow._latest_debug_records(claim_ids) if shadow else {}
    queries = shadow._search_queries(claim_ids) if shadow else {}

    rows = []
    for cid in claim_ids:
        saved = by_id.get(cid)
        if not saved:
            print(f"[offline] {cid} kayıtlı NLI yok — atlandı")
            continue
        tier = _tier_from_saved(saved)
        scores: list[dict] = []
        n_package = saved.get("package_n")
        if shadow:
            rec = latest.get(cid)
            if rec:
                candidates = shadow._hydrate_package(list(rec.get("package_urls") or []))
                n_package = len(candidates)
                print(f"[offline] {cid} paket NLI ({n_package} aday) …")
                scores = collect_specificity_nli_scores(
                    saved.get("claim_text") or rec.get("claim_text") or "",
                    candidates,
                )
            else:
                print(f"[offline] {cid} debug paket URL yok; epistemik top-1 proxy")
                nli = _nli_from_saved(saved)
                scores = [nli] if nli else []
        else:
            nli = _nli_from_saved(saved)
            scores = [nli] if nli else []
        epistemic = classify_evidence_expectation(
            saved.get("claim_text") or "", scores
        )
        pool = _max_direct_score(scores)
        pool["n_scored"] = len(scores)
        row = {
            "claim_id": cid,
            "claim_text": saved.get("claim_text"),
            "package_n": n_package,
            "relevance_ok": saved.get("relevance_ok"),
            "quality_ok": saved.get("quality_ok"),
            "reason_code": saved.get("reason_code"),
            "specificity_ok": saved.get("specificity_ok"),
            "strong_match": saved.get("strong_match"),
            "top_nli_label": saved.get("nli_label"),
            "top_nli_confidence": saved.get("nli_confidence"),
            "specificity_tier": tier,
            "epistemic_class": epistemic,
            "pool_n_scored": pool["n_scored"],
            "pool_max_sr_label": pool["max_sr_label"],
            "pool_max_sr_confidence": pool["max_sr_confidence"],
            "justification": _justification(
                tier=tier, epistemic=epistemic, saved=saved, pool=pool
            ),
            "search_query_en": queries.get(cid, ""),
        }
        rows.append(row)
        print(
            f"  [{cid}] tier={tier} epistemic={epistemic or '—'} "
            f"top={saved.get('nli_label')}/{saved.get('nli_confidence')} "
            f"pool_max={pool['max_sr_label']}/{pool['max_sr_confidence']}"
        )
        print(f"           {row['justification']}")

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps({"claim_ids": claim_ids, "results": rows}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\n[offline] yazıldı: {out_path}")
    print(
        f"{'id':>6} {'tier':>11} {'epistemic':>28}  top-1  pool_max"
    )
    for row in rows:
        print(
            f"{row['claim_id']:>6} "
            f"{row['specificity_tier']:>11} "
            f"{(row['epistemic_class'] or '—'):>28}  "
            f"{row['top_nli_label']} {row['top_nli_confidence']}  "
            f"{row['pool_max_sr_label']}/{row['pool_max_sr_confidence']}"
        )


if __name__ == "__main__":
    main()
