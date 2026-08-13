#!/usr/bin/env python3
"""
v2 birleşik iddia listesini transkript chunk sınırlarına geri atar (API'siz).

Kaynak: data/reextract_v2_compare.json + DB transkript
Çıktı: data/extraction_chunks_reconstructed/{video_id}.json
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from utils.claude_client import _is_recap_chunk, _split_transcript_chunks
from utils.db import get_conn

REEXTRACT_PATH = Path(__file__).parent.parent / "data" / "reextract_v2_compare.json"
OUT_DIR = Path(__file__).parent.parent / "data" / "extraction_chunks_reconstructed"
DEFAULT_VIDEOS = ["odZgEDFDmbE", "P4m9F9mykQ8"]


def chunk_timestamp_range(chunk: str) -> tuple[int | None, int | None]:
    tags = [int(m) for m in re.findall(r"\[(\d+)s\]", chunk or "")]
    if not tags:
        return None, None
    return min(tags), max(tags)


def assign_claim_to_chunk(ts: int | None, ranges: list[dict]) -> tuple[int, str]:
    """(chunk_index, assignment_reason)"""
    if ts is None:
        return 0, "no_timestamp_default_first"

    in_range = [
        r for r in ranges
        if r["ts_min"] is not None and r["ts_max"] is not None
        and r["ts_min"] <= ts <= r["ts_max"]
    ]
    if len(in_range) == 1:
        return in_range[0]["chunk_index"], "in_range"
    if len(in_range) > 1:
        best = min(in_range, key=lambda r: min(abs(ts - r["ts_min"]), abs(ts - r["ts_max"])))
        return best["chunk_index"], "multi_range_nearest"

    # Overlap/tail: en yakın chunk merkezine ata
    best_idx = 0
    best_dist = float("inf")
    for r in ranges:
        if r["ts_min"] is None:
            continue
        center = (r["ts_min"] + r["ts_max"]) / 2
        dist = abs(ts - center)
        if dist < best_dist:
            best_dist = dist
            best_idx = r["chunk_index"]
    return best_idx, "nearest_center"


def reconstruct_video(conn, video_id: str, after_claims: list[dict]) -> dict:
    row = conn.execute(
        "SELECT transcript, title FROM videos WHERE video_id=?",
        (video_id,),
    ).fetchone()
    if not row or not row["transcript"]:
        raise ValueError(f"transkript yok: {video_id}")

    transcript = row["transcript"]
    chunks = _split_transcript_chunks(transcript)
    ranges = []
    for i, chunk in enumerate(chunks, 1):
        ts_min, ts_max = chunk_timestamp_range(chunk)
        ranges.append({
            "chunk_index": i,
            "ts_min": ts_min,
            "ts_max": ts_max,
            "is_recap": _is_recap_chunk(chunk, is_last=(i == len(chunks))),
            "char_len": len(chunk),
        })

    by_chunk: dict[int, list[dict]] = {r["chunk_index"]: [] for r in ranges}
    ambiguous: list[dict] = []

    for claim in after_claims:
        ts = claim.get("timestamp_sec")
        idx, reason = assign_claim_to_chunk(ts, ranges)
        entry = {**claim, "_assignment": reason}
        if reason != "in_range":
            ambiguous.append({"claim_text": claim.get("claim_text"), "timestamp_sec": ts, "reason": reason})
        by_chunk[idx].append(entry)

    chunk_lists = []
    for r in ranges:
        idx = r["chunk_index"]
        chunk_lists.append({
            "chunk_index": idx,
            "is_recap": r["is_recap"],
            "ts_min": r["ts_min"],
            "ts_max": r["ts_max"],
            "claims": [
                {k: v for k, v in c.items() if not k.startswith("_")}
                for c in by_chunk[idx]
            ],
            "claim_count": len(by_chunk[idx]),
        })

    return {
        "video_id": video_id,
        "title": row["title"],
        "total_claims": len(after_claims),
        "chunk_count": len(chunks),
        "ambiguous_assignments": ambiguous,
        "chunks": chunk_lists,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--video-id", action="append", dest="video_ids")
    ap.add_argument("--reextract", type=Path, default=REEXTRACT_PATH)
    args = ap.parse_args()

    video_ids = args.video_ids or DEFAULT_VIDEOS
    data = json.loads(args.reextract.read_text(encoding="utf-8"))
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    conn = get_conn()
    for vid in video_ids:
        video_data = data.get("videos", {}).get(vid)
        if not video_data:
            print(f"[reconstruct] atlandı (reextract yok): {vid}")
            continue
        after = video_data.get("after") or []
        result = reconstruct_video(conn, vid, after)
        out_path = OUT_DIR / f"{vid}.json"
        out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        counts = [c["claim_count"] for c in result["chunks"]]
        print(f"[reconstruct] {vid}: {result['total_claims']} iddia → {result['chunk_count']} chunk {counts}")
        if result["ambiguous_assignments"]:
            print(f"  belirsiz atama: {len(result['ambiguous_assignments'])}")
    conn.close()


if __name__ == "__main__":
    main()
