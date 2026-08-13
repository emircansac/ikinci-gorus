"""
Aşama 2 iddialarının DB'ye güvenli yazılması — API başarısından sonra arşivle + ekle.
"""
from __future__ import annotations

import os

DEFAULT_EXTRACTION_VERSION = os.environ.get("EXTRACTION_VERSION", "v2")

# Downstream pipeline'lar (fact-check, skor) yalnızca aktif iddiaları işlemeli.
ACTIVE_CLAIM_WHERE = "archived_at IS NULL"


def fetch_active_claims(conn, video_id: str) -> list[dict]:
    rows = conn.execute("""
        SELECT claim_id, timestamp_sec, claim_text, category, initial_risk, extraction_version
        FROM claims
        WHERE video_id = ? AND archived_at IS NULL
        ORDER BY claim_id
    """, (video_id,)).fetchall()
    return [dict(r) for r in rows]


def archive_superseded_claims(conn, video_id: str, new_version: str) -> int:
    """Aktif iddiaları silmeden arşivler (verdict kayıtları korunur)."""
    cur = conn.execute("""
        UPDATE claims
        SET archived_at = datetime('now'),
            archive_reason = ?
        WHERE video_id = ?
          AND archived_at IS NULL
          AND (extraction_version IS NULL OR extraction_version != ?)
    """, (f"superseded_{new_version}", video_id, new_version))
    conn.commit()
    return cur.rowcount


def insert_claims_batch(
    conn,
    video_id: str,
    channel_id: str,
    claims: list[dict],
    extraction_version: str = DEFAULT_EXTRACTION_VERSION,
) -> int:
    for c in claims:
        conn.execute("""
            INSERT INTO claims (
                video_id, channel_id, timestamp_sec, claim_text, search_query_en,
                category, initial_risk, extraction_version
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            video_id,
            channel_id,
            c.get("timestamp_sec"),
            c["claim_text"],
            c.get("search_query_en"),
            c.get("category", "diğer"),
            c.get("initial_risk", "medium"),
            extraction_version,
        ))
    conn.execute("""
        UPDATE videos
        SET claims_extracted_at = datetime('now'),
            active_extraction_version = ?
        WHERE video_id = ?
    """, (extraction_version, video_id))
    conn.commit()
    return len(claims)


def promote_extraction(
    conn,
    video_id: str,
    channel_id: str,
    claims: list[dict],
    extraction_version: str = DEFAULT_EXTRACTION_VERSION,
    *,
    carryover_verdicts: bool = False,
) -> dict:
    """
    API başarılı olduktan sonra çağrılır: eski aktif iddiaları arşivler, yenilerini ekler.
    Hata durumunda çağrılmamalı — mevcut aktif iddialar olduğu gibi kalır.
    """
    archived = archive_superseded_claims(conn, video_id, extraction_version)
    inserted = insert_claims_batch(conn, video_id, channel_id, claims, extraction_version)
    result = {"archived": archived, "inserted": inserted, "extraction_version": extraction_version}
    if carryover_verdicts and archived:
        from utils.verdict_carryover import carryover_verdicts as _carryover
        result["verdict_carryover"] = _carryover(conn, video_id)
    return result
