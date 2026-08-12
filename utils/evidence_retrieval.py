"""
Kanıt getirme (evidence retrieval) — Aşama 3'ün girdisi.

CER'den (PRAISELab-PicusLab, SIGIR 2025 — biyomedikal fact-checking için
hakemli, HealthFC/BioASQ-7/SciFact'te state-of-the-art) esinlenilen iki
iyileştirme:

  1. SADECE BAŞLIK DEĞİL, GERÇEK ÖZET (abstract). Önceki versiyon PubMed
     ESummary ile sadece makale başlığını çekiyordu. Başlık, bir iddiayı
     doğrulamak/çürütmek için neredeyse hiç bilgi taşımaz — "Erectile
     dysfunction and pelvic floor: a review" başlığı iddiayı destekliyor mu
     çürütüyor mu söylemez, özetin içeriği söyler. Bu yüzden EFetch ile tam
     özet metni çekiliyor.

  2. SPARSE + DENSE. PubMed'in kendi arama motoru (ESearch) "sparse" bir
     kelime eşleştirmesi yapar — en alakalı sonucu her zaman ilk sıraya
     koymaz. CER, bunu bir dense (embedding tabanlı) yeniden sıralama ile
     tamamlıyor. Burada da aynısını yapıyoruz: PubMed'den geniş bir aday
     havuzu (10) çekilir, sonra yerel bir çok dilli embedding modeliyle
     iddiaya anlamca en yakın 3 tanesi seçilir.

DİL NOTU: Sorgu artık ham Türkçe iddia metni değil, iddia çıkarma aşamasında
(utils/claude_client.py, CLAIM_EXTRACTION_SYSTEM) Claude'un ürettiği kısa
İngilizce arama sorgusu (claims.search_query_en) ile yapılıyor. Ayrı bir
çeviri modeli/adımı kurmaya gerek kalmadı — zaten çalışan bir LLM çağrısına
bindirildi. search_query_en yoksa (eski kayıtlar, ya da LLM üretmediyse)
claim_text'e geri düşülür ama bu düşük kaliteli sonuç verir, log'a not düşülür.

pip install sentence-transformers  (opsiyonel — kurulu değilse dense rerank
atlanır, sparse sıralamayla devam edilir, sistem çökmez)
"""
import requests
import xml.etree.ElementTree as ET
from functools import lru_cache

PUBMED_ESEARCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
PUBMED_EFETCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"

CANDIDATE_POOL_SIZE = 10   # sparse retrieval ile çekilecek aday sayısı (rerank için havuz)
FINAL_EVIDENCE_COUNT = 3   # dense rerank sonrası gerçekten kullanılacak sayı
EMBEDDING_MODEL_NAME = "paraphrase-multilingual-MiniLM-L12-v2"  # Türkçe dahil ~50 dil


def _pubmed_search_ids(query_en: str, retmax: int) -> list[str]:
    """Sparse retrieval: PubMed'in kendi arama motoruyla aday havuzu."""
    params = {"db": "pubmed", "term": query_en, "retmax": retmax, "retmode": "json",
              "sort": "relevance"}
    r = requests.get(PUBMED_ESEARCH, params=params, timeout=15)
    r.raise_for_status()
    return r.json().get("esearchresult", {}).get("idlist", [])


def _pubmed_fetch_abstracts(pmids: list[str]) -> dict:
    """
    EFetch ile GERÇEK özet metinlerini çeker. ESummary (önceki versiyon) sadece
    başlık döner — bu yeterli kanıt sağlamaz, bu yüzden EFetch'e geçildi.
    Dönüş: {pmid: {title, abstract, pubdate, url}}
    """
    if not pmids:
        return {}
    params = {"db": "pubmed", "id": ",".join(pmids), "retmode": "xml", "rettype": "abstract"}
    r = requests.get(PUBMED_EFETCH, params=params, timeout=20)
    r.raise_for_status()
    out = {}
    try:
        root = ET.fromstring(r.text)
        for article in root.findall(".//PubmedArticle"):
            pmid_el = article.find(".//PMID")
            pmid = pmid_el.text if pmid_el is not None else None
            if not pmid:
                continue
            title_el = article.find(".//ArticleTitle")
            title = "".join(title_el.itertext()).strip() if title_el is not None else ""
            # Bir makalenin özeti birden fazla <AbstractText> parçasından oluşabilir
            # (Background/Methods/Results/Conclusions gibi etiketli bölümler) — hepsini birleştir.
            abstract_parts = article.findall(".//AbstractText")
            abstract = " ".join("".join(a.itertext()).strip() for a in abstract_parts).strip()
            pubdate_el = article.find(".//PubDate/Year")
            pubdate = pubdate_el.text if pubdate_el is not None else ""
            out[pmid] = {"title": title, "abstract": abstract, "pubdate": pubdate,
                         "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"}
    except ET.ParseError as e:
        print(f"[evidence] EFetch XML parse hatası: {e}")
    return out


@lru_cache(maxsize=1)
def _get_embedder():
    """
    Dense reranking için hafif, çok dilli bir embedding modeli. Kurulu değilse
    (sentence-transformers yoksa) None döner — sistem sparse sıralamayla
    devam eder, ÇÖKMEZ. Türkçe iddiayi doğrudan (çevirisiz) kullanabildiği
    için bu adımda ek çeviri gerekmiyor.
    """
    try:
        from sentence_transformers import SentenceTransformer
        return SentenceTransformer(EMBEDDING_MODEL_NAME)
    except ImportError:
        print("[evidence] sentence-transformers kurulu değil, dense rerank atlanıyor "
              "(pip install sentence-transformers ile etkinleştirebilirsiniz) — "
              "sparse (PubMed) sıralamasıyla devam ediliyor.")
        return None
    except Exception as e:
        print(f"[evidence] embedding modeli yüklenemedi ({e}), dense rerank atlanıyor")
        return None


def _dense_rerank(claim_text: str, candidates: list[dict], top_k: int) -> list[dict]:
    """
    Adayları claim_text'e (orijinal dildeki iddia — model çok dilli olduğu için
    çeviriye gerek yok) anlamca en yakın olacak şekilde yeniden sıralar.
    Embedder yoksa veya aday listesi boşsa, PubMed'in kendi sıralamasından
    ilk top_k'yı döner (zarif düşüş — sistem hiçbir durumda çökmez).
    """
    embedder = _get_embedder()
    if embedder is None or not candidates:
        return candidates[:top_k]

    texts = [f"{c['title']} {c['abstract']}".strip() for c in candidates]
    try:
        import numpy as np
        claim_vec = embedder.encode([claim_text])[0]
        cand_vecs = embedder.encode(texts)
        norms = np.linalg.norm(cand_vecs, axis=1) * np.linalg.norm(claim_vec) + 1e-9
        sims = (cand_vecs @ claim_vec) / norms
        ranked = sorted(zip(candidates, sims), key=lambda pair: -pair[1])
        return [c for c, _ in ranked[:top_k]]
    except Exception as e:
        print(f"[evidence] dense rerank hatası ({e}), sparse sıralamayla devam ediliyor")
        return candidates[:top_k]


def retrieve_pubmed_evidence(claim_text: str, search_query_en: str | None = None) -> list[dict]:
    """
    Sparse (PubMed ESearch) + dense (embedding rerank) hibrit kanıt getirme.

    claim_text: orijinal iddia metni (Türkçe olabilir) — dense rerank bunu
        kullanır, çok dilli model olduğu için çeviriye gerek yok.
    search_query_en: iddia çıkarma aşamasında Claude'un ürettiği kısa İngilizce
        arama sorgusu. PubMed büyük ölçüde İngilizce indeksli olduğu için bu
        olmadan Türkçe metin gönderilirse sonuç kalitesi çok düşer.

    Dönüş: [{title, abstract, pubdate, url}, ...] — en fazla FINAL_EVIDENCE_COUNT adet,
    claim_text'e anlamca en yakın olacak şekilde sıralı.
    """
    if not search_query_en:
        print("[evidence] uyarı: search_query_en yok, ham iddia metniyle aranıyor "
              "(düşük kaliteli sonuç bekleyin — bu genellikle eski/önceki sürüm "
              "verisidir, 02_extract_claims.py'yi tekrar çalıştırmayı düşünün)")
    query = search_query_en or claim_text

    try:
        pmids = _pubmed_search_ids(query, retmax=CANDIDATE_POOL_SIZE)
    except requests.RequestException as e:
        print(f"[evidence] PubMed ESearch hatası: {e}")
        return []
    if not pmids:
        return []

    try:
        abstracts = _pubmed_fetch_abstracts(pmids)
    except requests.RequestException as e:
        print(f"[evidence] PubMed EFetch hatası: {e}")
        return []

    # Özeti olan adaylar önceliklidir; hiçbirinde özet yoksa (nadir, çok eski/kısa
    # yayınlarda olur) en azından başlıkla devam edilir — evidence_text boş kalmasın.
    with_abstract = [abstracts[p] for p in pmids if p in abstracts and abstracts[p]["abstract"]]
    candidates = with_abstract or [abstracts[p] for p in pmids if p in abstracts]

    return _dense_rerank(claim_text, candidates, FINAL_EVIDENCE_COUNT)
