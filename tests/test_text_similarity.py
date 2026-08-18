import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from utils.text_similarity import find_similar_clusters, get_cluster_members, normalize


def _embedding_model_available() -> bool:
    try:
        from sentence_transformers import SentenceTransformer
        from utils.claim_dedup import MODEL_NAME
        SentenceTransformer(MODEL_NAME, local_files_only=True)
        return True
    except Exception:
        return False


def test_normalize_collapses_whitespace():
    assert normalize("  Perine   Noktası  ") == "perine noktası"


def test_similar_texts_clustered():
    items = [
        {"id": "a", "text": "Perine noktası sinir baskısı sertleşme sorununa yol açar"},
        {"id": "b", "text": "Perine noktası sinir baskısı sertleşme sorununa yol açar"},
    ]
    sizes = find_similar_clusters(items, id_key="id", text_key="text", threshold=0.85)
    assert sizes["a"] == 2
    assert sizes["b"] == 2


def test_single_linkage_chains_similar_items():
    items = [
        {"id": "a", "text": "perine noktası sinir baskısı sertleşme sorunu"},
        {"id": "b", "text": "perine noktası sinir baskısı sertleşme sorunu kaynağı"},
        {"id": "c", "text": "perine noktası sinir baskısı sertleşme sorunu kaynağıdır"},
    ]
    clusters = get_cluster_members(items, id_key="id", text_key="text", threshold=0.85)
    assert len(clusters) == 1
    assert len(clusters[0]) == 3


def test_different_texts_stay_separate():
    items = [
        {"id": "a", "text": "mastürbasyon prostat sağlığına faydalıdır"},
        {"id": "b", "text": "perine noktası sinir baskısı sertleşme sorununa yol açar"},
    ]
    sizes = find_similar_clusters(items, id_key="id", text_key="text", threshold=0.85)
    assert sizes.get("a", 1) == 1
    assert sizes.get("b", 1) == 1


@pytest.mark.skipif(not _embedding_model_available(), reason="embedding model/network unavailable")
def test_embedding_clusters_cross_channel_perine():
    """Demo seed'deki çapraz-kanal perine iddiaları embedding ile kümelenmeli."""
    from utils.text_similarity import get_cluster_members_embedding

    items = [
        {"claim_id": 1, "channel_id": "B", "claim_text":
         "Perine bölgesindeki pudendal sinir sıkışması çoğu 60+ erkekteki sertleşme sorununun kaynağıdır"},
        {"claim_id": 2, "channel_id": "C", "claim_text":
         "Perine noktasındaki sinir baskısı 60 yaş üstü erkeklerin çoğunda sertleşme sorununa yol açan asıl nedendir"},
    ]
    clusters, status = get_cluster_members_embedding(
        items, id_key="claim_id", text_key="claim_text", threshold=0.75,
    )
    assert status == "ok"
    assert len(clusters) == 1
    assert len(clusters[0]) == 2


def test_embedding_clusters_block_numeric_template(monkeypatch):
    """Şeftali/armut GI-GL — yüksek cosine olsa bile ayrı kümeler."""
    import numpy as np

    def _same_embed(texts):
        v = np.ones(4)
        return np.stack([v / np.linalg.norm(v)] * len(texts))

    monkeypatch.setattr("utils.claim_dedup.embed_texts", _same_embed)
    from utils.text_similarity import get_cluster_members_embedding

    items = [
        {"claim_id": 1, "channel_id": "A", "claim_text": "Şeftalinin glisemik indeksi 42, glisemik yükü 4'tür."},
        {"claim_id": 2, "channel_id": "B", "claim_text": "Armutun glisemik indeksi 38, glisemik yükü 4'tür."},
    ]
    clusters, status = get_cluster_members_embedding(
        items, id_key="claim_id", text_key="claim_text", threshold=0.5,
    )
    assert status == "ok"
    assert clusters == []


def test_embedding_clusters_failed_status(monkeypatch):
    def _boom(texts):
        raise RuntimeError("model load failed")

    monkeypatch.setattr("utils.claim_dedup.embed_texts", _boom)
    from utils.text_similarity import get_cluster_members_embedding

    items = [
        {"claim_id": 1, "channel_id": "A", "claim_text": "Perine bölgesindeki pudendal sinir sıkışması"},
        {"claim_id": 2, "channel_id": "B", "claim_text": "Perine noktasındaki sinir baskısı sertleşme"},
    ]
    clusters, status = get_cluster_members_embedding(
        items, id_key="claim_id", text_key="claim_text", threshold=0.75,
    )
    assert clusters == []
    assert status.startswith("failed:")
    assert "model load failed" in status
