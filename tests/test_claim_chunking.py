import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from utils.claude_client import (
    _split_transcript_chunks,
    _repair_truncated_claims_json,
    _is_recap_chunk,
)
from utils import claim_dedup
from tests.test_claim_dedup import _mock_embed


def test_split_on_timestamp_boundaries():
    parts = [f"[{i*10}s] cümle {i} " + ("x" * 200) for i in range(80)]
    transcript = "".join(parts)
    chunks = _split_transcript_chunks(transcript, max_chunk=5000)
    assert len(chunks) > 1
    joined = "".join(chunks)
    assert len(joined) >= len(transcript) * 0.9  # overlap nedeniyle biraz daha uzun olabilir


def test_dedupe_claims(monkeypatch):
    monkeypatch.setattr(claim_dedup, "embed_texts", _mock_embed)
    claims = [
        {"claim_text": "Muz krampı azaltır"},
        {"claim_text": "  muz   krampı azaltır  "},
        {"claim_text": "Tuz su tutar"},
    ]
    out = claim_dedup.dedupe_claims(claims, threshold=0.99)
    assert len(out) == 2


def test_dedupe_claims_fuzzy_similar(monkeypatch):
    def _mock_embed(texts):
        import numpy as np
        vecs = []
        for t in texts:
            base = np.array([1.0, 0.1, 0.0]) if "GFR" in t or "Glomer" in t else np.array([0.0, 1.0, 0.0])
            vecs.append(base / np.linalg.norm(base))
        # Make GFR pair nearly identical
        if len(texts) == 3:
            vecs[0] = np.array([1.0, 0.05, 0.0])
            vecs[1] = np.array([0.99, 0.06, 0.0])
            for i in (0, 1):
                vecs[i] = vecs[i] / np.linalg.norm(vecs[i])
        return np.stack(vecs)

    monkeypatch.setattr(claim_dedup, "embed_texts", _mock_embed)
    claims = [
        {"claim_text": "GFR (glomerüler filtrasyon hızı) değeri böbrek hastalığının evresini ve gerekli diyet kısıtlamalarını belirleyen temel ölçüttür"},
        {"claim_text": "Glomerüler filtrasyon hızı (GFR) değeri, böbrek hastalığının evresini ve gereken diyet kısıtlamalarını belirleyen tek rakamdır."},
        {"claim_text": "Kabak düşük potasyumlu bir sebzedir"},
    ]
    out = claim_dedup.dedupe_claims(claims, threshold=0.85, lexical_threshold=0.35)
    assert len(out) == 2


def test_recap_chunk_detected_on_last_chunk():
    recap = "[1650s] Konuyu toparlayacak olursak böbrekleriniz zorlanıyor."
    assert _is_recap_chunk(recap, is_last=True)
    assert not _is_recap_chunk(recap, is_last=False)
    assert not _is_recap_chunk("[100s] Kabak böbrek dostu bir sebzedir.", is_last=True)


def test_is_case_narrative():
    from utils.claim_dedup import is_case_narrative
    marcos = (
        "67 yaşındaki bir hasta, evre 3 böbrek yetmezliği tanısı aldıktan 2 yıl sonra, "
        "beslenme eğitimi sayesinde böbrek fonksiyonları stabil kalmış ve hastalığı evre 4'e ilerlememiştir."
    )
    assert is_case_narrative(marcos)
    assert not is_case_narrative("Kabak düşük potasyumlu bir sebzedir")


def test_dedupe_pipeline_recap_keeps_case(monkeypatch):
    import numpy as np

    def _mock_embed(texts):
        vecs = []
        for t in texts:
            if "67 yaşındaki" in t or "marcos" in t.lower():
                base = np.array([0.0, 0.0, 1.0])
            elif "potasyum" in t.lower() and "%70" in t:
                base = np.array([1.0, 0.0, 0.0])
            else:
                base = np.array([0.0, 1.0, 0.0])
            vecs.append(base / np.linalg.norm(base))
        return np.stack(vecs)

    monkeypatch.setattr(claim_dedup, "embed_texts", _mock_embed)
    monkeypatch.setattr(claim_dedup, "get_threshold", lambda: 0.80)
    monkeypatch.setattr(claim_dedup, "get_lexical_threshold", lambda: 0.30)

    prior = [{"claim_text": "Sebzeleri kaynatmak potasyum oranını %70'e kadar azaltır"}]
    recap_claims = [
        {"claim_text": "Doğru pişirme yöntemleri potasyum oranını %70'e kadar düşürür"},
        {"claim_text": "67 yaşındaki bir hasta evre 3 böbrek yetmezliğinde 2 yıl stabil kaldı"},
    ]
    chunk_lists = [
        {"chunk_index": 1, "is_recap": False, "claims": prior},
        {"chunk_index": 2, "is_recap": True, "claims": recap_claims},
    ]
    out = claim_dedup.dedupe_pipeline(chunk_lists, window=12)
    texts = [c["claim_text"] for c in out]
    assert any("67 yaşındaki" in t for t in texts)
    assert len(out) == 2  # potasyum tekrarı elendi, vaka kaldı


def test_repair_truncated_json():
    broken = '''```json
{"claims": [
{"timestamp_sec": 17, "claim_text": "Test iddia bir", "category": "mekanizma", "initial_risk": "medium", "search_query_en": "test one"},
{"timestamp_sec": 42, "claim_text": "Test iddia iki", "category": "tedavi", "initial_risk": "high", "search_query_en": "test two"},
{"timestamp_sec": 99, "claim_text": "Yarım kalm'''
    parsed = _repair_truncated_claims_json(broken)
    assert parsed is not None
    assert len(parsed["claims"]) == 2
