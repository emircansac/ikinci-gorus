"""reviewer_summary birim testleri."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from utils.reviewer_summary import (
    build_reviewer_summary,
    decompose_claim_for_retrieval,
    is_compound_claim,
    would_auto_accept_v1,
    compute_shadow_human_gates,
)


def test_mismatch_check_point():
    row = {
        "final_verdict": "doğrulanmış",
        "reasoning": "Sodyum kısmı destekleniyor, ancak koruyucu madde iddiası kanıtlanmadı.",
        "calibration_flags": "verdict_reasoning_mismatch,web_search_override",
        "category": "mekanizma",
        "initial_risk": "medium",
        "claim_text": "Test iddiası",
        "evidence_stance": "supports",
        "source_directness": "direct",
        "cite_source": "web_search_override",
    }
    out = build_reviewer_summary(row)
    assert "Verdict kendi gerekçesiyle tam örtüşmüyor" in out["check_point"]
    assert out["model_disagreement"] is True
    assert out["source_note"] == "Claude'un kendi aramasından (pakette değildi)"


def test_background_no_direct_evidence():
    row = {
        "final_verdict": "tartışmalı",
        "reasoning": "Genel mekanizma literatürü var.",
        "calibration_flags": "specificity_tier:background,no_direct_evidence_expected,retrieval_cited",
        "category": "mekanizma",
        "initial_risk": "low",
        "claim_text": "Test",
        "evidence_stance": "insufficient",
        "source_directness": "indirect",
        "cite_source": "retrieval_cited",
    }
    out = build_reviewer_summary(row)
    assert "doğrudan bir çalışma bulunamadı" in out["check_point"]
    assert out["source_note"] == "Sağlanan kanıt paketinden"


def test_high_risk_category():
    row = {
        "final_verdict": "yanlış",
        "reasoning": "Kanıt yok.",
        "calibration_flags": "retrieval_cited,specificity_tier:direct",
        "category": "tedavi",
        "initial_risk": "low",
        "claim_text": "Bu bitki kanseri tedavi eder.",
        "evidence_stance": "contradicts",
        "source_directness": "direct",
        "cite_source": "retrieval_cited",
    }
    out = build_reviewer_summary(row)
    assert out["risk_level"] == "yüksek"


def test_compound_claim_heuristic():
    text = (
        "Protein ve yağ birlikte tüketildiğinde glisemi daha yavaş yükselir "
        "ve yürüyüş sonrası insülin duyarlılığı artar."
    )
    assert is_compound_claim(text)
    parts = decompose_claim_for_retrieval(text)
    assert len(parts) == 2
    assert "glisemi" in parts[0]
    assert "insülin" in parts[1]


def test_decompose_hem_pair():
    text = (
        "Bu bitki hem damar duvarlarını gevşeterek tansiyonu düşürür "
        "hem de karaciğer yağlanmasını geri çevirir."
    )
    parts = decompose_claim_for_retrieval(text)
    assert len(parts) == 2
    assert is_compound_claim(text)
    assert "tansiyonu" in parts[0] or "damar" in parts[0]
    assert "karaciğer" in parts[1]


def test_decompose_comma_verb_phrases():
    text = (
        "Protein tüketildiğinde glisemi daha yavaş yükselir, "
        "yürüyüş sonrası insülin duyarlılığı belirgin artar."
    )
    parts = decompose_claim_for_retrieval(text)
    assert len(parts) == 2
    assert "glisemi" in parts[0]
    assert "insülin" in parts[1]


def test_decompose_does_not_split_food_list():
    text = "Ispanak, domates ve pancar yüksek potasyum içerir."
    parts = decompose_claim_for_retrieval(text)
    assert len(parts) == 1
    assert not is_compound_claim(text)


def test_decompose_short_ve_not_compound():
    text = "Çay ve kahve uyku kaçırır."
    parts = decompose_claim_for_retrieval(text)
    assert len(parts) == 1
    assert not is_compound_claim(text)


def test_decompose_does_not_split_mid_phrase_ve():
    """Cümle-ortası NP/AP «ve» yarım cümle üretmesin."""
    keratin = "Tiroid bezi saç hücrelerinin bölünme ve keratin üretme hızını belirler."
    assert len(decompose_claim_for_retrieval(keratin)) == 1
    visible = (
        "Ayşe Hanım vaka örneğinde, rutine başladıktan 8 hafta sonra "
        "çenesiyle boğazı arasındaki sarkık açıda gözle görülür ve "
        "ölçülebilir bir toparlanma oluştu."
    )
    assert len(decompose_claim_for_retrieval(visible)) == 1
    memory = (
        "74 yaş civarındaki kişilere pancar suyuna dayalı yüksek nitrat içeren "
        "kahvaltı verildiğinde birkaç gün sonra çekilen beyin MR'larında hafıza "
        "ve karar vermeden sorumlu bölgelere giden kan akışında nokta atışı artış görüldü."
    )
    assert len(decompose_claim_for_retrieval(memory)) == 1


def test_decompose_still_splits_clause_level_ve():
    text = (
        "Retinol, peptit ve hyaluronik asit içeren kremler sadece epidermiste etkilidir "
        "ve derinin 1 cm altındaki kas/bağ dokusuna ulaşamaz."
    )
    parts = decompose_claim_for_retrieval(text)
    assert len(parts) == 2
    assert "etkilidir" in parts[0]
    assert "ulaşamaz" in parts[1]


def test_nli_disagreement():
    row = {
        "final_verdict": "yanlış",
        "reasoning": "Kaynak desteklemiyor.",
        "calibration_flags": "retrieval_cited",
        "category": "diğer",
        "initial_risk": "low",
        "claim_text": "Test",
        "evidence_stance": "contradicts",
        "source_directness": "direct",
        "nli_label": "SUPPORTS",
    }
    out = build_reviewer_summary(row)
    assert out["model_disagreement"] is True


def test_would_auto_accept_v1_nli_only_accept():
    row = {
        "final_verdict": "doğrulanmış",
        "reasoning": "NLI ilk filtresi: SUPPORTS (güven 0.90); LLM'e escalate edilmedi.",
        "calibration_flags": "",
        "category": "mekanizma",
        "initial_risk": "low",
        "claim_text": "Test iddiası",
        "evidence_stance": "supports",
        "source_directness": "direct",
        "escalated": 0,
        "nli_label": "SUPPORTS",
        "nli_confidence": 0.9,
        "parse_failed": False,
    }
    ok, reason = would_auto_accept_v1(row)
    assert ok is True
    assert reason == ""


def test_would_auto_accept_v1_blocks_escalated():
    row = {
        "final_verdict": "doğrulanmış",
        "reasoning": "Claude path",
        "calibration_flags": "retrieval_cited,specificity_tier:supportive",
        "category": "mekanizma",
        "initial_risk": "low",
        "claim_text": "Test",
        "evidence_stance": "supports",
        "source_directness": "direct",
        "escalated": 1,
        "nli_label": "SUPPORTS",
        "nli_confidence": 0.9,
        "parse_failed": False,
    }
    ok, reason = would_auto_accept_v1(row)
    assert ok is False
    assert reason == "escalated:not_nli_only"


def test_would_auto_accept_v1_blocks_package_only_scope():
    row = {
        "final_verdict": "doğrulanmış",
        "reasoning": "Paket only",
        "calibration_flags": "package_only_forced,specificity_tier:direct",
        "category": "mekanizma",
        "initial_risk": "low",
        "claim_text": "Test",
        "escalated": 0,
        "nli_label": "SUPPORTS",
        "nli_confidence": 0.9,
        "parse_failed": False,
    }
    ok, reason = would_auto_accept_v1(row)
    assert ok is False
    assert reason == "out_of_scope:package_only_forced"


def test_would_auto_accept_v1_blocks_mismatch():
    row = {
        "final_verdict": "doğrulanmış",
        "reasoning": "Kısmi destek var.",
        "calibration_flags": "verdict_reasoning_mismatch",
        "category": "mekanizma",
        "initial_risk": "low",
        "claim_text": "Test",
        "evidence_stance": "supports",
        "source_directness": "direct",
        "escalated": 0,
        "nli_label": "SUPPORTS",
        "nli_confidence": 0.9,
        "parse_failed": False,
    }
    ok, reason = would_auto_accept_v1(row)
    assert ok is False
    assert "verdict_reasoning_mismatch" in reason


def test_shadow_gates_verdict_and_confidence():
    g = compute_shadow_human_gates(
        final_verdict="tartışmalı",
        confidence=0.62,
        calibration_flags="",
        needs_human=False,
    )
    assert g["would_require_human_verdict_gate"] == 1
    assert g["would_require_human_confidence_gate"] == 1
    assert g["would_require_human_compound_gate"] == 0
    assert g["would_auto_accept_after_all_gates"] == 0


def test_shadow_compound_gate_zero_when_needs_human_catches():
    g = compute_shadow_human_gates(
        final_verdict="tartışmalı",
        confidence=0.9,
        calibration_flags="compound_tier_mismatch",
        needs_human=True,
    )
    assert g["would_require_human_compound_gate"] == 0
    assert g["would_auto_accept_after_all_gates"] == 0


def test_shadow_compound_gate_regression_when_needs_human_misses():
    g = compute_shadow_human_gates(
        final_verdict="doğrulanmış",
        confidence=0.9,
        calibration_flags="compound_tier_mismatch",
        needs_human=False,
    )
    assert g["would_require_human_compound_gate"] == 1
    assert g["would_auto_accept_after_all_gates"] == 0


def test_shadow_would_auto_accept_after_all_gates():
    g = compute_shadow_human_gates(
        final_verdict="doğrulanmış",
        confidence=0.85,
        calibration_flags="",
        needs_human=False,
    )
    assert g["would_require_human_verdict_gate"] == 0
    assert g["would_require_human_confidence_gate"] == 0
    assert g["would_require_human_compound_gate"] == 0
    assert g["would_auto_accept_after_all_gates"] == 1
