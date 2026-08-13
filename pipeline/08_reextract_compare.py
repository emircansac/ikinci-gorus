"""
Aşama 2 v2 A/B: Seçili videoları yeniden çıkarır, önceki iddialarla karşılaştırır.

Eski iddialar API başarılı olana kadar DOKUNULMAZ. Başarı sonrası arşivlenir (silinmez),
yeni iddialar extraction_version ile eklenir.

Kullanım:
    python pipeline/08_reextract_compare.py --video-id odZgEDFDmbE --video-id P4m9F9mykQ8
    python pipeline/08_reextract_compare.py --extraction-version v2
"""
import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent))
from utils.claude_client import extract_claims
from utils.db import get_conn
from utils.extraction_store import (
    DEFAULT_EXTRACTION_VERSION,
    fetch_active_claims,
    promote_extraction,
)
from utils.text_similarity import get_cluster_members, normalize

DEFAULT_VIDEOS = ["odZgEDFDmbE", "P4m9F9mykQ8"]


def _dup_cluster_count(claims: list[dict]) -> int:
    items = [{"id": i, "text": c["claim_text"]} for i, c in enumerate(claims)]
    return len(get_cluster_members(items, id_key="id", text_key="text", threshold=0.85))


def _compare(before: list[dict], after: list[dict]) -> dict:
    before_norms = {normalize(c["claim_text"]) for c in before}
    after_norms = {normalize(c["claim_text"]) for c in after}
    removed = before_norms - after_norms
    added = after_norms - before_norms
    kept = before_norms & after_norms
    return {
        "before_count": len(before),
        "after_count": len(after),
        "kept_exact": len(kept),
        "removed_from_active": sorted(removed),
        "added": sorted(added),
        "before_dup_clusters": _dup_cluster_count(before),
        "after_dup_clusters": _dup_cluster_count(after),
        "before_risk": dict(Counter(c["initial_risk"] for c in before)),
        "after_risk": dict(Counter(c["initial_risk"] for c in after)),
        "before_category": dict(Counter(c["category"] for c in before)),
        "after_category": dict(Counter(c["category"] for c in after)),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--video-id", action="append", dest="video_ids")
    ap.add_argument("--export-dir", default="data")
    ap.add_argument("--extraction-version", default=DEFAULT_EXTRACTION_VERSION)
    args = ap.parse_args()
    video_ids = args.video_ids or DEFAULT_VIDEOS
    export_dir = Path(args.export_dir)
    export_dir.mkdir(parents=True, exist_ok=True)

    conn = get_conn()
    snapshot = {"generated_at": datetime.now(timezone.utc).isoformat(), "extraction_version": args.extraction_version, "videos": {}}

    for video_id in video_ids:
        row = conn.execute(
            "SELECT video_id, channel_id, title, transcript FROM videos WHERE video_id=?",
            (video_id,),
        ).fetchone()
        if not row or not row["transcript"]:
            print(f"[reextract] atlandı (transkript yok): {video_id}")
            continue

        before = fetch_active_claims(conn, video_id)
        print(f"\n{'='*70}\n{video_id} — {row['title'][:60]}\n{'='*70}")
        print(f"  before (aktif): {len(before)} iddia, dup_clusters={_dup_cluster_count(before)}")

        snap_path = export_dir / f"reextract_before_{video_id}.json"
        snap_path.write_text(json.dumps(before, ensure_ascii=False, indent=2), encoding="utf-8")

        print(f"  [claude] yeniden çıkarılıyor (DB'ye yazılmadan)...")
        try:
            claims, success = extract_claims(row["transcript"], video_id=video_id)
        except Exception as e:
            print(f"  !! API hatası — aktif iddialar korundu: {e}")
            snapshot["videos"][video_id] = {"error": str(e), "before": before, "snapshot": str(snap_path)}
            continue

        if not success:
            print(f"  !! JSON parse başarısız — aktif iddialar korundu (snapshot: {snap_path})")
            snapshot["videos"][video_id] = {"error": "parse_failed", "before": before, "snapshot": str(snap_path)}
            continue

        stats = promote_extraction(
            conn, video_id, row["channel_id"], claims, args.extraction_version,
            carryover_verdicts=True,
        )
        after = fetch_active_claims(conn, video_id)
        cmp = _compare(before, after)
        cmp["promote"] = stats
        snapshot["videos"][video_id] = {"before": before, "after": after, "compare": cmp}

        print(f"  after (aktif):  {cmp['after_count']} iddia, dup_clusters={cmp['after_dup_clusters']}")
        print(f"  arşivlendi: {stats['archived']} eski iddia (silinmedi)")
        if stats.get("verdict_carryover"):
            vc = stats["verdict_carryover"]
            print(f"  verdict_carryover: {vc.get('matched', 0)}/{vc.get('archived_with_verdict', 0)} eşleşti")
        print(f"  delta:  +{len(cmp['added'])} yeni, {cmp['kept_exact']} aynı metin")
        print(f"  risk:   {cmp['before_risk']} → {cmp['after_risk']}")
        print(f"  diğer:  {cmp['before_category'].get('diğer', 0)} → {cmp['after_category'].get('diğer', 0)}")
        if cmp["added"]:
            print("  [+] yeni iddialar:")
            for t in cmp["added"][:8]:
                print(f"      · {t[:100]}{'...' if len(t) > 100 else ''}")
            if len(cmp["added"]) > 8:
                print(f"      ... +{len(cmp['added']) - 8} daha")

    out_path = export_dir / "reextract_v2_compare.json"
    out_path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
    conn.close()
    print(f"\n[reextract] karşılaştırma kaydedildi → {out_path}")


if __name__ == "__main__":
    main()
