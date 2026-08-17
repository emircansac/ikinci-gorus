import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from utils.factcheck_review import (
    is_drug_interaction_claim,
    compute_needs_human,
    apply_verdict_reasoning_mismatch,
    review_flags,
    VERDICT_REASONING_MISMATCH_FLAG,
)


def test_drug_interaction_claim_detected():
    text = "Lahana ve marulda bulunan yüksek K vitamini, kan sulandırıcı antikoagülan ilaçlarla doğrudan etkileşime girer."
    assert is_drug_interaction_claim(text)


def test_non_drug_claim_not_flagged():
    assert not is_drug_interaction_claim("Ispanak potasyum içerir")


def test_indirect_non_escalated_needs_human():
    """Claim 673 tipi: NLI yolu, escalate yok, indirect kanıt → insan kuyruğu."""
    assert compute_needs_human(
        category="mekanizma",
        initial_risk="low",
        claim_text="Kırmızı biberdeki folik asit homosisteini zararsız maddelere dönüştürür.",
        parse_failed=False,
        final_verdict="doğrulanmış",
        escalated_flag=0,
        calibrated={},
        source_directness="indirect",
        library_review_hit=None,
    )


def test_indirect_non_escalated_review_flags():
    human_reviewed, auto_accepted = review_flags(
        needs_human=compute_needs_human(
            category="mekanizma",
            initial_risk="low",
            claim_text="test",
            parse_failed=False,
            final_verdict="doğrulanmış",
            escalated_flag=0,
            calibrated={},
            source_directness="indirect",
        )
    )
    assert human_reviewed == 0
    assert auto_accepted == 0


def test_direct_low_risk_can_auto_accept():
    assert not compute_needs_human(
        category="diğer",
        initial_risk="low",
        claim_text="test",
        parse_failed=False,
        final_verdict="doğrulanmış",
        escalated_flag=0,
        calibrated={},
        source_directness="direct",
    )
    _, auto_accepted = review_flags(needs_human=False)
    assert auto_accepted == 1


def test_671_verdict_reasoning_mismatch_flags_and_needs_human():
    reasoning = (
        "Koruyucu madde (katkı maddesi) iddiası spesifik olarak kanıtlanmadı, "
        "ancak yüksek sodyum kısmı destekleniyor."
    )
    final = {"final_verdict": "doğrulanmış", "reasoning": reasoning, "calibration_flags": ""}
    assert apply_verdict_reasoning_mismatch(final)
    assert VERDICT_REASONING_MISMATCH_FLAG in final["calibration_flags"]
    assert compute_needs_human(
        category="mekanizma",
        initial_risk="medium",
        claim_text="test",
        parse_failed=False,
        final_verdict="doğrulanmış",
        escalated_flag=1,
        calibrated={},
        source_directness="direct",
        calibration_flags=final["calibration_flags"],
    )
    _, auto_accepted = review_flags(
        needs_human=compute_needs_human(
            category="mekanizma",
            initial_risk="medium",
            claim_text="test",
            parse_failed=False,
            final_verdict="doğrulanmış",
            escalated_flag=1,
            calibrated={},
            source_directness="direct",
            calibration_flags=final["calibration_flags"],
        )
    )
    assert auto_accepted == 0


def test_tartışmalı_skips_mismatch_check():
    final = {
        "final_verdict": "tartışmalı",
        "reasoning": "Koruyucu madde kısmı kanıtlanmadı.",
        "calibration_flags": "",
    }
    assert not apply_verdict_reasoning_mismatch(final)
    assert VERDICT_REASONING_MISMATCH_FLAG not in (final.get("calibration_flags") or "")


def test_package_only_forced_always_needs_human():
    from utils.factcheck_review import PACKAGE_ONLY_FORCED_FLAG
    assert compute_needs_human(
        category="diğer",
        initial_risk="low",
        claim_text="Ölçülü kahve Alzheimer riskini azaltır.",
        parse_failed=False,
        final_verdict="tartışmalı",
        escalated_flag=1,
        calibrated={"needs_human": False},
        source_directness="direct",
        calibration_flags=PACKAGE_ONLY_FORCED_FLAG,
    )
    assert not compute_needs_human(
        category="diğer",
        initial_risk="low",
        claim_text="Ölçülü kahve Alzheimer riskini azaltır.",
        parse_failed=False,
        final_verdict="tartışmalı",
        escalated_flag=1,
        calibrated={"needs_human": False},
        source_directness="direct",
        calibration_flags="retrieval_cited",
    )
