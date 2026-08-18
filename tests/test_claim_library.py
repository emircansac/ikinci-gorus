import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from utils.claim_library import is_seed_eligible, classify_library_match, PARTIAL_REASONING_RE
from utils.evidence_retrieval import retrieve_guideline_snippets, retrieve_hybrid_evidence


def test_653_blocklisted():
    ok, reason = is_seed_eligible(
        claim_id=653, final_verdict="doğrulanmış", reasoning="GFR temel ölçüt",
        human_reviewed=1,
    )
    assert not ok
    assert reason == "blocklist"


def test_partial_reasoning_rejected():
    ok, reason = is_seed_eligible(
        claim_id=681,
        final_verdict="tartışmalı",
        reasoning="USDA 558 mg doğru ancak iddianın böbrek atamaz kısmı kanıtlanmıyor",
        human_reviewed=1,
    )
    assert not ok
    assert reason.startswith("verdict=") or reason == "partial_reasoning"


def test_clean_verdict_eligible():
    ok, reason = is_seed_eligible(
        claim_id=694,
        final_verdict="doğrulanmış",
        reasoning="NKF diyabet böbrek yetmezliğinin önde gelen nedenidir.",
        evidence_stance="supports",
        human_reviewed=1,
    )
    assert ok
    assert reason == ""


def test_696_kismi_not_false_positive():
    """'potasyum kısmı' (its part) kısmi kanıt sayılmamalı — ı/i katlaması."""
    reasoning = (
        "İddianın potasyum kısmı doğrudan kılavuz düzeyinde kaynakla, "
        "oksalat kısmı ise daha genel/ikincil bir kaynakla desteklendiği için "
        "genel doğrulama yapılabilir."
    )
    assert PARTIAL_REASONING_RE.search(reasoning) is None
    ok, reason = is_seed_eligible(
        claim_id=696, final_verdict="doğrulanmış", reasoning=reasoning,
        evidence_stance="supports", human_reviewed=1,
    )
    assert ok, reason
    assert PARTIAL_REASONING_RE.search("Kısmi kanıt; bir kısmı kanıtlanmadı")


def test_690_compound_false_still_partial():
    reasoning = (
        "Ancak iddianın 'böbrek yetmezliğinde bu mekanizma işlemez' kısmı yanlış: "
        "CKD hastalarında pilot çalışmalar tam tersini gösteriyor. "
        "Bu yüzden iddianın genellemesi yanlış/aşırı basitleştirilmiş."
    )
    assert PARTIAL_REASONING_RE.search(reasoning)
    ok, _ = is_seed_eligible(
        claim_id=690, final_verdict="yanlış", reasoning=reasoning,
        evidence_stance="contradicts", human_reviewed=1,
    )
    assert not ok


def test_classify_library_match_bands():
    assert classify_library_match(0.70, 0.50, auto_threshold=0.8055, lexical_threshold=0.35) is None
    assert classify_library_match(0.78, 0.50, auto_threshold=0.8055, lexical_threshold=0.35) == "flag_review"
    assert classify_library_match(0.85, 0.20, auto_threshold=0.8055, lexical_threshold=0.35) == "flag_review"
    assert classify_library_match(0.85, 0.50, auto_threshold=0.8055, lexical_threshold=0.35) == "auto"
    assert classify_library_match(0.90, 0.90, numeric_conflict=True, auto_threshold=0.8055, lexical_threshold=0.35) == "flag_review"


def test_663_no_guideline_false_positive(monkeypatch):
    monkeypatch.setattr(
        "utils.nli.nli_check",
        lambda *a, **k: {"nli_label": "NOT_ENOUGH_INFO", "nli_confidence": 0.4, "raw": {}},
    )
    text = "Kabağın %92'den fazlası su olduğu için böbreği yormadan hidrasyon sağlar."
    query = "zucchini water content hydration kidney"
    snippets = retrieve_guideline_snippets(query, "mekanizma", claim_text=text)
    assert snippets == []
    ev, path, _meta = retrieve_hybrid_evidence(text, query, "mekanizma", include_serper=False)
    assert path != "guideline" or not ev
