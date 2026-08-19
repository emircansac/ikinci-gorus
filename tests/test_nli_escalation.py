import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from utils.nli import should_escalate
from utils.reasoning_patterns import (
    evidence_has_partial_caveat,
    locate_partial_caveat,
    locate_partial_caveat_in_pieces,
)


def _nli(label: str, conf: float) -> dict:
    return {"nli_label": label, "nli_confidence": conf}


def test_low_confidence_still_escalates():
    assert should_escalate(_nli("SUPPORTS", 0.5), "medium", evidence_text="plain abstract")


def test_high_conf_clean_evidence_does_not_escalate():
    assert not should_escalate(
        _nli("SUPPORTS", 0.9),
        "medium",
        evidence_text="Vitamin C reduces oxidative stress in controlled trials.",
    )


def test_high_conf_partial_evidence_escalates_en():
    text = (
        "Oxidative stress in CKD. However, an excessive amount of ROS results in "
        "oxidation of biological molecules."
    )
    assert evidence_has_partial_caveat(text)
    assert should_escalate(_nli("SUPPORTS", 0.85), "medium", evidence_text=text)


def test_high_conf_partial_evidence_escalates_tr():
    text = "Bu bulgu kısmi destek sağlar; iddia tam örtüşmüyor."
    assert evidence_has_partial_caveat(text)
    assert should_escalate(_nli("REFUTES", 0.8), "low", evidence_text=text)


def test_partial_rule_not_applied_to_nei():
    assert should_escalate(
        _nli("NOT_ENOUGH_INFO", 0.95),
        "medium",
        evidence_text="However insufficient evidence for the claim.",
    )


def test_partial_rule_not_applied_when_no_evidence_text():
    assert not should_escalate(_nli("SUPPORTS", 0.9), "medium", evidence_text=None)


def test_high_risk_always_escalates():
    assert should_escalate(_nli("SUPPORTS", 0.99), "high", evidence_text="clean support")


def test_locate_partial_caveat_phrase_however():
    text = (
        "Oxidative stress in CKD. However, an excessive amount of ROS results in "
        "oxidation of biological molecules."
    )
    assert locate_partial_caveat(text).lower() == "however"


def test_locate_partial_caveat_in_pieces_index():
    # #1282 regresyonu: caveat parça 2'de. Tek-parça snippet (pieces[:1]) kaçırır.
    pieces = [
        "Chlorogenic acid reduces oxidative damage in mesenchymal stem cells.",
        (
            "Epidemiological studies provide insights into coffee and cognition. "
            "However, future research should focus on specific bioactive compounds."
        ),
        "Colonic metabolites of chlorogenic acid may counteract TNF-alpha inflammation.",
    ]
    loc = locate_partial_caveat_in_pieces(pieces)
    assert loc["partial_caveat_matched_index"] == 1
    assert loc["partial_caveat_matched_phrase"].lower() == "however"
    assert evidence_has_partial_caveat(" ".join(pieces))
    assert locate_partial_caveat_in_pieces(pieces[:1]) is None
