"""İnsan onayı / reddi / arşiv — verdicts + claims günceller, CSV export yeniler."""
import subprocess
import sys
from pathlib import Path

from utils.db import get_conn
from utils.suspicion import compute_suspicion

ROOT = Path(__file__).parent.parent

AUTO_ARCHIVE_SCORE_THRESHOLD = 25


def _get_claim_row(conn, claim_id: int):
    return conn.execute("""
        SELECT c.claim_id, c.archived_at, vr.final_verdict, vr.confidence,
               vr.human_reviewed, vr.escalated
        FROM claims c
        LEFT JOIN verdicts vr ON vr.claim_id = c.claim_id
        WHERE c.claim_id = ?
    """, (claim_id,)).fetchone()


def _suspicion_for_row(row) -> float | None:
    parse_failed = row["final_verdict"] is None and row["escalated"] == 1
    score, _ = compute_suspicion(row["final_verdict"], row["confidence"], parse_failed=parse_failed)
    return score


def archive_claim(conn, claim_id: int, reason: str) -> None:
    conn.execute("""
        UPDATE claims SET archived_at = datetime('now'), archive_reason = ?
        WHERE claim_id = ?
    """, (reason, claim_id))


def review_claim(claim_id: int, action: str, note: str | None = None) -> dict:
    """
    action:
      approve — AI hükmü kalır; human_reviewed=1; skor ≤25 ise auto arşiv
      reject  — tartışmalı + arşiv (reject)
      archive — hüküm değişmez; arşiv (manual); human_reviewed=1
    """
    if action not in ("approve", "reject", "archive"):
        return {"ok": False, "error": "action approve, reject veya archive olmalı"}

    conn = get_conn()
    row = _get_claim_row(conn, claim_id)
    if not row:
        conn.close()
        return {"ok": False, "error": "iddia bulunamadı"}
    if row["archived_at"]:
        conn.close()
        return {"ok": False, "error": "bu iddia zaten arşivde"}

    archived = False
    archive_reason = None

    if action == "archive":
        conn.execute("""
            UPDATE verdicts SET human_reviewed = 1, auto_accepted = 0, reviewer_note = ?, verified_at = datetime('now')
            WHERE claim_id = ?
        """, (note or "arşivlendi", claim_id))
        archive_claim(conn, claim_id, "manual")
        archived = True
        archive_reason = "manual"
    elif action == "approve":
        if row["human_reviewed"]:
            conn.close()
            return {"ok": False, "error": "bu iddia zaten incelenmiş"}
        conn.execute("""
            UPDATE verdicts SET human_reviewed = 1, auto_accepted = 0, reviewer_note = ?, verified_at = datetime('now')
            WHERE claim_id = ?
        """, (note or "onaylandı", claim_id))
        updated = _get_claim_row(conn, claim_id)
        score = _suspicion_for_row(updated)
        if score is not None and score <= AUTO_ARCHIVE_SCORE_THRESHOLD:
            archive_claim(conn, claim_id, "auto_low_risk")
            archived = True
            archive_reason = "auto_low_risk"
    else:  # reject
        if row["human_reviewed"]:
            conn.close()
            return {"ok": False, "error": "bu iddia zaten incelenmiş"}
        conn.execute("""
            UPDATE verdicts
            SET human_reviewed = 1,
                auto_accepted = 0,
                final_verdict = 'tartışmalı',
                confidence = COALESCE(confidence, 0.5),
                reviewer_note = ?,
                verified_at = datetime('now')
            WHERE claim_id = ?
        """, (note or "reddedildi — insan incelemesi", claim_id))
        archive_claim(conn, claim_id, "reject")
        archived = True
        archive_reason = "reject"

    conn.commit()
    conn.close()
    refresh_dashboard_exports()
    return {
        "ok": True,
        "claim_id": claim_id,
        "action": action,
        "archived": archived,
        "archive_reason": archive_reason,
    }


def refresh_dashboard_exports():
    """Dashboard CSV'lerini DB'den yeniden üret."""
    subprocess.run(
        [sys.executable, str(ROOT / "pipeline" / "04_score_suspects.py"), "--export", "data/suspects.csv"],
        cwd=ROOT, check=True,
    )
    subprocess.run(
        [sys.executable, str(ROOT / "pipeline" / "06_claim_index.py"), "--export-dir", "data"],
        cwd=ROOT, check=True,
    )
