import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from utils.text_similarity import find_similar_clusters, get_cluster_members, normalize


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
