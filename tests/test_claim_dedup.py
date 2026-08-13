import numpy as np
import pytest

from utils import claim_dedup


def _mock_embed(texts: list[str]) -> np.ndarray:
    out = []
    for i, t in enumerate(texts):
        vec = np.zeros(4)
        vec[i % 4] = 1.0
        vec[3] = len(t) * 0.001
        out.append(vec / np.linalg.norm(vec))
    return np.stack(out)


def test_dedupe_exact_match(monkeypatch):
    monkeypatch.setattr(claim_dedup, "embed_texts", _mock_embed)
    claims = [
        {"claim_text": "Muz krampı azaltır"},
        {"claim_text": "  muz   krampı azaltır  "},
        {"claim_text": "Tuz su tutar"},
    ]
    out = claim_dedup.dedupe_claims(claims, threshold=0.99)
    assert len(out) == 2


def test_get_threshold_default():
    meta = claim_dedup.threshold_metadata()
    assert "threshold" in meta
    assert isinstance(meta["threshold"], float)


def test_token_jaccard_blocks_same_topic_high_cosine(monkeypatch):
    """Yüksek cosine ama düşük kelime örtüşmesi — ayrı kalmalı."""
    import numpy as np

    def _same_embed(texts):
        v = np.array([1.0, 0.0, 0.0])
        return np.stack([v / np.linalg.norm(v)] * len(texts))

    monkeypatch.setattr(claim_dedup, "embed_texts", _same_embed)
    claims = [
        {"claim_text": "100 gram çiğ ıspanak yaklaşık 550 miligram potasyum içerir"},
        {
            "claim_text": "Çiğ ıspanak yüksek potasyum ve oksalat içeriği nedeniyle zayıflamış böbrekler için tehlikelidir"
        },
    ]
    out = claim_dedup.dedupe_claims(claims, threshold=0.5, lexical_threshold=0.35)
    assert len(out) == 2


def test_numeric_guard_blocks_gi_gl_template(monkeypatch):
    """Aynı şablon, farklı GI — cosine/jaccard yüksek olsa da birleşme yok."""

    def _same_embed(texts):
        v = np.ones(4)
        return np.stack([v / np.linalg.norm(v)] * len(texts))

    monkeypatch.setattr(claim_dedup, "embed_texts", _same_embed)
    claims = [
        {"claim_text": "Şeftalinin glisemik indeksi 42, glisemik yükü 4'tür."},
        {"claim_text": "Armutun glisemik indeksi 38, glisemik yükü 4'tür."},
    ]
    out = claim_dedup.dedupe_claims(claims, threshold=0.5, lexical_threshold=0.30)
    assert len(out) == 2
    assert claim_dedup.numeric_values_conflict(claims[0]["claim_text"], claims[1]["claim_text"])


def test_numeric_guard_allows_same_numbers(monkeypatch):
    def _same_embed(texts):
        v = np.ones(4)
        return np.stack([v / np.linalg.norm(v)] * len(texts))

    monkeypatch.setattr(claim_dedup, "embed_texts", _same_embed)
    claims = [
        {"claim_text": "Şeftalinin glisemik indeksi 42, glisemik yükü 4'tür."},
        {"claim_text": "Taze şeftalinin glisemik indeksi 42 ve glisemik yükü 4'tür."},
    ]
    assert not claim_dedup.numeric_values_conflict(claims[0]["claim_text"], claims[1]["claim_text"])
    out = claim_dedup.dedupe_claims(claims, threshold=0.5, lexical_threshold=0.30)
    assert len(out) == 1


def test_numeric_guard_mg_doses():
    a = "100 gram kabak yaklaşık 240 mg potasyum içerir"
    b = "100 gram ıspanak yaklaşık 550 mg potasyum içerir"
    assert claim_dedup.numeric_values_conflict(a, b)
    assert not claim_dedup.numeric_values_conflict(
        "100 gram ıspanak yaklaşık 550 mg potasyum içerir",
        "100 gram çiğ ıspanak yaklaşık 558 mg potasyum içerir",
    )
    assert claim_dedup.numeric_values_conflict(
        "Papayanın glisemik indeksi 58, glisemik yükü 12'dir.",
        "Üzümün glisemik indeksi 61, glisemik yükü 12'dir.",
    )
