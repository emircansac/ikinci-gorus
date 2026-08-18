import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from utils.db import get_conn
from utils.evidence_retrieval import assess_evidence_sufficiency
from utils.evidence_topic_cache import (
    lookup_topic_cache,
    pilot_entities_in_text,
    seed_cache_from_evidence,
    store_topic_cache,
    topic_key_for_claim,
)


def test_topic_key_mekanizma_pilot_entities():
    key = topic_key_for_claim(
        "Ispanak ve domates yüksek potasyum içerir.",
        "mekanizma",
        search_query_en="spinach tomato potassium kidney",
    )
    assert key is not None
    assert "ıspanak" in key.split(",")
    assert "domates" in key.split(",")
    assert "potasyum" in key.split(",")


def test_topic_key_non_mekanizma_returns_none():
    assert topic_key_for_claim("Potasyum yüksek", "önleme") is None


def test_topic_key_no_pilot_entity_returns_none():
    assert topic_key_for_claim("Muz lif içerir", "mekanizma") is None


def test_cache_lookup_overlap_not_exact_only():
    conn = get_conn()
    try:
        conn.execute("DELETE FROM evidence_topic_cache")
        conn.commit()
        store_topic_cache(
            conn,
            "pancar",
            [{
                "url": "https://example.org/beet-study",
                "title": "Beet nitrate study",
                "abstract": "Pancar nitrat içerir.",
                "source_tier": "primary_study",
                "retrieval_tier": "native",
                "evidence_source": "live",
            }],
            origin_claim_id=362,
        )
        hits = lookup_topic_cache(conn, "pancar,potasyum")
        assert len(hits) == 1
        assert hits[0]["evidence_source"] == "cache"
        assert hits[0]["url"] == "https://example.org/beet-study"
    finally:
        conn.execute("DELETE FROM evidence_topic_cache")
        conn.commit()
        conn.close()


def test_cache_hit_goes_through_sufficiency_not_auto_win():
    """Cache adayı pakete girer ama assess_evidence_sufficiency yine çalışır."""
    conn = get_conn()
    try:
        conn.execute("DELETE FROM evidence_topic_cache")
        conn.commit()
        seed_cache_from_evidence(
            conn,
            topic_key="potasyum",
            evidence_items=[{
                "url": "https://example.org/k",
                "title": "Unrelated title",
                "abstract": "Nothing about the claim topic here.",
                "source_tier": "encyclopedia",
                "retrieval_tier": "native",
            }],
            origin_claim_id=1,
        )
        cache_only, path, meta = __import__(
            "utils.evidence_retrieval", fromlist=["retrieve_hybrid_evidence"]
        ).retrieve_hybrid_evidence(
            "GFR düşüklüğünde potasyum birikir.",
            "glomerular filtration potassium",
            "mekanizma",
            skip_live_retrieval=True,
            conn=conn,
        )
        assert meta["cache_candidates"] >= 1
        assert path.startswith("topic_cache")
        suff = assess_evidence_sufficiency(
            cache_only,
            "GFR düşüklüğünde potasyum birikir.",
            "glomerular filtration potassium",
        )
        assert suff.relevance_ok is False or suff.quality_ok is False or not suff.sufficient
    finally:
        conn.execute("DELETE FROM evidence_topic_cache")
        conn.commit()
        conn.close()


def test_pilot_entities_gfr_from_claim():
    assert "gfr" in pilot_entities_in_text("glomerular filtrasyon hızı (GFR) düşer")
