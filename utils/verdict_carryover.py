"""
Arşivlenmiş iddialardaki verdict'leri yeni aktif iddialara taşır (text + embedding eşleşmesi).
"""
from __future__ import annotations

from utils.claim_dedup import embed_texts, is_duplicate_pair
from utils.text_similarity import normalize


def _best_match(
    archived_text: str,
    archived_emb,
    active_candidates: list[tuple[int, str, object]],
    *,
    used_ids: set[int],
) -> int | None:
    best_id = None
    best_score = -1.0
    for claim_id, text, emb in active_candidates:
        if claim_id in used_ids:
            continue
        if normalize(archived_text) == normalize(text):
            return claim_id
        if is_duplicate_pair(archived_text, text, archived_emb, emb):
            score = float(archived_emb @ emb)
            if score > best_score:
                best_score = score
                best_id = claim_id
    return best_id


def carryover_verdicts(conn, video_id: str) -> dict:
    """
    Arşivlenmiş iddialarda verdict var, aktif karşılığında yok → kopyala.
    """
    archived = conn.execute("""
        SELECT c.claim_id, c.claim_text
        FROM claims c
        INNER JOIN verdicts vr ON vr.claim_id = c.claim_id
        WHERE c.video_id = ? AND c.archived_at IS NOT NULL
    """, (video_id,)).fetchall()

    active = conn.execute("""
        SELECT c.claim_id, c.claim_text
        FROM claims c
        LEFT JOIN verdicts vr ON vr.claim_id = c.claim_id
        WHERE c.video_id = ? AND c.archived_at IS NULL AND vr.claim_id IS NULL
    """, (video_id,)).fetchall()

    if not archived or not active:
        return {"matched": 0, "skipped": len(archived), "reason": "nothing_to_match"}

    arch_texts = [normalize(r["claim_text"]) for r in archived]
    act_texts = [normalize(r["claim_text"]) for r in active]
    arch_embs = embed_texts(arch_texts)
    act_embs = embed_texts(act_texts)
    active_candidates = [
        (active[i]["claim_id"], act_texts[i], act_embs[i])
        for i in range(len(active))
    ]

    used: set[int] = set()
    matched = 0

    for i, row in enumerate(archived):
        target_id = _best_match(
            arch_texts[i],
            arch_embs[i],
            active_candidates,
            used_ids=used,
        )
        if target_id is None:
            continue

        src = conn.execute("SELECT * FROM verdicts WHERE claim_id=?", (row["claim_id"],)).fetchone()
        if not src:
            continue

        conn.execute("""
            INSERT OR REPLACE INTO verdicts (
                claim_id, nli_label, nli_confidence, nli_evidence_snippet,
                escalated, final_verdict, confidence, source_url,
                reasoning, source_directness, evidence_stance, source_tier,
                calibration_flags, human_reviewed, auto_accepted, reviewer_note, verified_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
        """, (
            target_id,
            src["nli_label"],
            src["nli_confidence"],
            src["nli_evidence_snippet"],
            src["escalated"],
            src["final_verdict"],
            src["confidence"],
            src["source_url"],
            src["reasoning"],
            src["source_directness"],
            src["evidence_stance"],
            src["source_tier"],
            src["calibration_flags"],
            src["human_reviewed"],
            src["auto_accepted"] if "auto_accepted" in src.keys() else 0,
            src["reviewer_note"],
        ))
        used.add(target_id)
        matched += 1

    conn.commit()
    return {
        "matched": matched,
        "archived_with_verdict": len(archived),
        "active_without_verdict": len(active),
        "unmatched_archived": len(archived) - matched,
    }
