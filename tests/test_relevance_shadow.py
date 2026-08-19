"""Shadow relevance — skor kaydı; eşik/gate yok."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from utils.evidence_retrieval import (
    RELEVANCE_BASIS_CITED,
    RELEVANCE_BASIS_MISSING,
    RELEVANCE_BASIS_PROXY,
    compute_evidence_relevance,
    resolve_relevance_evidence,
    shadow_relevance_debug_fields,
)


def test_resolve_cited_package_item_preferred():
    evidence = [
        {
            "title": "proxy top",
            "abstract": "unrelated wheelchair",
            "url": "https://pubmed.ncbi.nlm.nih.gov/111",
            "rerank_score": 0.99,
        },
        {
            "title": "cited parsley",
            "abstract": "diuretic effect",
            "url": "https://pubmed.ncbi.nlm.nih.gov/999",
            "rerank_score": 0.10,
        },
    ]
    item, basis = resolve_relevance_evidence(
        "https://pubmed.ncbi.nlm.nih.gov/999",
        evidence,
    )
    assert basis == RELEVANCE_BASIS_CITED
    assert item["title"] == "cited parsley"


def test_resolve_proxy_when_source_url_not_in_package():
    evidence = [
        {
            "title": "A Brain-Controlled Wheelchair",
            "abstract": "feasibility",
            "url": "https://pubmed.ncbi.nlm.nih.gov/111",
            "rerank_score": 0.80,
        },
        {
            "title": "other",
            "abstract": "x",
            "url": "https://pubmed.ncbi.nlm.nih.gov/222",
            "rerank_score": 0.20,
        },
    ]
    item, basis = resolve_relevance_evidence(
        "https://example.com/web-search-only",
        evidence,
    )
    assert basis == RELEVANCE_BASIS_PROXY
    assert item["title"] == "A Brain-Controlled Wheelchair"


def test_resolve_missing_when_no_package():
    item, basis = resolve_relevance_evidence("https://pubmed.ncbi.nlm.nih.gov/1", [])
    assert item is None
    assert basis == RELEVANCE_BASIS_MISSING


def test_compute_evidence_relevance_empty_is_none():
    assert compute_evidence_relevance("", "some evidence") is None
    assert compute_evidence_relevance("claim", "") is None
    assert compute_evidence_relevance("  ", "  ") is None


def test_shadow_fields_do_not_invent_score_without_text(monkeypatch):
    import utils.evidence_retrieval as er

    monkeypatch.setattr(er, "compute_evidence_relevance", lambda *_a, **_k: 0.99)
    fields = shadow_relevance_debug_fields("claim", None, [])
    assert fields["relevance_score"] is None
    assert fields["relevance_basis"] == RELEVANCE_BASIS_MISSING
    assert fields["relevance_evidence_title"] is None


def test_shadow_fields_pass_selected_text(monkeypatch):
    import utils.evidence_retrieval as er

    seen = {}

    def fake_score(claim_text, evidence_text):
        seen["claim"] = claim_text
        seen["text"] = evidence_text
        return 0.267

    monkeypatch.setattr(er, "compute_evidence_relevance", fake_score)
    evidence = [
        {
            "title": "Wheelchair",
            "abstract": "feasibility study",
            "url": "https://pubmed.ncbi.nlm.nih.gov/111",
        }
    ]
    fields = shadow_relevance_debug_fields(
        "tamamen iyileştirir",
        "https://example.com/other",
        evidence,
    )
    assert fields["relevance_score"] == 0.267
    assert fields["relevance_basis"] == RELEVANCE_BASIS_PROXY
    assert fields["relevance_evidence_title"] == "Wheelchair"
    assert seen["claim"] == "tamamen iyileştirir"
    assert "Wheelchair" in seen["text"]
    assert "feasibility study" in seen["text"]
