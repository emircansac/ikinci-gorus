"""utils.review — onayla/reddet/arşiv; reddet artık seçilen verdict yazar."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


def _isolated_db(monkeypatch, tmp_path):
    db_path = tmp_path / "monitor.db"
    monkeypatch.setattr("utils.db.DB_PATH", db_path)
    from utils.db import init_db
    init_db()
    monkeypatch.setattr("utils.review.refresh_dashboard_exports", lambda: None)
    return db_path


def _seed_claim(
    claim_id: int,
    *,
    verdict="yanlış",
    confidence=0.8,
    human_reviewed=0,
    calibration_flags=None,
):
    from utils.db import get_conn
    conn = get_conn()
    conn.execute(
        "INSERT OR REPLACE INTO channels (channel_id, name) VALUES (?, ?)",
        ("DEMO_TEST_CH", "Test Kanal"),
    )
    conn.execute(
        "INSERT OR REPLACE INTO videos (video_id, channel_id, title) VALUES (?, ?, ?)",
        ("DEMO_TEST_V", "DEMO_TEST_CH", "Test video"),
    )
    conn.execute(
        """
        INSERT INTO claims (claim_id, video_id, channel_id, timestamp_sec, claim_text, category, initial_risk)
        VALUES (?, 'DEMO_TEST_V', 'DEMO_TEST_CH', 0, 'test iddiası', 'diğer', 'low')
        """,
        (claim_id,),
    )
    conn.execute(
        """
        INSERT INTO verdicts (claim_id, final_verdict, confidence, human_reviewed, auto_accepted,
                              calibration_flags)
        VALUES (?, ?, ?, ?, 0, ?)
        """,
        (claim_id, verdict, confidence, human_reviewed, calibration_flags),
    )
    conn.commit()
    conn.close()


def _row(claim_id: int):
    from utils.db import get_conn
    conn = get_conn()
    row = conn.execute(
        """
        SELECT c.archived_at, c.archive_reason, v.final_verdict, v.human_reviewed,
               v.auto_accepted, v.reviewer_note
        FROM claims c
        JOIN verdicts v ON v.claim_id = c.claim_id
        WHERE c.claim_id = ?
        """,
        (claim_id,),
    ).fetchone()
    conn.close()
    return row


def test_reject_writes_chosen_verdict(monkeypatch, tmp_path):
    _isolated_db(monkeypatch, tmp_path)
    _seed_claim(900001, verdict="doğrulanmış")
    from utils.review import review_claim
    result = review_claim(900001, "reject", verdict="yanlış")
    assert result["ok"] is True
    assert result["verdict"] == "yanlış"
    assert result["archived"] is True
    assert result["archive_reason"] == "reject"
    row = _row(900001)
    assert row["final_verdict"] == "yanlış"
    assert row["human_reviewed"] == 1
    assert row["auto_accepted"] == 0
    assert row["archive_reason"] == "reject"
    assert row["archived_at"]


def test_reject_without_verdict_defaults_tartismali(monkeypatch, tmp_path):
    _isolated_db(monkeypatch, tmp_path)
    _seed_claim(900002, verdict="yanlış")
    from utils.review import review_claim
    result = review_claim(900002, "reject")
    assert result["ok"] is True
    assert result["verdict"] == "tartışmalı"
    row = _row(900002)
    assert row["final_verdict"] == "tartışmalı"
    assert row["human_reviewed"] == 1
    assert row["archive_reason"] == "reject"


def test_reject_invalid_verdict_does_not_write(monkeypatch, tmp_path):
    _isolated_db(monkeypatch, tmp_path)
    _seed_claim(900003, verdict="yanlış")
    from utils.review import review_claim
    result = review_claim(900003, "reject", verdict="uydurma")
    assert result["ok"] is False
    row = _row(900003)
    assert row["final_verdict"] == "yanlış"
    assert row["human_reviewed"] == 0
    assert row["archived_at"] is None


def test_approve_keeps_verdict(monkeypatch, tmp_path):
    _isolated_db(monkeypatch, tmp_path)
    _seed_claim(900004, verdict="yanlış", confidence=0.8)
    from utils.review import review_claim
    result = review_claim(900004, "approve")
    assert result["ok"] is True
    row = _row(900004)
    assert row["final_verdict"] == "yanlış"
    assert row["human_reviewed"] == 1
    assert row["auto_accepted"] == 0
    assert row["reviewer_note"] == "onaylandı"


def test_archive_keeps_verdict(monkeypatch, tmp_path):
    _isolated_db(monkeypatch, tmp_path)
    _seed_claim(900005, verdict="belirsiz")
    from utils.review import review_claim
    result = review_claim(900005, "archive")
    assert result["ok"] is True
    assert result["archive_reason"] == "manual"
    row = _row(900005)
    assert row["final_verdict"] == "belirsiz"
    assert row["human_reviewed"] == 1
    assert row["archive_reason"] == "manual"


def test_review_does_not_call_export_and_is_fast(monkeypatch, tmp_path):
    """Onayla 04/06 çalıştırmaz; tıklama milisaniyeler sürmelidir."""
    import time
    _isolated_db(monkeypatch, tmp_path)
    _seed_claim(900010, verdict="yanlış", confidence=0.8)

    def boom():
        raise RuntimeError("export should not run on review")

    monkeypatch.setattr("utils.review.refresh_dashboard_exports", boom)
    from utils.review import review_claim
    t0 = time.perf_counter()
    result = review_claim(900010, "approve")
    elapsed = time.perf_counter() - t0
    assert result["ok"] is True
    assert elapsed < 0.5


def _outcomes(claim_id: int | None = None):
    from utils.db import get_conn
    conn = get_conn()
    if claim_id is None:
        rows = conn.execute("SELECT * FROM review_outcomes ORDER BY id").fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM review_outcomes WHERE claim_id = ? ORDER BY id",
            (claim_id,),
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def test_approve_writes_agreed_outcome(monkeypatch, tmp_path):
    _isolated_db(monkeypatch, tmp_path)
    _seed_claim(900020, verdict="yanlış", confidence=0.8)
    from utils.review import review_claim
    result = review_claim(900020, "approve")
    assert result["ok"] is True
    rows = _outcomes(900020)
    assert len(rows) == 1
    assert rows[0]["ai_verdict"] == "yanlış"
    assert rows[0]["human_verdict"] == "yanlış"
    assert rows[0]["agreed"] == 1
    assert rows[0]["ai_confidence"] == 0.8


def test_reject_writes_disagreed_outcome(monkeypatch, tmp_path):
    _isolated_db(monkeypatch, tmp_path)
    _seed_claim(900021, verdict="doğrulanmış", confidence=0.7)
    from utils.review import review_claim
    result = review_claim(900021, "reject", verdict="tartışmalı")
    assert result["ok"] is True
    rows = _outcomes(900021)
    assert len(rows) == 1
    assert rows[0]["ai_verdict"] == "doğrulanmış"
    assert rows[0]["human_verdict"] == "tartışmalı"
    assert rows[0]["agreed"] == 0


def test_archive_does_not_write_outcome(monkeypatch, tmp_path):
    _isolated_db(monkeypatch, tmp_path)
    _seed_claim(900022, verdict="belirsiz")
    from utils.review import review_claim
    review_claim(900022, "archive")
    assert _outcomes(900022) == []


def test_invalid_reject_does_not_write_outcome(monkeypatch, tmp_path):
    _isolated_db(monkeypatch, tmp_path)
    _seed_claim(900023, verdict="yanlış")
    from utils.review import review_claim
    review_claim(900023, "reject", verdict="uydurma")
    assert _outcomes(900023) == []


def test_second_approve_does_not_write_second_outcome(monkeypatch, tmp_path):
    _isolated_db(monkeypatch, tmp_path)
    _seed_claim(900024, verdict="yanlış")
    from utils.review import review_claim
    review_claim(900024, "approve")
    review_claim(900024, "approve")
    assert len(_outcomes(900024)) == 1


def test_approve_records_check_point_category(monkeypatch, tmp_path):
    _isolated_db(monkeypatch, tmp_path)
    _seed_claim(
        900025,
        verdict="doğrulanmış",
        confidence=0.85,
        calibration_flags="verdict_reasoning_mismatch,web_search_override",
    )
    from utils.review import review_claim
    review_claim(900025, "approve")
    rows = _outcomes(900025)
    assert rows[0]["reviewer_check_point_category"] == "verdict_reasoning_mismatch"


def test_approve_null_verdict_agreed_zero(monkeypatch, tmp_path):
    _isolated_db(monkeypatch, tmp_path)
    _seed_claim(900026, verdict=None, confidence=None)
    from utils.review import review_claim
    result = review_claim(900026, "approve")
    assert result["ok"] is True
    rows = _outcomes(900026)
    assert len(rows) == 1
    assert rows[0]["ai_verdict"] is None
    assert rows[0]["human_verdict"] is None
    assert rows[0]["agreed"] == 0
