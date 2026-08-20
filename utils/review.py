"""İnsan onayı / reddi / arşiv — verdicts + claims günceller.

Ağır CSV yenileme (04 + 06) burada çağrılmaz; app.py periyodik export işi yapar.
"""
import subprocess
import sys
from pathlib import Path

from utils.db import get_conn
from utils.review_outcomes import insert_review_outcome
from utils.suspicion import compute_suspicion

ROOT = Path(__file__).parent.parent

AUTO_ARCHIVE_SCORE_THRESHOLD = 25


def _get_claim_row(conn, claim_id: int):
    return conn.execute("""
        SELECT c.claim_id, c.archived_at, c.claim_text,
               vr.final_verdict, vr.confidence, vr.human_reviewed, vr.escalated,
               vr.calibration_flags, vr.reasoning, vr.evidence_stance,
               vr.source_directness, vr.nli_label, vr.nli_confidence
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


VALID_REVIEW_VERDICTS = frozenset({"doğrulanmış", "yanlış", "tartışmalı", "belirsiz"})


def review_claim(
    claim_id: int,
    action: str,
    note: str | None = None,
    verdict: str | None = None,
) -> dict:
    """
    action:
      approve — AI hükmü kalır; human_reviewed=1; skor ≤25 ise auto arşiv
      reject  — seçilen (veya varsayılan tartışmalı) hüküm + arşiv (reject)
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
    record_outcome = False
    human_verdict = None
    ai_verdict = row["final_verdict"]
    ai_confidence = row["confidence"]

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
        record_outcome = True
        human_verdict = ai_verdict
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
        chosen = (verdict or "").strip() or "tartışmalı"
        if chosen not in VALID_REVIEW_VERDICTS:
            conn.close()
            return {
                "ok": False,
                "error": "verdict doğrulanmış, yanlış, tartışmalı veya belirsiz olmalı",
            }
        conn.execute("""
            UPDATE verdicts
            SET human_reviewed = 1,
                auto_accepted = 0,
                final_verdict = ?,
                confidence = COALESCE(confidence, 0.5),
                reviewer_note = ?,
                verified_at = datetime('now')
            WHERE claim_id = ?
        """, (chosen, note or "reddedildi — insan incelemesi", claim_id))
        archive_claim(conn, claim_id, "reject")
        archived = True
        archive_reason = "reject"
        record_outcome = True
        human_verdict = chosen

    if record_outcome:
        insert_review_outcome(
            conn,
            row,
            human_verdict=human_verdict,
            ai_verdict=ai_verdict,
            ai_confidence=ai_confidence,
        )

    conn.commit()
    after = _get_claim_row(conn, claim_id)
    conn.close()
    result = {
        "ok": True,
        "claim_id": claim_id,
        "action": action,
        "archived": archived,
        "archive_reason": archive_reason,
        "human_reviewed": 1,
        "final_verdict": after["final_verdict"] if after else None,
        "reviewer_note": {
            "approve": note or "onaylandı",
            "reject": note or "reddedildi — insan incelemesi",
            "archive": note or "arşivlendi",
        }[action],
    }
    if action == "reject":
        result["verdict"] = chosen
    return result


def refresh_dashboard_exports():
    """Dashboard CSV'lerini DB'den yeniden üret (04 + 06). Review tıklamasında çağrılmaz."""
    subprocess.run(
        [sys.executable, str(ROOT / "pipeline" / "04_score_suspects.py"), "--export", "data/suspects.csv"],
        cwd=ROOT, check=True,
    )
    subprocess.run(
        [sys.executable, str(ROOT / "pipeline" / "06_claim_index.py"), "--export-dir", "data"],
        cwd=ROOT, check=True,
    )
