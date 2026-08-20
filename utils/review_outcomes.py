"""İnceleme sonuçları — öğrenme kaydı (davranış değiştirmez)."""


def ensure_review_outcomes_table(conn) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS review_outcomes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            claim_id INTEGER NOT NULL,
            reviewed_at TEXT DEFAULT (datetime('now')),
            ai_verdict TEXT,
            ai_confidence REAL,
            human_verdict TEXT,
            agreed INTEGER NOT NULL,
            calibration_flags_at_review TEXT,
            specificity_tier_at_review TEXT,
            reviewer_check_point_category TEXT,
            FOREIGN KEY (claim_id) REFERENCES claims(claim_id)
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_review_outcomes_reviewed_at "
        "ON review_outcomes(reviewed_at)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_review_outcomes_category "
        "ON review_outcomes(reviewer_check_point_category)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_review_outcomes_agreed "
        "ON review_outcomes(agreed)"
    )
    conn.commit()


def insert_review_outcome(
    conn,
    claim_row,
    *,
    human_verdict: str | None,
    ai_verdict: str | None,
    ai_confidence: float | None,
) -> None:
    from utils.reviewer_summary import (
        _parse_flags,
        _specificity_tier,
        check_point_category,
    )

    row = dict(claim_row) if claim_row is not None and not isinstance(claim_row, dict) else (claim_row or {})
    flags = _parse_flags(row.get("calibration_flags"))
    agreed = int(ai_verdict is not None and ai_verdict == human_verdict)
    conn.execute(
        """
        INSERT INTO review_outcomes (
            claim_id, ai_verdict, ai_confidence, human_verdict, agreed,
            calibration_flags_at_review, specificity_tier_at_review,
            reviewer_check_point_category
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            int(row["claim_id"]),
            ai_verdict,
            ai_confidence,
            human_verdict,
            agreed,
            row.get("calibration_flags"),
            _specificity_tier(row, flags),
            check_point_category(row, flags),
        ),
    )
