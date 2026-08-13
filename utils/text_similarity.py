"""
Paylaşılan metin benzerliği kümeleme fonksiyonu.

utils/bot_detection.py (yorumlar arası kopya tespiti) ve pipeline/06_claim_index.py
(kanallar arası aynı iddianın tekrarını tespit) AYNI mantığı kullanıyor — bu yüzden
tek yerden yönetiliyor.
"""
from collections import defaultdict
from difflib import SequenceMatcher

EMBEDDING_CLUSTER_THRESHOLD = 0.80


def normalize(text: str) -> str:
    import re
    return re.sub(r"\s+", " ", text.strip().lower())


def find_similar_clusters(items: list[dict], id_key: str, text_key: str,
                           threshold: float = 0.85, min_length: int = 8) -> dict:
    """
    items içindeki metinleri normalize edip birbirine çok benzeyenleri kümeler.
    Dönüş: {item_id: cluster_size} — cluster_size 1 ise benzersiz demektir.

    NOT: Naif O(n^2) karşılaştırma kullanır; binlerce öğede yavaşlar — üretimde
    MinHash/SimHash gibi yaklaşık yöntemlere geçin.
    """
    clusters = get_cluster_members(items, id_key, text_key, threshold, min_length)
    cluster_size = defaultdict(lambda: 1)
    for members in clusters:
        for it in members:
            cluster_size[it[id_key]] = len(members)
    return cluster_size


def get_cluster_members(items: list[dict], id_key: str, text_key: str,
                         threshold: float = 0.85, min_length: int = 8) -> list[list[dict]]:
    """
    Boyutu>1 olan kümelerin TAM üye listesini döner.

    ÖNEMLİ TASARIM NOTU (single-linkage): yeni bir öğe, kümenin SADECE ilk eklenen
    üyesiyle (temsilcisiyle) değil, kümedeki HERHANGİ BİR üyeyle eşiği aşarsa o
    kümeye eklenir. İlk versiyon sadece temsilciyle karşılaştırıyordu; bu, sıraya
    bağlı kayıplara yol açıyordu — A ve C metinleri anlamca aynı iddiayı taşısa
    bile, aradaki B metnine göre kümeye önce eklenen A'yla C'nin benzerliği eşiğin
    altında kalabiliyor, C yanlışlıkla ayrı bir kümeye düşüyordu (06_claim_index.py
    ile test edilirken tam olarak bu senaryo — iki farklı kanaldaki aynı yanlış
    iddia — gözlemlendi). Single-linkage bunu düzeltir, ama zincirleme yoluyla
    ilgisiz metinleri de aynı kümeye sürükleyebilir (A~B, B~C ama A≁C olsa bile
    hepsi tek kümede toplanır) — bu bilinen bir ödünleşimdir.
    """
    texts = [(it, normalize(it[text_key])) for it in items if it.get(text_key)]
    clusters = []  # [[(item, norm_text), ...], ...]

    for it, text in texts:
        if len(text) < min_length:
            continue
        target_cluster = None
        for cluster in clusters:
            if any(SequenceMatcher(None, text, member_text).ratio() >= threshold
                   for _, member_text in cluster):
                target_cluster = cluster
                break
        if target_cluster is not None:
            target_cluster.append((it, text))
        else:
            clusters.append([(it, text)])

    return [[it for it, _ in cluster] for cluster in clusters if len(cluster) > 1]


def get_cluster_members_embedding(
    items: list[dict],
    id_key: str,
    text_key: str,
    threshold: float = EMBEDDING_CLUSTER_THRESHOLD,
    min_length: int = 8,
) -> list[list[dict]]:
    """
    Embedding cosine + single-linkage kümeleme (anlamca benzer iddialar).
    Narrative clustering için yalnızca cosine kullanılır — lexical eşik
    dedup için uygundur ama paraphrase iddiaları kaçırır.
    """
    from utils.claim_dedup import embed_texts

    valid = [(it, normalize(it[text_key])) for it in items if it.get(text_key)]
    valid = [(it, t) for it, t in valid if len(t) >= min_length]
    if len(valid) < 2:
        return []

    try:
        import numpy as np
        texts = [t for _, t in valid]
        embs = embed_texts(texts)
    except Exception:
        return []

    clusters: list[list[int]] = []

    for idx in range(len(valid)):
        emb = embs[idx]
        target = None
        for cluster in clusters:
            if any(float(np.dot(emb, embs[j])) >= threshold for j in cluster):
                target = cluster
                break
        if target is not None:
            target.append(idx)
        else:
            clusters.append([idx])

    return [[valid[i][0] for i in c] for c in clusters if len(c) > 1]
