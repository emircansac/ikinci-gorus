"""
Topic evidence cache — offline değerlendirme.

1) pending_batches + (opsiyonel) odZg canlı seed ile cache doldur
2) Kuyruk iddialarında cache-only vs live-only karşılaştır

Kullanım:
    ./venv/bin/python pipeline/16_topic_cache_offline_test.py
    ./venv/bin/python pipeline/16_topic_cache_offline_test.py --seed-live-odZg
    ./venv/bin/python pipeline/16_topic_cache_offline_test.py --test-ids 365,889,907
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent))

from utils.db import get_conn
from utils.evidence_retrieval import assess_evidence_sufficiency, retrieve_hybrid_evidence
from utils.evidence_topic_cache import (
    pilot_entities_in_text,
    seed_cache_from_evidence,
    topic_key_for_claim,
)

ROOT = Path(__file__).parent.parent
PENDING = ROOT / "data" / "pending_batches.json"
OUT_JSON = ROOT / "data" / "topic_cache_offline_test.json"
OUT_MD = ROOT / "data" / "topic_cache_offline_test.md"

SEED_PENDING_IDS = (360, 362, 363)
DEFAULT_TEST_IDS = (365, 366, 372, 889, 907)
ODZG_VIDEO = "odZgEDFDmbE"


def _pending_jobs() -> dict[int, dict]:
    if not PENDING.is_file():
        return {}
    data = json.loads(PENDING.read_text(encoding="utf-8"))
    out: dict[int, dict] = {}
    for rec in data.get("batches") or []:
        for job in rec.get("jobs") or []:
            out[int(job["claim_id"])] = job
    return out


def _seed_from_pending(conn) -> list[dict]:
    jobs = _pending_jobs()
    seeded = []
    for cid in SEED_PENDING_IDS:
        job = jobs.get(cid)
        if not job or not job.get("evidence"):
            continue
        row = conn.execute(
            "SELECT claim_text, category, search_query_en FROM claims WHERE claim_id=?",
            (cid,),
        ).fetchone()
        if not row:
            continue
        key = topic_key_for_claim(
            row["claim_text"], row["category"], search_query_en=row["search_query_en"]
        )
        if not key:
            continue
        n = seed_cache_from_evidence(
            conn, topic_key=key, evidence_items=job["evidence"], origin_claim_id=cid
        )
        seeded.append({"claim_id": cid, "topic_key": key, "rows": n, "source": "pending_batches"})
    return seeded


def _seed_from_odZg_live(conn, *, limit: int = 25) -> list[dict]:
    rows = conn.execute(
        """
        SELECT c.claim_id, c.claim_text, c.category, c.search_query_en
        FROM claims c
        JOIN verdicts v ON v.claim_id = c.claim_id
        WHERE c.video_id = ? AND c.category = 'mekanizma' AND c.archived_at IS NULL
        ORDER BY c.claim_id
        """,
        (ODZG_VIDEO,),
    ).fetchall()
    seeded = []
    for row in rows:
        if len(seeded) >= limit:
            break
        key = topic_key_for_claim(
            row["claim_text"], row["category"], search_query_en=row["search_query_en"]
        )
        if not key:
            continue
        _ev, _path, meta = retrieve_hybrid_evidence(
            row["claim_text"],
            search_query_en=row["search_query_en"],
            category=row["category"],
            origin_claim_id=int(row["claim_id"]),
            conn=conn,
            include_serper=False,
        )
        if meta.get("live_candidates", 0) > 0:
            seeded.append({
                "claim_id": int(row["claim_id"]),
                "topic_key": key,
                "live_candidates": meta["live_candidates"],
                "source": "live_odZg_seed",
            })
    return seeded


def _eval_claim(conn, cid: int) -> dict | None:
    row = conn.execute(
        """
        SELECT claim_id, claim_text, category, search_query_en, video_id
        FROM claims WHERE claim_id = ?
        """,
        (cid,),
    ).fetchone()
    if not row:
        return None
    key = topic_key_for_claim(
        row["claim_text"], row["category"], search_query_en=row["search_query_en"]
    )
    if not key:
        return None

    cache_ev, cache_path, cache_meta = retrieve_hybrid_evidence(
        row["claim_text"],
        search_query_en=row["search_query_en"],
        category=row["category"],
        skip_live_retrieval=True,
        conn=conn,
    )
    live_ev, live_path, live_meta = retrieve_hybrid_evidence(
        row["claim_text"],
        search_query_en=row["search_query_en"],
        category=row["category"],
        skip_live_retrieval=False,
        use_topic_cache=False,
        write_topic_cache=False,
        include_serper=False,
        conn=conn,
    )
    hybrid_ev, hybrid_path, hybrid_meta = retrieve_hybrid_evidence(
        row["claim_text"],
        search_query_en=row["search_query_en"],
        category=row["category"],
        skip_live_retrieval=False,
        use_topic_cache=True,
        include_serper=False,
        conn=conn,
    )

    query = row["search_query_en"] or row["claim_text"]
    live_suff = assess_evidence_sufficiency(live_ev, row["claim_text"], query)
    cache_suff = assess_evidence_sufficiency(cache_ev, row["claim_text"], query)
    hybrid_suff = assess_evidence_sufficiency(hybrid_ev, row["claim_text"], query)

    def _pkg_summary(evidence, suff):
        return {
            "n": len(evidence),
            "cache_in_pool": sum(1 for e in evidence if e.get("evidence_source") == "cache"),
            "live_in_pool": sum(1 for e in evidence if e.get("evidence_source") == "live"),
            "sufficient": suff.sufficient,
            "strong_match": suff.strong_match,
            "specificity_tier": suff.specificity_tier,
            "reason": suff.reason,
            "urls": [e.get("url") for e in evidence[:5]],
        }

    return {
        "claim_id": cid,
        "video_id": row["video_id"],
        "topic_key": key,
        "entities": pilot_entities_in_text(row["claim_text"], row["search_query_en"]),
        "claim_snippet": (row["claim_text"] or "")[:100],
        "live_only": _pkg_summary(live_ev, live_suff),
        "cache_only": _pkg_summary(cache_ev, cache_suff),
        "hybrid": _pkg_summary(hybrid_ev, hybrid_suff),
        "paths": {
            "live": live_path,
            "cache": cache_path,
            "hybrid": hybrid_path,
        },
        "meta": {
            "cache_candidates_hybrid": hybrid_meta.get("cache_candidates", 0),
            "cache_in_final_hybrid": hybrid_meta.get("cache_in_final", 0),
        },
        "sufficiency_changed": live_suff.sufficient != hybrid_suff.sufficient,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--seed-live-odZg",
        action="store_true",
        help="odZg mekanizma pilot iddialarından canlı retrieval ile cache seed (ağ gerekir)",
    )
    parser.add_argument(
        "--test-ids",
        default=",".join(str(i) for i in DEFAULT_TEST_IDS),
    )
    parser.add_argument("--clear-cache", action="store_true", help="Test öncesi cache tablosunu temizle")
    args = parser.parse_args()

    conn = get_conn()
    try:
        if args.clear_cache:
            conn.execute("DELETE FROM evidence_topic_cache")
            conn.commit()

        seed_log = _seed_from_pending(conn)
        if args.seed_live_odZg:
            seed_log.extend(_seed_from_odZg_live(conn))

        cache_rows = conn.execute("SELECT COUNT(*) FROM evidence_topic_cache").fetchone()[0]
        test_ids = [int(x.strip()) for x in args.test_ids.split(",") if x.strip()]
        results = []
        for cid in test_ids:
            row = _eval_claim(conn, cid)
            if row:
                results.append(row)

        out = {
            "seed": seed_log,
            "cache_row_count": cache_rows,
            "test_results": results,
        }
        OUT_JSON.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")

        lines = [
            "# Topic evidence cache — offline test",
            "",
            f"Cache satır sayısı: **{cache_rows}**",
            "",
            "## Seed",
            "",
        ]
        for s in seed_log:
            lines.append(f"- #{s['claim_id']} topic_key=`{s['topic_key']}` source={s['source']}")
        lines += ["", "## Test iddiaları", ""]
        for r in results:
            lines += [
                f"### #{r['claim_id']} ({r['video_id']}) — `{r['topic_key']}`",
                "",
                f"> {r['claim_snippet']}…",
                "",
                "| Mod | n | cache | live | sufficient | tier | reason |",
                "|-----|--:|------:|-----:|:----------:|------|--------|",
            ]
            for mode in ("cache_only", "live_only", "hybrid"):
                p = r[mode]
                lines.append(
                    f"| {mode} | {p['n']} | {p['cache_in_pool']} | {p['live_in_pool']} | "
                    f"{p['sufficient']} | {p['specificity_tier']} | {p['reason']} |"
                )
            lines.append(
                f"- hybrid path: `{r['paths']['hybrid']}`; "
                f"cache_in_final={r['meta']['cache_in_final_hybrid']}; "
                f"sufficiency_changed={r['sufficiency_changed']}"
            )
            lines.append("")

        OUT_MD.write_text("\n".join(lines), encoding="utf-8")
        print(f"[topic_cache_offline] seed={len(seed_log)} cache_rows={cache_rows} tested={len(results)}")
        print(f"  -> {OUT_JSON}")
        print(f"  -> {OUT_MD}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
