import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from utils.factcheck_review import (
    is_drug_interaction_claim,
    compute_needs_human,
    apply_verdict_reasoning_mismatch,
    apply_compound_component_cap,
    COMPOUND_TIER_MISMATCH_FLAG,
    review_flags,
    VERDICT_REASONING_MISMATCH_FLAG,
    security_risk_triggers,
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


def test_compound_tier_mismatch_caps_doğrulanmış():
    """#1284: Alzheimer supportive + Parkinson direct → tartışmalı."""
    final = {
        "final_verdict": "doğrulanmış",
        "confidence": 0.82,
        "reasoning": "Her iki hastalık için destek var.",
        "calibration_flags": "",
    }
    component_map = {
        "components": [
            {"text": "Ölçülü kahve tüketimi Alzheimer", "tier": "supportive", "kept": 3},
            {"text": "Parkinson riskini azaltır", "tier": "direct", "kept": 2},
        ]
    }
    assert apply_compound_component_cap(final, component_map)
    assert final["final_verdict"] == "tartışmalı"
    assert COMPOUND_TIER_MISMATCH_FLAG in final["calibration_flags"]


def test_compound_same_tier_no_cap():
    final = {
        "final_verdict": "doğrulanmış",
        "confidence": 0.82,
        "reasoning": "Her iki bileşen direct.",
        "calibration_flags": "",
    }
    component_map = {
        "components": [
            {"text": "A", "tier": "direct", "kept": 2},
            {"text": "B", "tier": "direct", "kept": 2},
        ]
    }
    assert not apply_compound_component_cap(final, component_map)
    assert final["final_verdict"] == "doğrulanmış"


def test_compound_tier_mismatch_blocks_auto_accept_without_package_only():
    """Farklı-tier bileşik, package_only_forced YOK → auto_accepted=0."""
    final = {
        "final_verdict": "doğrulanmış",
        "confidence": 0.82,
        "reasoning": "Her iki hastalık için destek var.",
        "calibration_flags": "",
    }
    component_map = {
        "components": [
            {"text": "Ölçülü kahve tüketimi Alzheimer", "tier": "supportive", "kept": 3},
            {"text": "Parkinson riskini azaltır", "tier": "direct", "kept": 2},
        ]
    }
    assert apply_compound_component_cap(final, component_map)
    assert COMPOUND_TIER_MISMATCH_FLAG in final["calibration_flags"]
    assert "package_only_forced" not in (final.get("calibration_flags") or "")
    triggers = security_risk_triggers(
        category="diğer",
        initial_risk="low",
        claim_text="Ölçülü kahve Alzheimer ve Parkinson riskini azaltır.",
        calibration_flags=final.get("calibration_flags"),
    )
    assert "compound_tier_mismatch" in triggers
    needs_human = compute_needs_human(
        category="diğer",
        initial_risk="low",
        claim_text="Ölçülü kahve Alzheimer ve Parkinson riskini azaltır.",
        parse_failed=False,
        final_verdict=final["final_verdict"],
        escalated_flag=1,
        calibrated={"needs_human": False},
        source_directness="direct",
        calibration_flags=final.get("calibration_flags"),
    )
    assert needs_human
    _, auto_accepted = review_flags(needs_human=needs_human)
    assert auto_accepted == 0


def test_compound_same_tier_auto_accept_unchanged():
    """Aynı-tier bileşik → cap yok, auto-accept adayı değişmez."""
    final = {
        "final_verdict": "doğrulanmış",
        "confidence": 0.82,
        "reasoning": "Her iki bileşen direct.",
        "calibration_flags": "",
    }
    component_map = {
        "components": [
            {"text": "A", "tier": "direct", "kept": 2},
            {"text": "B", "tier": "direct", "kept": 2},
        ]
    }
    assert not apply_compound_component_cap(final, component_map)
    needs_human = compute_needs_human(
        category="diğer",
        initial_risk="low",
        claim_text="test",
        parse_failed=False,
        final_verdict=final["final_verdict"],
        escalated_flag=0,
        calibrated={},
        source_directness="direct",
        calibration_flags=final.get("calibration_flags"),
    )
    assert not needs_human
    _, auto_accepted = review_flags(needs_human=needs_human)
    assert auto_accepted == 1


def test_stale_auto_accept_drug_interaction_like_709():
    """#709 tipi: ilaç etkileşimi iddiası auto_accepted=1 olmamalı."""
    from utils.factcheck_review import stale_auto_accept_reasons

    reasons = stale_auto_accept_reasons(
        category="önleme",
        initial_risk="medium",
        claim_text="Bazı bitki çayları ilaçlarla tehlikeli etkileşimlere girebilir.",
        final_verdict="doğrulanmış",
        confidence=0.65,
        source_url="https://www.nccih.nih.gov/health/providers/digest/herb-drug-interactions",
        reasoning="NCCIH ilaç etkileşimlerini doğruluyor.",
        source_directness="direct",
        evidence_stance="supports",
        source_tier="guideline",
        calibration_flags="tier_url:guideline->other,tier_cap:other",
        escalated=1,
    )
    assert "drug_interaction" in reasons


def test_stale_auto_accept_partial_caveat_nli_snippet():
    from utils.factcheck_review import stale_auto_accept_reasons

    reasons = stale_auto_accept_reasons(
        category="mekanizma",
        initial_risk="low",
        claim_text="Test iddia.",
        final_verdict="doğrulanmış",
        confidence=0.8,
        source_url="https://example.com",
        reasoning="Destek var.",
        source_directness="direct",
        evidence_stance="supports",
        source_tier="primary_study",
        calibration_flags="retrieval_cited,specificity_tier:background",
        escalated=1,
        nli_evidence_snippet="Results support the claim. However, limitations apply.",
    )
    assert "partial_caveat" in reasons


def test_stale_auto_accept_legit_background_claim():
    from utils.factcheck_review import stale_auto_accept_reasons

    reasons = stale_auto_accept_reasons(
        category="mekanizma",
        initial_risk="low",
        claim_text="Güneş ışığı D vitamini üretimini destekler.",
        final_verdict="doğrulanmış",
        confidence=0.8,
        source_url="https://pubmed.ncbi.nlm.nih.gov/123/",
        reasoning="Mekanizma iyi bilinir.",
        source_directness="direct",
        evidence_stance="supports",
        source_tier="primary_study",
        calibration_flags="retrieval_cited,specificity_tier:background",
        escalated=1,
    )
    assert reasons == []
