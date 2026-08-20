import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from utils.suspicion import compute_channel_risk, compute_priority, compute_suspicion


def test_suspicion_yanlis_high_confidence():
    score, note = compute_suspicion("yanlış", 0.9)
    assert score == 95.0
    assert note == "yüksek_şüpheli"


def test_suspicion_yanlis_low_confidence():
    score, note = compute_suspicion("yanlış", 0.3)
    assert score == 65.0
    assert note == "şüpheli"


def test_suspicion_dogrulanmis():
    score, note = compute_suspicion("doğrulanmış", 0.9)
    assert score == 5.0
    assert note == "şüphesiz"


def test_suspicion_belirsiz():
    score, note = compute_suspicion("belirsiz", 0.8)
    assert score == 50.0
    assert note == "belirsiz"


def test_suspicion_veri_eksik():
    score, note = compute_suspicion(None, 0.5)
    assert score is None
    assert note == "veri_eksik"


def test_priority_high_stakes_category():
    priority = compute_priority(92.5, "tanı", channels_affected=2)
    assert priority > 80


def test_priority_low_suspicion_no_boost():
    priority = compute_priority(30.0, "tanı", channels_affected=5)
    assert priority == 0.0


def test_channel_risk_uses_scored_suspicion_not_all_claims():
    """Kontrol edilmemiş iddialar skoru yapay düşürmemeli."""
    rows = [{"final_verdict": "yanlış", "confidence": 0.9, "initial_risk": "medium"}] * 5
    rows += [{"final_verdict": None, "confidence": None, "initial_risk": "low"}] * 95
    score, tier, meta = compute_channel_risk(rows)
    assert score is not None
    assert score > 15
    assert meta["fact_check_coverage"] == 0.05
    assert tier != "acil"


def test_channel_risk_low_coverage_caps_acil():
    rows = [{"final_verdict": "yanlış", "confidence": 1.0, "initial_risk": "high"}] * 3
    rows += [{"final_verdict": None, "confidence": None, "initial_risk": "high"}] * 17
    score, tier, meta = compute_channel_risk(rows, funnel_flag=True, ai_persona_flag=True)
    assert meta["fact_check_coverage"] == 0.15
    assert tier == "incele"


def test_channel_risk_uses_row_confidence_not_default():
    """04 SELECT'ten confidence düşerse compute_suspicion 0.5 varsayar; skor şişer."""
    with_conf = [{"final_verdict": "tartışmalı", "confidence": 0.4, "initial_risk": "medium"}] * 10
    no_conf = [{"final_verdict": "tartışmalı", "initial_risk": "medium"}] * 10
    score_real, _, meta_real = compute_channel_risk(with_conf)
    score_default, _, meta_default = compute_channel_risk(no_conf)
    assert meta_real["avg_suspicion"] == 58.0  # 50 + 0.4 * 0.4 * 50
    assert meta_default["avg_suspicion"] == 60.0  # 50 + 0.5 * 0.4 * 50
    assert score_real < score_default


def test_score_suspects_selects_confidence():
    src = (Path(__file__).parent.parent / "pipeline" / "04_score_suspects.py").read_text(
        encoding="utf-8"
    )
    assert "vr.confidence" in src


def test_channel_risk_clamps_theoretical_120_to_100():
    """Bileşen tavanları 120 edebilir; çıktı min(100, toplam) ile kırpılmalı."""
    rows = [{"final_verdict": "yanlış", "confidence": 1.0, "initial_risk": "high"}]
    rows += [{"final_verdict": None, "confidence": None, "initial_risk": "high"}] * 99
    score, tier, meta = compute_channel_risk(
        rows,
        funnel_flag=True,
        ai_persona_flag=True,
        growth_anomaly_flag=True,
        bot_comment_ratio=1.0,
    )
    component_sum = sum(meta["score_components"].values())
    assert component_sum > 100
    assert score == 100.0
    assert 0.0 <= score <= 100.0
    src = (Path(__file__).parent.parent / "utils" / "suspicion.py").read_text(encoding="utf-8")
    assert "min(100.0, max(0.0, score))" in src
    # kapsam %1 → acil yasağı incele'ye çeker; kırpma skoru 100 bırakır
    assert meta["fact_check_coverage"] == 0.01
    assert tier == "incele"
