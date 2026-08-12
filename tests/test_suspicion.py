import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from utils.suspicion import compute_priority, compute_suspicion


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
