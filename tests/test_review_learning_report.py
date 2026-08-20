"""pipeline/22_review_learning_report — boş tablo ve fixture özeti."""
import importlib.util
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

ROOT = Path(__file__).parent.parent


def _load_report():
    spec = importlib.util.spec_from_file_location(
        "review_learning_report22",
        ROOT / "pipeline" / "22_review_learning_report.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _isolated_db(monkeypatch, tmp_path):
    db_path = tmp_path / "monitor.db"
    monkeypatch.setattr("utils.db.DB_PATH", db_path)
    from utils.db import init_db
    init_db()
    return db_path


def test_empty_report(monkeypatch, tmp_path):
    _isolated_db(monkeypatch, tmp_path)
    from utils.db import get_conn
    mod = _load_report()
    conn = get_conn()
    stats = mod.fetch_stats(conn)
    body = mod.render_report(stats)
    conn.close()
    assert stats["n"] == 0
    assert "Toplam review: **0**" in body
    assert "Henüz kayıt yok" in body


def test_fixture_report_shows_n_pattern_and_category(monkeypatch, tmp_path):
    _isolated_db(monkeypatch, tmp_path)
    from utils.db import get_conn
    conn = get_conn()
    conn.execute(
        "INSERT INTO channels (channel_id, name) VALUES ('CH', 'T')"
    )
    conn.execute(
        "INSERT INTO videos (video_id, channel_id, title) VALUES ('V', 'CH', 'v')"
    )
    for cid in (1, 2, 3, 4):
        conn.execute(
            """
            INSERT INTO claims (claim_id, video_id, channel_id, timestamp_sec,
                                claim_text, category, initial_risk)
            VALUES (?, 'V', 'CH', 0, 'x', 'diğer', 'low')
            """,
            (cid,),
        )
    rows = [
        (1, "doğrulanmış", 0.85, "tartışmalı", 0, "verdict_reasoning_mismatch", "direct"),
        (2, "doğrulanmış", 0.85, "tartışmalı", 0, "verdict_reasoning_mismatch", "direct"),
        (3, "yanlış", 0.72, "yanlış", 1, "no_direct_evidence", "background"),
        (4, "tartışmalı", 0.55, "belirsiz", 0, "compound", "supportive"),
    ]
    for cid, ai, conf, human, agreed, cat, tier in rows:
        conn.execute(
            """
            INSERT INTO review_outcomes (
                claim_id, ai_verdict, ai_confidence, human_verdict, agreed,
                calibration_flags_at_review, specificity_tier_at_review,
                reviewer_check_point_category
            ) VALUES (?, ?, ?, ?, ?, '', ?, ?)
            """,
            (cid, ai, conf, human, agreed, tier, cat),
        )
    conn.commit()
    mod = _load_report()
    body = mod.render_report(mod.fetch_stats(conn))
    conn.close()
    assert "Toplam review: **4**" in body
    assert "Disagreed: **3**" in body
    assert "doğrulanmış→tartışmalı" in body
    assert "verdict_reasoning_mismatch" in body
    assert "no_direct_evidence" in body
    assert "[0.80,0.90)" in body or "[0.50,0.60)" in body
