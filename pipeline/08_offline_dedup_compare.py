"""
Embedding dedup'u v2 iddialarına offline uygular — API çağrısı gerektirmez.
Kalibre eşik için: ./venv/bin/python scripts/calibrate_dedup_threshold.py
Rekonstrüksiyon: ./venv/bin/python scripts/reconstruct_chunk_claims.py
"""
import json
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent))
from utils.claim_dedup import (
    dedupe_claims,
    dedupe_pipeline,
    threshold_metadata,
)
from utils.text_similarity import get_cluster_members, normalize

VIDEOS = ["odZgEDFDmbE", "P4m9F9mykQ8"]
REEXTRACT_PATH = Path("data/reextract_v2_compare.json")
CHUNK_DIR = Path("data/extraction_chunks_reconstructed")
TARGETS = {"odZgEDFDmbE": (65, 75), "P4m9F9mykQ8": (25, 35)}

RECALL_CHECKLIST = {
    "odZgEDFDmbE": [
        ("marcos_case", ["67 yaşındaki", "evre 3", "evre 4"]),
        ("ines_potasyum", ["potasyum", "6.2"]),
        ("gfr_definition", ["gfr", "glomerüler filtrasyon"]),
    ],
    "P4m9F9mykQ8": [
        ("b12_metformin", ["b12", "metformin"]),
        ("lunula", ["lunula"]),
        ("koku_parkinson", ["koku", "parkinson"]),
        ("parkinson_case", ["parkinson", "67 yaşındaki"]),
        ("b12_neuro", ["b12", "miyelin"]),
    ],
}


def load_v2_after(video_id: str) -> list[dict]:
    data = json.loads(REEXTRACT_PATH.read_text(encoding="utf-8"))
    return data["videos"][video_id]["after"]


def load_chunk_lists(video_id: str) -> list[dict]:
    path = CHUNK_DIR / f"{video_id}.json"
    if not path.is_file():
        raise FileNotFoundError(f"Önce reconstruct çalıştırın: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    return [
        {"chunk_index": c["chunk_index"], "is_recap": c["is_recap"], "claims": c["claims"]}
        for c in data["chunks"]
    ]


def dup_clusters(claims: list[dict]) -> int:
    items = [{"id": i, "text": c["claim_text"]} for i, c in enumerate(claims)]
    return len(get_cluster_members(items, id_key="id", text_key="text", threshold=0.85))


def check_recall(video_id: str, kept_claims: list[dict]) -> dict:
    texts = " ".join(normalize(c.get("claim_text") or "") for c in kept_claims)
    results = {}
    for key, terms in RECALL_CHECKLIST.get(video_id, []):
        results[key] = all(t in texts for t in terms)
    return results


def main():
    meta = threshold_metadata()
    print(
        f"[offline] dedup cosine={meta['threshold']} "
        f"lexical={meta.get('lexical_threshold', 'n/a')} "
        f"calibrated={meta.get('calibrated', False)}"
    )
    report = {"threshold_meta": meta, "videos": {}}

    for vid in VIDEOS:
        v2_raw = load_v2_after(vid)
        chunk_lists = load_chunk_lists(vid)
        v2_single = dedupe_claims([{"claim_text": c["claim_text"], **c} for c in v2_raw])
        v2_pipeline = dedupe_pipeline(chunk_lists)

        v2_texts = {normalize(c["claim_text"]) for c in v2_raw}
        single_texts = {normalize(c["claim_text"]) for c in v2_single}
        pipe_texts = {normalize(c["claim_text"]) for c in v2_pipeline}
        removed_pipe = sorted(v2_texts - pipe_texts)

        lo, hi = TARGETS[vid]
        in_target = lo <= len(v2_pipeline) <= hi
        recall = check_recall(vid, v2_pipeline)

        report["videos"][vid] = {
            "v2_raw_count": len(v2_raw),
            "v2_single_pass_count": len(v2_single),
            "v2_pipeline_count": len(v2_pipeline),
            "target_range": [lo, hi],
            "in_target": in_target,
            "v2_raw_dup_clusters": dup_clusters(v2_raw),
            "v2_pipeline_dup_clusters": dup_clusters(v2_pipeline),
            "removed_by_pipeline": removed_pipe,
            "recall_checklist": recall,
            "recall_ok": all(recall.values()),
        }

        print(f"\n{vid}")
        print(f"  v2 ham: {len(v2_raw)} iddia, dup_clusters={report['videos'][vid]['v2_raw_dup_clusters']}")
        print(f"  tek geçiş dedup: {len(v2_single)} iddia")
        print(f"  iki katmanlı pipeline: {len(v2_pipeline)} iddia, dup_clusters={report['videos'][vid]['v2_pipeline_dup_clusters']}")
        print(f"  hedef {lo}-{hi}: {'OK' if in_target else 'DIŞINDA'}")
        print(f"  recall checklist: {recall}")
        for t in removed_pipe[:8]:
            print(f"    · elendi: {t[:90]}...")
        if len(removed_pipe) > 8:
            print(f"    ... +{len(removed_pipe) - 8} daha")

    out = Path("data/reextract_v2_offline_dedup.json")
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[offline] → {out}")


if __name__ == "__main__":
    main()
