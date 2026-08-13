"""
Çapraz-video doğrulanmış iddia kütüphanesi.

Aynı iddia farklı videolarda tekrarlandığında fact-check maliyetini düşürür.
Yalnızca human_reviewed=1, tam doğrulanmış/yanlış ve kısmi olmayan iddialar seed edilir.
"""
from __future__ import annotations

import sqlite3

import numpy as np

from utils.claim_dedup import (
    embed_texts, get_threshold, get_lexical_threshold,
    token_jaccard, pair_merge_blocked,
)
from utils.text_similarity import normalize

LIBRARY_VERDICTS = frozenset({"doğrulanmış", "yanlış"})

# Tam otomatik eşleşme — alt band (0.75–auto) Claude bypass yapmaz
LIBRARY_REVIEW_THRESHOLD = 0.75

# Seed'e asla alınmayacak origin claim_id'ler (audit)
SEED_BLOCKLIST: frozenset[int] = frozenset({653})

from utils.reasoning_patterns import PARTIAL_REASONING_RE

PARTIAL_CALIBRATION_FLAGS = frozenset({
    "default_conf", "mixed_overconfident", "indirect_binary_verdict",
    "insufficient_evidence", "nutrition_partial_match",
})


def _blob_from_emb(emb: np.ndarray) -> bytes:
    return emb.astype(np.float32).tobytes()


def _emb_from_blob(blob: bytes, dim: int = 384) -> np.ndarray:
    return np.frombuffer(blob, dtype=np.float32).copy().reshape(-1)[:dim]


def is_seed_eligible(
    *,
    claim_id: int,
    final_verdict: str | None,
    reasoning: str | None,
    evidence_stance: str | None = None,
    calibration_flags: str | None = None,
    human_reviewed: int = 0,
) -> tuple[bool, str]:
    """
    Kütüphaneye seed edilebilir mi?
    Dönüş: (eligible, reason_if_not)
    """
    if claim_id in SEED_BLOCKLIST:
        return False, "blocklist"
    if human_reviewed != 1:
        return False, "not_human_reviewed"
    if final_verdict not in LIBRARY_VERDICTS:
        return False, f"verdict={final_verdict}"
    if evidence_stance in ("mixed", "insufficient"):
        return False, f"stance={evidence_stance}"
    reasoning = reasoning or ""
    if PARTIAL_REASONING_RE.search(reasoning):
        return False, "partial_reasoning"
    flags = {f.strip() for f in (calibration_flags or "").split(",") if f.strip()}
    if flags & PARTIAL_CALIBRATION_FLAGS:
        return False, f"calibration={','.join(sorted(flags & PARTIAL_CALIBRATION_FLAGS))}"
    return True, ""


def classify_library_match(
    cosine: float,
    jaccard: float,
    *,
    numeric_conflict: bool = False,
    auto_threshold: float | None = None,
    lexical_threshold: float | None = None,
    review_threshold: float = LIBRARY_REVIEW_THRESHOLD,
) -> str | None:
    """
    Kütüphane eşleşme kademesi.

    auto         — cosine≥0.8055 ve lexical≥0.35 ve sayı çatışması yok → Claude bypass
    flag_review  — 0.75≤cosine<auto, VEYA cosine≥auto ama lexical<0.35,
                   VEYA sayısal koruma (GI 42 vs 38) → logla, bypass yok
    None         — cosine<0.75
    """
    auto_threshold = get_threshold() if auto_threshold is None else auto_threshold
    lexical_threshold = (
        get_lexical_threshold() if lexical_threshold is None else lexical_threshold
    )
    if cosine < review_threshold:
        return None
    if numeric_conflict:
        return "flag_review"
    if cosine >= auto_threshold and jaccard >= lexical_threshold:
        return "auto"
    return "flag_review"


def _match_reason(
    cosine: float,
    jaccard: float,
    *,
    numeric_conflict: bool,
    auto_threshold: float,
    lexical_threshold: float,
) -> str:
    if numeric_conflict:
        return "numeric_conflict"
    if cosine >= auto_threshold and jaccard < lexical_threshold:
        return "high_cosine_low_lexical"
    if cosine < auto_threshold:
        return "mid_cosine_band"
    return "auto"


def ensure_library_table(conn) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS verified_claim_library (
            library_id      INTEGER PRIMARY KEY AUTOINCREMENT,
            claim_text      TEXT NOT NULL,
            claim_text_norm TEXT NOT NULL,
            embedding       BLOB,
            final_verdict     TEXT NOT NULL,
            confidence        REAL,
            source_url        TEXT,
            source_tier       TEXT,
            reasoning         TEXT,
            origin_claim_id   INTEGER,
            created_at        TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_vcl_norm ON verified_claim_library(claim_text_norm)
    """)
    try:
        conn.execute("ALTER TABLE verdicts ADD COLUMN library_match INTEGER DEFAULT 0")
    except sqlite3.OperationalError:
        pass
    conn.commit()


def lookup_library(conn, claim_text: str) -> dict | None:
    """
    Embedding+lexical eşleşme ile kütüphane sorgusu.

    match_tier='auto'         — cosine≥eşik + lexical; fact-check bypass
    match_tier='flag_review'  — 0.75–auto bandı, yüksek cosine+düşük lexical,
                                veya sayısal çatışma; bypass YOK, çağıran loglar
    """
    ensure_library_table(conn)
    norm = normalize(claim_text)
    if not norm:
        return None

    exact = conn.execute("""
        SELECT * FROM verified_claim_library WHERE claim_text_norm = ? LIMIT 1
    """, (norm,)).fetchone()
    if exact:
        out = dict(exact)
        out["match_tier"] = "auto"
        out["match_score"] = 1.0
        out["match_jaccard"] = 1.0
        out["match_reason"] = "exact"
        return out

    rows = conn.execute("""
        SELECT library_id, claim_text, embedding, final_verdict, confidence,
               source_url, source_tier, reasoning, origin_claim_id
        FROM verified_claim_library
        WHERE embedding IS NOT NULL
    """).fetchall()
    if not rows:
        return None

    query_emb = embed_texts([claim_text])[0]
    auto_threshold = get_threshold()
    lexical_threshold = get_lexical_threshold()
    best_auto = None
    best_auto_score = -1.0
    best_review = None
    best_review_score = -1.0

    for row in rows:
        emb = _emb_from_blob(row["embedding"])
        score = float(query_emb @ emb)
        if score < LIBRARY_REVIEW_THRESHOLD:
            continue
        row_text = row["claim_text"] or ""
        jac = token_jaccard(norm, normalize(row_text))
        conflict = pair_merge_blocked(claim_text, row_text)
        tier = classify_library_match(
            score, jac,
            numeric_conflict=conflict,
            auto_threshold=auto_threshold,
            lexical_threshold=lexical_threshold,
        )
        if tier is None:
            continue
        packed = dict(row)
        packed["match_tier"] = tier
        packed["match_score"] = score
        packed["match_jaccard"] = jac
        packed["match_reason"] = _match_reason(
            score, jac,
            numeric_conflict=conflict,
            auto_threshold=auto_threshold,
            lexical_threshold=lexical_threshold,
        )
        if tier == "auto" and score > best_auto_score:
            best_auto_score = score
            best_auto = packed
        elif tier == "flag_review" and score > best_review_score:
            best_review_score = score
            best_review = packed

    if best_auto is not None:
        return best_auto
    return best_review


def purge_ineligible_entries(conn) -> dict:
    """Blocklist + kısmi iddiaları kütüphaneden çıkarır."""
    ensure_library_table(conn)
    removed: list[int] = []

    for row in conn.execute("""
        SELECT library_id, origin_claim_id, final_verdict, reasoning
        FROM verified_claim_library
    """).fetchall():
        origin_id = row["origin_claim_id"]
        human_reviewed = 0
        evidence_stance = None
        calibration_flags = None
        if origin_id:
            vr = conn.execute("""
                SELECT human_reviewed, evidence_stance, calibration_flags
                FROM verdicts WHERE claim_id = ?
            """, (origin_id,)).fetchone()
            if vr:
                human_reviewed = vr["human_reviewed"]
                evidence_stance = vr["evidence_stance"]
                calibration_flags = vr["calibration_flags"]
        ok, reason = is_seed_eligible(
            claim_id=origin_id or -1,
            final_verdict=row["final_verdict"],
            reasoning=row["reasoning"],
            evidence_stance=evidence_stance,
            calibration_flags=calibration_flags,
            human_reviewed=human_reviewed,
        )
        if not ok:
            conn.execute("DELETE FROM verified_claim_library WHERE library_id = ?", (row["library_id"],))
            removed.append(row["origin_claim_id"])

    conn.commit()
    return {"removed_origin_ids": removed, "remaining": conn.execute(
        "SELECT COUNT(*) FROM verified_claim_library").fetchone()[0]}


def seed_from_verdicts(conn, *, video_id: str | None = None, min_confidence: float = 0.65) -> dict:
    """Onaylı, tam ve kısmi olmayan iddiaları kütüphaneye ekler."""
    ensure_library_table(conn)
    purge_ineligible_entries(conn)

    clause = "AND c.video_id = ?" if video_id else ""
    params: tuple = (video_id,) if video_id else ()

    rows = conn.execute(f"""
        SELECT c.claim_id, c.claim_text, vr.final_verdict, vr.confidence,
               vr.source_url, vr.source_tier, vr.reasoning, vr.evidence_stance,
               vr.calibration_flags, vr.human_reviewed
        FROM claims c
        JOIN verdicts vr ON vr.claim_id = c.claim_id
        WHERE c.archived_at IS NULL
          AND vr.human_reviewed = 1
          AND vr.final_verdict IN ('doğrulanmış', 'yanlış')
          AND vr.confidence >= ?
          {clause}
    """, (min_confidence, *params)).fetchall()

    added, skipped, rejected = 0, 0, 0
    eligible_rows = []
    for row in rows:
        ok, reason = is_seed_eligible(
            claim_id=row["claim_id"],
            final_verdict=row["final_verdict"],
            reasoning=row["reasoning"],
            evidence_stance=row["evidence_stance"],
            calibration_flags=row["calibration_flags"],
            human_reviewed=row["human_reviewed"],
        )
        if not ok:
            rejected += 1
            continue
        eligible_rows.append(row)

    texts = [r["claim_text"] for r in eligible_rows]
    embs = embed_texts(texts) if texts else np.empty((0, 0))

    for i, row in enumerate(eligible_rows):
        norm = normalize(row["claim_text"])
        exists = conn.execute(
            "SELECT 1 FROM verified_claim_library WHERE claim_text_norm = ?", (norm,)
        ).fetchone()
        if exists:
            skipped += 1
            continue
        conn.execute("""
            INSERT INTO verified_claim_library
                (claim_text, claim_text_norm, embedding, final_verdict, confidence,
                 source_url, source_tier, reasoning, origin_claim_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            row["claim_text"], norm, _blob_from_emb(embs[i]),
            row["final_verdict"], row["confidence"],
            row["source_url"], row["source_tier"], row["reasoning"],
            row["claim_id"],
        ))
        added += 1
    conn.commit()
    total = conn.execute("SELECT COUNT(*) FROM verified_claim_library").fetchone()[0]
    return {
        "added": added, "skipped": skipped, "rejected_partial": rejected,
        "library_total": total,
    }


def library_stats(conn) -> dict:
    ensure_library_table(conn)
    by_v = conn.execute("""
        SELECT final_verdict, COUNT(*) FROM verified_claim_library GROUP BY final_verdict
    """).fetchall()
    ids = [r[0] for r in conn.execute(
        "SELECT origin_claim_id FROM verified_claim_library ORDER BY origin_claim_id"
    ).fetchall()]
    return {"total": sum(n for _, n in by_v), "by_verdict": dict(by_v), "origin_ids": ids}
