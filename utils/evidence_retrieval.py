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

Hibrit katman (v2): MeSH genişletme, kılavuz snippet fallback, besin DB yönlendirmesi.
Hibrit katman (v3): PubMed PublicationType → source_tier; Europe PMC paralel;
MedlinePlus tüketici sağlığı (üçüncü kaynak).
Hibrit katman (v4): kademeli getirme — native (ücretsiz) → Serper (ucuz) →
Claude web_search (pahalı, escalate_factcheck güvenlik ağı, burada çağrılmaz).
Yeterlilik tek kapı: assess_evidence_sufficiency (alaka × kademe, AND).
"""
from __future__ import annotations

import html as html_lib
import os
import re
import requests
import xml.etree.ElementTree as ET
from functools import lru_cache
from pathlib import Path
from typing import NamedTuple

from utils.factcheck_calibrate import TIER_CONF_CAP, infer_source_tier

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent.parent / ".env")
except ImportError:
    pass

PUBMED_ESEARCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
PUBMED_EFETCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
EUROPEPMC_SEARCH = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
MEDLINEPLUS_SEARCH = "https://wsearch.nlm.nih.gov/ws/query"
SERPER_SEARCH = "https://google.serper.dev/search"

# quality_ok: mevcut TIER_CONF_CAP, primary_study tavanı ve üzeri + native besin yolu.
# Yeni kademe icat edilmez.
SUFFICIENT_QUALITY_TIERS = frozenset(
    {tier for tier, cap in TIER_CONF_CAP.items() if cap >= TIER_CONF_CAP["primary_study"]}
    | {"usda_cache_static"}
)

CANDIDATE_POOL_SIZE = 10
FINAL_EVIDENCE_COUNT = 3
ESCALATE_PACKAGE_SIZE = 5
EMBEDDING_MODEL_NAME = "paraphrase-multilingual-MiniLM-L12-v2"
# Kapı spesifikliği: SUPPORTS/REFUTES + bu eşik. Gevşetilmez (yanlış pozitif kaçışı yok).
SPECIFICITY_NLI_MIN_CONF = 0.75
# supportive kademe + epistemik "doğrudan kanıt" tabanı. 0.75 kilidi değişmez.
SPECIFICITY_SUPPORTIVE_MIN_CONF = 0.5
EPISTEMIC_NO_DIRECT = "no_direct_evidence_expected"

# search_query_en'deki arka plan tıbbi terimler — ana varlık değil.
# "blueberry insulin ... diabetes" → anahtar "blueberry", "diabetes" değil.
KEY_TERM_STOPWORDS = frozenset({
    "the", "and", "for", "with", "from", "that", "this", "into", "after",
    "diabetes", "diabetic", "insulin", "kidney", "renal", "ckd", "disease",
    "chronic", "blood", "pressure", "patient", "patients", "diet", "dietary",
    "health", "clinical", "study", "trial", "review", "effect", "effects",
    "type", "rate", "normal", "content", "method", "methods", "reduce",
    "reduction", "sensitivity", "microvascular", "vascular", "complication",
    "complications", "hydration", "water", "filtration", "glomerular",
    "stage", "levels", "high", "low", "human", "risk", "among", "based",
})

# Ana varlık eşanlamlıları: makale "Vaccinium" deyip "blueberry" demeyebilir.
# El sözlüğü ölçeklenmez; asıl güvenlik ağı claim_text token'ları + kök öneki.
ENTITY_SYNONYMS = {
    "blueberry": ("blueberry", "blueberries", "vaccinium", "yaban mersini"),
    "blueberries": ("blueberry", "blueberries", "vaccinium", "yaban mersini"),
    "kiwi": ("kiwi", "kiwifruit", "actinidin", "kivi"),
    "kivi": ("kiwi", "kiwifruit", "actinidin", "kivi"),
    "zucchini": ("zucchini", "courgette", "squash", "kabak"),
    "spinach": ("spinach", "ıspanak", "ispanak"),
    "cucumber": ("cucumber", "salatalık", "salatalik"),
    "cabbage": ("cabbage", "lahana"),
    "beetroot": ("beetroot", "beet", "pancar"),
    "beet": ("beetroot", "beet", "pancar"),
    "pancar": ("beetroot", "beet", "pancar"),
    "ıspanak": ("spinach", "ıspanak", "ispanak"),
    "ispanak": ("spinach", "ıspanak", "ispanak"),
    "spinach": ("spinach", "ıspanak", "ispanak"),
    "kabak": ("zucchini", "courgette", "squash", "kabak"),
    "zucchini": ("zucchini", "courgette", "squash", "kabak"),
    "domates": ("domates", "tomato", "tomatoes"),
    "tomato": ("domates", "tomato", "tomatoes"),
    "tomatoes": ("domates", "tomato", "tomatoes"),
    "lahana": ("cabbage", "lahana"),
    "cabbage": ("cabbage", "lahana"),
    "salatalık": ("cucumber", "salatalık", "salatalik"),
    "salatalik": ("cucumber", "salatalık", "salatalik"),
    "cucumber": ("cucumber", "salatalık", "salatalik"),
    "gfr": ("gfr", "glomerular", "filtration"),
    "potasyum": ("potasyum", "potassium"),
    "potassium": ("potasyum", "potassium"),
    "fosfor": ("fosfor", "phosphorus", "phosphate"),
    "phosphorus": ("fosfor", "phosphorus", "phosphate"),
    "phosphate": ("fosfor", "phosphorus", "phosphate"),
    "oksalat": ("oksalat", "oxalate", "oxalates"),
    "oxalate": ("oksalat", "oxalate", "oxalates"),
    "oxalates": ("oksalat", "oxalate", "oxalates"),
    "homosistein": ("homosistein", "homocysteine"),
    "homocysteine": ("homosistein", "homocysteine"),
    "cinnamon": ("cinnamon", "cinnamomum", "tarçın", "tarcin"),
    "tarçın": ("cinnamon", "cinnamomum", "tarçın", "tarcin"),
    "collagen": ("collagen", "collagenous", "kolajen", "kolagen"),
    "kolajen": ("collagen", "collagenous", "kolajen", "kolagen"),
    "mct": ("mct", "mcts", "medium-chain", "orta zincirli"),
}

CLAIM_TEXT_STOPWORDS = frozenset({
    "bir", "bu", "şu", "ve", "veya", "ile", "için", "icin", "gibi", "daha",
    "sonra", "kadar", "değil", "degil", "olan", "olarak", "çok", "cok",
    "kan", "şeker", "seker", "hastalık", "hastalik", "hasta", "böbrek",
    "bobrek", "diyabet", "insülin", "insulin",
})

# Anahtar kelime → kılavuz özeti (NKF/KDIGO/DaVita temalı, statik fallback)
GUIDELINE_SNIPPETS = [
    {
        # "kidney" tek başına çok geniş — [663] hidrasyon iddiasına yanlış pozitif veriyordu
        "keywords": ("hyperkalemia", "potassium", "cardiac arrest", "ckd", "hyperkalemi"),
        "min_query_hits": 2,
        "claim_hints": ("potasyum", "hiperkalemi", "kalp durmas", "potassium"),
        "title": "NKF: Potassium and Your CKD Diet",
        "abstract": (
            "Damaged kidneys cannot remove potassium efficiently. High potassium foods "
            "can cause hyperkalemia, which may lead to dangerous heart rhythms and "
            "cardiac arrest in advanced CKD. Dietary potassium management including "
            "food selection and leaching is recommended."
        ),
        "url": "https://www.kidney.org/atoz/content/potassium",
        "source": "guideline",
        "source_tier": "guideline",
    },
    {
        "keywords": ("leaching", "potassium", "cooking", "vegetable", "reduce"),
        "min_query_hits": 2,
        "claim_hints": ("potasyum", "haşla", "hasla", "leaching", "kaynat", "düşür", "dusur"),
        "title": "NKF: Potassium and Your CKD Diet — leaching",
        "abstract": (
            "Leaching or double-cooking vegetables in large amounts of water can "
            "significantly reduce potassium content. Patients should discard cooking "
            "water rather than consuming it, as it contains leached potassium."
        ),
        "url": "https://www.kidney.org/atoz/content/potassium",
        "source": "guideline",
        "source_tier": "guideline",
    },
    {
        "keywords": ("gfr", "glomerular", "filtration", "ckd", "stage"),
        "min_query_hits": 2,
        "claim_hints": ("gfr", "glomerül", "glomerul", "filtrasyon", "süzme", "suzme"),
        "title": "KDIGO CKD Classification",
        "abstract": (
            "GFR categories G1-G5 are fundamental components of CKD staging alongside "
            "cause and albuminuria (CGA classification). GFR is the primary measure "
            "of kidney function for disease staging."
        ),
        "url": "https://kdigo.org/guidelines/ckd-evaluation-and-management/",
        "source": "guideline",
        "source_tier": "guideline",
    },
    {
        "keywords": ("beetroot", "nitrate", "juice", "potassium"),
        "min_query_hits": 2,
        "claim_hints": ("pancar", "nitrat", "beet", "juice", "suyu"),
        "title": "Clinical pharmacokinetics of dietary nitrate",
        "abstract": (
            "Oral nitrate from beetroot requires enterosalivary conversion; plasma "
            "nitrate peaks at 1-2 hours and nitrite at 2-3 hours after ingestion, "
            "not within seconds. Beetroot is high in potassium (~325 mg/100g)."
        ),
        "url": "https://pubmed.ncbi.nlm.nih.gov/",
        "source": "static_reference",
        "source_tier": "static_reference",
    },
    {
        "keywords": ("oxalate", "heat", "cooking", "reduction", "vegetable"),
        "min_query_hits": 2,
        "claim_hints": ("oksalat", "oxalate", "ısı", "isi", "kaynat", "pişir", "pisir"),
        "title": "Oxalate reduction by cooking/leaching",
        "abstract": (
            "Soluble oxalates leach into cooking water during boiling; heat does not "
            "simply destroy oxalate structure but facilitates water-soluble loss. "
            "Studies report 30-87% oxalate reduction depending on method and duration."
        ),
        "url": "https://pubmed.ncbi.nlm.nih.gov/",
        "source": "static_reference",
        "source_tier": "static_reference",
    },
]


def expand_query_variants(query_en: str) -> list[str]:
    """MeSH ve CKD eşanlamlılarıyla genişletilmiş PubMed sorguları."""
    q = (query_en or "").strip()
    if not q:
        return []
    variants = [q]
    lower = q.lower()
    extras = []
    if any(w in lower for w in ("kidney", "renal", "ckd", "dialysis", "creatinine", "potassium", "hyperkalemia")):
        extras.append('"Renal Insufficiency, Chronic"[MeSH]')
        extras.append("(chronic kidney disease OR CKD OR renal failure)")
    if "potassium" in lower or "hyperkalemia" in lower:
        extras.append('"Hyperkalemia"[MeSH]')
    if "oxalate" in lower:
        extras.append('"Oxalates"[MeSH]')
    if "leaching" in lower or "boiling" in lower:
        extras.append("(leaching OR double cook OR demineralization)")
    if extras:
        variants.append(f"({q}) AND ({' OR '.join(extras[:3])})")
    # Daha geniş fallback: ilk 4 anahtar kelime
    tokens = re.findall(r"[a-zA-Z]{3,}", q)
    if len(tokens) >= 3:
        variants.append(" AND ".join(tokens[:4]))
    # Dedupe sırayı koru
    seen = set()
    out = []
    for v in variants:
        if v not in seen:
            seen.add(v)
            out.append(v)
    return out


def pubmed_search_hit_count(query_en: str, retmax: int = 5) -> dict:
    """Gölge test için: hit count + abstract var mı."""
    try:
        pmids = _pubmed_search_ids(query_en, retmax=retmax)
    except requests.RequestException as e:
        return {"query": query_en, "hits": 0, "with_abstract": 0, "error": str(e)}
    if not pmids:
        return {"query": query_en, "hits": 0, "with_abstract": 0}
    try:
        abstracts = _pubmed_fetch_abstracts(pmids)
    except requests.RequestException as e:
        return {"query": query_en, "hits": len(pmids), "with_abstract": 0, "error": str(e)}
    with_abs = sum(1 for p in pmids if p in abstracts and abstracts[p].get("abstract"))
    title_only = sum(1 for p in pmids if p in abstracts and not abstracts[p].get("abstract"))
    return {"query": query_en, "hits": len(pmids), "with_abstract": with_abs, "title_only": title_only}


def retrieve_guideline_snippets(
    query_en: str,
    category: str | None = None,
    claim_text: str | None = None,
) -> list[dict]:
    """
    Anahtar kelime eşleşmesiyle statik kılavuz snippet'leri.

    Daraltma kuralları (v2):
      - Sorguda en az min_query_hits (varsayılan 2) keyword eşleşmesi
      - İddia metninde claim_hints'ten en az biri (PubMed boşken alakasız NKF düşmesin)
    """
    q = (query_en or "").lower()
    claim = (claim_text or "").lower()
    if not q:
        return []
    matched = []
    for snip in GUIDELINE_SNIPPETS:
        keywords = snip["keywords"]
        min_hits = snip.get("min_query_hits", 2)
        hits = sum(1 for kw in keywords if kw in q)
        if hits < min_hits:
            continue
        hints = snip.get("claim_hints") or ()
        if hints and claim and not any(h in claim for h in hints):
            continue
        declared = snip.get("source_tier") or snip.get("source", "guideline")
        inferred = infer_source_tier(snip["url"])
        # Sahte PubMed ana sayfası vb. — beyan edilen yüksek kademeyi URL gerçeği düşürür
        tier = inferred if inferred == "static_reference" else declared
        matched.append({
            "title": snip["title"],
            "abstract": snip["abstract"],
            "pubdate": "",
            "url": snip["url"],
            "source": tier,
            "source_tier": tier,
            "provider": "guideline_snippet",
            "retrieval_tier": "native",
            "evidence_content_type": "abstract",
        })
    return matched[:2]


def _pubmed_candidates_from_query(query_en: str, retmax: int) -> list[dict]:
    try:
        pmids = _pubmed_search_ids(query_en, retmax=retmax)
    except requests.RequestException:
        return []
    if not pmids:
        return []
    try:
        abstracts = _pubmed_fetch_abstracts(pmids)
    except requests.RequestException:
        return []
    with_abstract = [abstracts[p] for p in pmids if p in abstracts and abstracts[p]["abstract"]]
    title_only = [abstracts[p] for p in pmids if p in abstracts and not abstracts[p].get("abstract")]
    return with_abstract or title_only


def _candidate_dedupe_key(item: dict) -> tuple[str, str] | None:
    pmid = str(item.get("pmid") or "").strip()
    if pmid:
        return ("pmid", pmid)
    doi = str(item.get("doi") or "").strip().lower()
    if doi:
        return ("doi", doi)
    url = (item.get("url") or "").strip()
    if url:
        return ("url", url)
    return None


def _enrich_candidate_ids(existing: dict, incoming: dict) -> None:
    """Aynı makalenin sonraki kaynaktan DOI/PMCID/yayınevi URL'sini paketle."""
    if not existing.get("doi") and incoming.get("doi"):
        existing["doi"] = incoming["doi"]
    if not existing.get("pmcid") and incoming.get("pmcid"):
        existing["pmcid"] = incoming["pmcid"]
    extras = list(existing.get("extra_urls") or [])
    seen = set(extras)
    for extra in incoming.get("extra_urls") or []:
        if extra and extra not in seen:
            extras.append(extra)
            seen.add(extra)
    if extras:
        existing["extra_urls"] = extras


def _merge_candidates(existing: list[dict], new_items: list[dict]) -> list[dict]:
    seen: dict[tuple[str, str], dict] = {}
    out: list[dict] = []
    for item in existing:
        key = _candidate_dedupe_key(item)
        if key:
            seen[key] = item
        out.append(item)
    for item in new_items:
        key = _candidate_dedupe_key(item)
        if key and key in seen:
            _enrich_candidate_ids(seen[key], item)
            continue
        if key:
            seen[key] = item
        out.append(item)
    return out


def _add_anchor(low: str, anchors: list[str], seen: set[str]) -> None:
    if low in seen:
        return
    seen.add(low)
    anchors.append(low)
    for syn in ENTITY_SYNONYMS.get(low, ()):
        s = syn.lower()
        if s not in seen:
            seen.add(s)
            anchors.append(s)


def key_terms_from_query(search_query_en: str, claim_text: str | None = None) -> list[str]:
    """
    İddianın ana varlığı (ör. blueberry). search_query_en'deki arka plan
    tıbbi terimler (diabetes, insulin) elenir; eşanlamlılar eklenir.
    claim_text token'ları da eklenir — el sözlüğüne düşmeden Türkçe varlık kalır.
    """
    anchors: list[str] = []
    seen: set[str] = set()
    for tok in re.findall(r"[A-Za-zçğıöşüÇĞİÖŞÜ]{3,}", search_query_en or ""):
        low = tok.lower()
        if low in KEY_TERM_STOPWORDS:
            continue
        _add_anchor(low, anchors, seen)
    for tok in re.findall(r"[A-Za-zçğıöşüÇĞİÖŞÜ]{3,}", claim_text or ""):
        low = tok.lower()
        if low in KEY_TERM_STOPWORDS or low in CLAIM_TEXT_STOPWORDS:
            continue
        _add_anchor(low, anchors, seen)
    return anchors


def candidate_mentions_key_terms(candidate: dict, terms: list[str]) -> bool:
    if not terms:
        return True
    blob = f"{candidate.get('title') or ''} {candidate.get('abstract') or ''}".lower()
    blob = re.sub(r"<[^>]+>", " ", blob)
    blob = html_lib.unescape(blob)
    if any(term in blob for term in terms):
        return True
    # cinnamon ⊂ cinnamomum değil; 6+ karakterlik kök öneki bilimsel adı yakalar.
    words = re.findall(r"[a-zçğıöşü]{4,}", blob)
    for term in terms:
        if len(term) < 6:
            continue
        stem = term[:6]
        if any(w.startswith(stem) for w in words):
            return True
    return False


def filter_candidates_by_key_terms(
    candidates: list[dict],
    search_query_en: str,
    claim_text: str | None = None,
) -> tuple[list[dict], dict]:
    """
    Cosine rerank'tan ÖNCE kaba alaka filtresi: ana varlık başlık+özette yoksa ele.
    Terim yoksa filtre uygulanmaz. Hepsi elenirse boş liste döner;
    geri doldurma `apply_key_term_filter` içinde.
    """
    terms = key_terms_from_query(search_query_en, claim_text)
    if not terms:
        return list(candidates), {
            "applied": False, "terms": [], "kept": len(candidates), "dropped": 0,
        }
    kept = [c for c in candidates if candidate_mentions_key_terms(c, terms)]
    return kept, {
        "applied": True,
        "terms": terms,
        "kept": len(kept),
        "dropped": len(candidates) - len(kept),
    }


def apply_key_term_filter(
    candidates: list[dict],
    search_query_en: str,
    claim_text: str | None = None,
) -> tuple[list[dict], dict]:
    """
    Key-term filtresi. 0 aday kalırsa cosine havuzuna weak_key_term_match ile dön —
    sessiz [] değil (Europe PMC kazancını silmemek için).
    """
    filtered, meta = filter_candidates_by_key_terms(
        candidates, search_query_en, claim_text
    )
    if not filtered and candidates:
        tagged = [{**c, "weak_key_term_match": 1} for c in candidates]
        return tagged, {**meta, "weak_fallback": True, "kept": len(tagged)}
    return filtered, {**meta, "weak_fallback": False}


class SufficiencyResult(NamedTuple):
    sufficient: bool
    relevance_ok: bool
    quality_ok: bool
    reason: str  # no_evidence | weak_key_term_match | low_tier | ok
    best_tier: str | None
    kept_count: int
    max_rerank_score: float | None
    specificity_ok: bool = False
    strong_match: bool = False  # sufficient AND specificity_ok; sufficient tanımı değişmez
    specificity_tier: str = "none"  # none | background | supportive | direct


def _candidate_source_tier(candidate: dict) -> str:
    """URL/domain (+ publication_types); model beyanı yok."""
    return infer_source_tier(
        candidate.get("url") or "",
        publication_types=candidate.get("publication_types"),
    )


def _max_rerank_score(candidates: list[dict]) -> float | None:
    scores: list[float] = []
    for item in candidates:
        raw = item.get("rerank_score")
        if raw is None:
            continue
        try:
            scores.append(float(raw))
        except (TypeError, ValueError):
            continue
    return max(scores) if scores else None


def _top_candidate(kept: list[dict]) -> dict:
    """En yüksek rerank_score; skor yoksa listedeki ilk aday."""
    scored: list[tuple[float, dict]] = []
    for item in kept:
        raw = item.get("rerank_score")
        if raw is None:
            continue
        try:
            scored.append((float(raw), item))
        except (TypeError, ValueError):
            continue
    if scored:
        return max(scored, key=lambda pair: pair[0])[1]
    return kept[0]


def _candidate_evidence_text(candidate: dict) -> str:
    abstract = (candidate.get("abstract") or "").strip()
    if abstract:
        return abstract
    return (candidate.get("title") or "").strip()


def _specificity_nli_result(claim_text: str, candidate: dict) -> dict | None:
    """Mevcut nli_check; yeni model yok. Metin yoksa None (çağrı yok)."""
    evidence_text = _candidate_evidence_text(candidate)
    if not evidence_text:
        return None
    from utils.nli import nli_check
    return nli_check(claim_text, evidence_text)


def _nli_label_conf(nli_result: dict | None) -> tuple[str | None, float]:
    if not nli_result:
        return None, 0.0
    label = nli_result.get("nli_label")
    try:
        conf = float(nli_result.get("nli_confidence") or 0.0)
    except (TypeError, ValueError):
        conf = 0.0
    return label, conf


def _specificity_ok_from_nli(nli_result: dict | None) -> bool:
    label, conf = _nli_label_conf(nli_result)
    return label in ("SUPPORTS", "REFUTES") and conf >= SPECIFICITY_NLI_MIN_CONF


def classify_specificity_tier(
    reason: str,
    relevance_ok: bool,
    quality_ok: bool,
    nli_result: dict | None,
) -> str:
    """Kademeli yeterlilik; assess_evidence_sufficiency kapılarını değiştirmez."""
    if reason in ("no_evidence", "weak_key_term_match"):
        return "none"
    if _specificity_ok_from_nli(nli_result):
        return "direct"
    label, conf = _nli_label_conf(nli_result)
    if label in ("SUPPORTS", "REFUTES") and conf >= SPECIFICITY_SUPPORTIVE_MIN_CONF:
        return "supportive"
    if relevance_ok and (
        not quality_ok
        or nli_result is None
        or label not in ("SUPPORTS", "REFUTES")
        or conf < SPECIFICITY_SUPPORTIVE_MIN_CONF
    ):
        return "background"
    return "none"


def classify_evidence_expectation(
    claim_text: str,
    all_candidates_specificity_scores: list[dict] | None,
) -> str | None:
    """
    Retrieval havuzundaki tüm adayların specificity NLI skorlarına bakar.
    Hiçbiri SUPPORTS/REFUTES + conf >= 0.5 değilse doğrudan kanıt beklenmez.
    claim_text imzada durur (çağıran bağlamı); eşik yalnızca skor listesine bakılır.
    """
    del claim_text
    for nli in all_candidates_specificity_scores or []:
        label, conf = _nli_label_conf(nli)
        if label in ("SUPPORTS", "REFUTES") and conf >= SPECIFICITY_SUPPORTIVE_MIN_CONF:
            return None
    return EPISTEMIC_NO_DIRECT


def collect_specificity_nli_scores(
    claim_text: str,
    candidates: list[dict] | None,
) -> list[dict]:
    """Paketteki her aday için yerel nli_check; top-1 ile sınırlı değil."""
    scores: list[dict] = []
    for candidate in candidates or []:
        nli = _specificity_nli_result(claim_text, candidate)
        if nli:
            scores.append(nli)
    return scores


def assess_evidence_sufficiency(
    candidates: list[dict],
    claim_text: str,
    search_query_en: str | None = None,
) -> SufficiencyResult:
    """
    Kaynak-agnostik yeterlilik: alaka (key-term) × kademe (infer_source_tier).
    sufficient yalnızca ikisi de True ise True. Harmanlanmış tek skor yok.
    Native ve Serper sonrası aynı fonksiyon — kaynağa özel eşik yok.

    relevance_ok ve quality_ok geçtikten sonra ek üst kademe: top adayın
    abstract'ı nli_check ile iddianın spesifik önermesini SUPPORTS/REFUTES
    ediyor mu (confidence >= SPECIFICITY_NLI_MIN_CONF). Bu specificity_ok /
    strong_match'i belirler; sufficient tanımını değiştirmez.
    """
    if not candidates:
        return SufficiencyResult(
            False, False, False, "no_evidence", None, 0, None,
            specificity_tier="none",
        )

    kept, _meta = filter_candidates_by_key_terms(
        candidates, search_query_en or "", claim_text
    )
    if not kept:
        return SufficiencyResult(
            False, False, False, "weak_key_term_match", None, 0,
            _max_rerank_score(candidates),
            specificity_tier="none",
        )

    tiers = [_candidate_source_tier(c) for c in kept]
    quality_ok = any(t in SUFFICIENT_QUALITY_TIERS for t in tiers)
    best_tier = max(tiers, key=lambda t: TIER_CONF_CAP.get(t, 0.0)) if tiers else None
    max_score = _max_rerank_score(kept)
    if not quality_ok:
        return SufficiencyResult(
            False, True, False, "low_tier", best_tier, len(kept), max_score,
            specificity_tier="background",
        )

    nli_result = _specificity_nli_result(claim_text, _top_candidate(kept))
    specificity_ok = _specificity_ok_from_nli(nli_result)
    return SufficiencyResult(
        True, True, True, "ok", best_tier, len(kept), max_score,
        specificity_ok, specificity_ok,
        classify_specificity_tier("ok", True, True, nli_result),
    )


COMPONENT_STRONG_TIERS = frozenset({"direct", "supportive"})
COMPONENT_WEAK_TIERS = frozenset({"background", "none"})


def _candidate_brief(candidate: dict) -> dict:
    return {
        "title": (candidate.get("title") or "").strip(),
        "url": (candidate.get("url") or "").strip(),
        "source_tier": (
            (candidate.get("source_tier") or candidate.get("source") or "").strip()
            or _candidate_source_tier(candidate)
        ),
    }


def _score_claim_against_pool(
    text: str,
    candidates: list[dict],
    search_query_en: str | None,
) -> dict:
    suff = assess_evidence_sufficiency(candidates, text, search_query_en)
    kept, _meta = filter_candidates_by_key_terms(
        candidates, search_query_en or "", text
    )
    return {
        "text": text,
        "tier": suff.specificity_tier,
        "reason": suff.reason,
        "kept": suff.kept_count,
        "candidates": [_candidate_brief(c) for c in kept],
    }


def component_has_tier_gap(component_rows: list[dict]) -> bool:
    """Biri direct/supportive, diğeri background/none — decomposition yeni bilgi ekliyor."""
    tiers = {(row.get("tier") or "none") for row in component_rows}
    return bool(tiers & COMPONENT_STRONG_TIERS) and bool(tiers & COMPONENT_WEAK_TIERS)


def score_component_evidence(
    claim_text: str,
    candidates: list[dict] | None,
    search_query_en: str | None = None,
) -> dict:
    """
    Aynı retrieval adaylarını her alt-iddia için yeniden puanla. Yeni arama yok.
    Bileşik değilse {}.
    """
    from utils.reviewer_summary import decompose_claim_for_retrieval

    parts = decompose_claim_for_retrieval(claim_text or "")
    if len(parts) < 2:
        return {}
    pool = list(candidates or [])
    return {
        "whole": _score_claim_against_pool(claim_text, pool, search_query_en),
        "components": [
            _score_claim_against_pool(part, pool, search_query_en) for part in parts
        ],
    }


def _tag_native_item(item: dict) -> dict:
    out = dict(item)
    out.setdefault("retrieval_tier", "native")
    out.setdefault("evidence_content_type", "abstract")
    out.setdefault("evidence_source", "live")
    return out


def collect_native_candidates(
    claim_text: str,
    search_query_en: str | None = None,
    category: str | None = None,
    *,
    include_europe_pmc: bool = True,
    include_medlineplus: bool = True,
) -> tuple[list[dict], list[str]]:
    """PubMed + Europe PMC + MedlinePlus (+ boşsa kılavuz). Serper yok."""
    query = search_query_en or claim_text
    candidates: list[dict] = []
    path_parts: list[str] = []

    pubmed_path = None
    for variant in expand_query_variants(query):
        batch = _pubmed_candidates_from_query(variant, retmax=CANDIDATE_POOL_SIZE)
        if batch:
            candidates = _merge_candidates(candidates, batch)
            pubmed_path = "pubmed_mesh" if variant != query else "pubmed"
        if len(candidates) >= CANDIDATE_POOL_SIZE:
            break
    if pubmed_path:
        path_parts.append(pubmed_path)

    if include_europe_pmc and query:
        epmc = europepmc_candidates(query, retmax=CANDIDATE_POOL_SIZE)
        before = len(candidates)
        candidates = _merge_candidates(candidates, epmc)
        if len(candidates) > before:
            path_parts.append("europepmc")

    if include_medlineplus and query:
        mp = medlineplus_candidates(query, retmax=5)
        before = len(candidates)
        candidates = _merge_candidates(candidates, mp)
        if len(candidates) > before:
            path_parts.append("medlineplus")

    if not candidates:
        guidelines = retrieve_guideline_snippets(query, category, claim_text=claim_text)
        if guidelines:
            candidates = list(guidelines)
            path_parts.append("guideline")

    return [_tag_native_item(c) for c in candidates], path_parts


def retrieve_hybrid_evidence(
    claim_text: str,
    search_query_en: str | None = None,
    category: str | None = None,
    *,
    include_europe_pmc: bool = True,
    include_medlineplus: bool = True,
    include_serper: bool = True,
    origin_claim_id: int | None = None,
    skip_live_retrieval: bool = False,
    use_topic_cache: bool = True,
    write_topic_cache: bool = True,
    conn=None,
) -> tuple[list[dict], str, dict]:
    """
    Hibrit kanıt getirme: topic cache → besin DB → PubMed → … → Serper.

    Dönüş: (evidence_list, retrieval_path, meta)
    meta: topic_key, cache_candidates, live_candidates, cache_in_final, live_in_final
    """
    from utils.evidence_topic_cache import (
        lookup_topic_cache,
        store_topic_cache,
        topic_key_for_claim,
    )
    from utils.nutrition_lookup import is_nutrition_quantity_claim, lookup_nutrition_evidence

    meta: dict = {
        "topic_key": None,
        "cache_candidates": 0,
        "live_candidates": 0,
        "cache_in_final": 0,
        "live_in_final": 0,
    }

    topic_key = topic_key_for_claim(
        claim_text, category, search_query_en=search_query_en
    )
    meta["topic_key"] = topic_key

    db_conn = conn
    own_conn = None
    if topic_key and db_conn is None:
        from utils.db import get_conn
        own_conn = get_conn()
        db_conn = own_conn

    cache_items: list[dict] = []
    if topic_key and use_topic_cache and db_conn is not None:
        cache_items = lookup_topic_cache(db_conn, topic_key)
        meta["cache_candidates"] = len(cache_items)

    if is_nutrition_quantity_claim(claim_text) and not skip_live_retrieval:
        nut = lookup_nutrition_evidence(claim_text)
        if nut:
            tagged = [_tag_native_item(x) for x in nut]
            merged = _merge_candidates(cache_items, tagged) if cache_items else tagged
            if topic_key and db_conn is not None and write_topic_cache:
                store_topic_cache(db_conn, topic_key, tagged, origin_claim_id)
            meta["live_candidates"] = len(tagged)
            meta["live_in_final"] = sum(
                1 for e in merged if e.get("evidence_source") == "live"
            )
            meta["cache_in_final"] = sum(
                1 for e in merged if e.get("evidence_source") == "cache"
            )
            if own_conn:
                own_conn.close()
            return merged, nut[0].get("source") or "nutrition_db", meta

    if not search_query_en:
        print("[evidence] uyarı: search_query_en yok, ham iddia metniyle aranıyor")
    query = search_query_en or claim_text

    path_parts: list[str] = []
    if cache_items:
        path_parts.append("topic_cache")

    live_candidates: list[dict] = []
    if skip_live_retrieval:
        candidates = list(cache_items)
    else:
        live_candidates, native_paths = collect_native_candidates(
            claim_text, query, category,
            include_europe_pmc=include_europe_pmc,
            include_medlineplus=include_medlineplus,
        )
        path_parts.extend(native_paths)
        meta["live_candidates"] = len(live_candidates)

        native_suff = assess_evidence_sufficiency(live_candidates, claim_text, query)
        if not native_suff.sufficient and include_serper:
            print(
                f"[evidence] native yetersiz (reason={native_suff.reason} "
                f"relevance_ok={native_suff.relevance_ok} "
                f"quality_ok={native_suff.quality_ok}) — Serper"
            )
            serper = retrieve_serper_evidence(query)
            if serper:
                for s in serper:
                    s.setdefault("evidence_source", "live")
                live_candidates = _merge_candidates(live_candidates, serper)
                path_parts.append("serper")
                meta["live_candidates"] = len(live_candidates)
                _attach_rerank_scores(claim_text, live_candidates)
                serper_suff = assess_evidence_sufficiency(
                    live_candidates, claim_text, query
                )
                if serper_suff.sufficient:
                    print(
                        f"[evidence] Serper yeterli (reason={serper_suff.reason} "
                        f"best_tier={serper_suff.best_tier})"
                    )
                else:
                    print(
                        f"[evidence] Serper sonrası hâlâ yetersiz "
                        f"(reason={serper_suff.reason} "
                        f"relevance_ok={serper_suff.relevance_ok} "
                        f"quality_ok={serper_suff.quality_ok}) — Claude web_search güvenlik ağı"
                    )

        if topic_key and db_conn is not None and live_candidates and write_topic_cache:
            store_topic_cache(db_conn, topic_key, live_candidates, origin_claim_id)

        candidates = (
            _merge_candidates(cache_items, live_candidates)
            if cache_items else live_candidates
        )
        _attach_rerank_scores(claim_text, candidates)

    if not candidates:
        if own_conn:
            own_conn.close()
        return [], "none", meta

    if skip_live_retrieval:
        _attach_rerank_scores(claim_text, candidates)

    filtered, filt_meta = apply_key_term_filter(candidates, query, claim_text)
    if filt_meta.get("weak_fallback"):
        print(
            "[evidence] alaka filtresi 0 aday bıraktı — cosine-only zayıf kanıta geri dönülüyor "
            "(weak_key_term_match)"
        )
        path_parts.append("weak_filter_fallback")
    elif filt_meta.get("applied") and filt_meta.get("dropped"):
        print(
            f"[evidence] alaka filtresi: {filt_meta['dropped']} aday elendi "
            f"(terimler={filt_meta['terms'][:6]}, kalan={filt_meta['kept']})"
        )
    if not filtered:
        if own_conn:
            own_conn.close()
        return [], "+".join(path_parts) if path_parts else "none", meta

    ranked = _dense_rerank(claim_text, filtered, ESCALATE_PACKAGE_SIZE)
    meta["cache_in_final"] = sum(
        1 for e in ranked if e.get("evidence_source") == "cache"
    )
    meta["live_in_final"] = sum(
        1 for e in ranked if e.get("evidence_source") == "live"
    )
    if own_conn:
        own_conn.close()
    return ranked, "+".join(path_parts) if path_parts else "none", meta


def _pubmed_search_ids(query_en: str, retmax: int) -> list[str]:
    """Sparse retrieval: PubMed'in kendi arama motoruyla aday havuzu."""
    params = {"db": "pubmed", "term": query_en, "retmax": retmax, "retmode": "json",
              "sort": "relevance"}
    r = requests.get(PUBMED_ESEARCH, params=params, timeout=15)
    r.raise_for_status()
    return r.json().get("esearchresult", {}).get("idlist", [])


def parse_pubmed_efetch_xml(xml_text: str) -> dict:
    """
    EFetch XML → {pmid: {title, abstract, pubdate, url, publication_types, source_tier}}.
    Canlı HTTP olmadan test edilebilir.
    """
    out = {}
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        print(f"[evidence] EFetch XML parse hatası: {e}")
        return out
    for article in root.findall(".//PubmedArticle"):
        pmid_el = article.find(".//PMID")
        pmid = pmid_el.text if pmid_el is not None else None
        if not pmid:
            continue
        title_el = article.find(".//ArticleTitle")
        title = "".join(title_el.itertext()).strip() if title_el is not None else ""
        abstract_parts = article.findall(".//AbstractText")
        abstract = " ".join("".join(a.itertext()).strip() for a in abstract_parts).strip()
        pubdate_el = article.find(".//PubDate/Year")
        pubdate = pubdate_el.text if pubdate_el is not None else ""
        pub_types = [
            (el.text or "").strip()
            for el in article.findall(".//PublicationType")
            if (el.text or "").strip()
        ]
        url = f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"
        doi = None
        pmcid = None
        extra_urls: list[str] = []
        for aid in article.findall(".//ArticleId"):
            id_type = (aid.get("IdType") or "").lower()
            val = (aid.text or "").strip()
            if not val:
                continue
            if id_type == "doi":
                doi = val
                extra_urls.append(f"https://doi.org/{val}")
            elif id_type == "pmc":
                pmcid = val
                extra_urls.append(f"https://www.ncbi.nlm.nih.gov/pmc/articles/{val}/")
        if not doi:
            for eloc in article.findall(".//ELocationID"):
                if (eloc.get("EIdType") or "").lower() != "doi":
                    continue
                val = (eloc.text or "").strip()
                if val:
                    doi = val
                    extra_urls.append(f"https://doi.org/{val}")
                    break
        tier = infer_source_tier(url, publication_types=pub_types)
        out[pmid] = {
            "title": title,
            "abstract": abstract,
            "pubdate": pubdate,
            "url": url,
            "pmid": pmid,
            "doi": doi,
            "pmcid": pmcid,
            "extra_urls": extra_urls,
            "publication_types": pub_types,
            "source": tier,
            "source_tier": tier,
            "provider": "pubmed",
            "retrieval_tier": "native",
            "evidence_content_type": "abstract",
        }
    return out


def _pubmed_fetch_abstracts(pmids: list[str]) -> dict:
    """
    EFetch ile GERÇEK özet metinlerini çeker. ESummary (önceki versiyon) sadece
    başlık döner — bu yeterli kanıt sağlamaz, bu yüzden EFetch'e geçildi.
    Dönüş: {pmid: {title, abstract, pubdate, url, publication_types, source_tier}}
    """
    if not pmids:
        return {}
    params = {"db": "pubmed", "id": ",".join(pmids), "retmode": "xml", "rettype": "abstract"}
    r = requests.get(PUBMED_EFETCH, params=params, timeout=20)
    r.raise_for_status()
    return parse_pubmed_efetch_xml(r.text)


def _europepmc_pub_types(result: dict) -> list[str]:
    types: list[str] = []
    ptl = result.get("pubTypeList") or {}
    raw_list = ptl.get("pubType") if isinstance(ptl, dict) else None
    if isinstance(raw_list, str):
        raw_list = [raw_list]
    if isinstance(raw_list, list):
        types.extend(str(t) for t in raw_list if t)
    pub_type_str = result.get("pubType") or ""
    if pub_type_str and not types:
        types.extend(p.strip() for p in str(pub_type_str).split(";") if p.strip())
    return types


def _europepmc_is_preprint(result: dict, pub_types: list[str]) -> bool:
    if str(result.get("source") or "").upper() == "PPR":
        return True
    journal = ""
    info = result.get("journalInfo") or {}
    if isinstance(info, dict):
        journal = str((info.get("journal") or {}).get("title") or "")
    journal = journal or str(result.get("journalTitle") or "")
    jl = journal.lower()
    if any(s in jl for s in ("biorxiv", "medrxiv", "arxiv")):
        return True
    return any("preprint" in (t or "").lower() for t in pub_types)


def parse_europepmc_search_json(payload: dict) -> list[dict]:
    """Europe PMC core JSON → evidence aday listesi (HTTP yok)."""
    results = ((payload or {}).get("resultList") or {}).get("result") or []
    if isinstance(results, dict):
        results = [results]
    out = []
    for result in results:
        if not isinstance(result, dict):
            continue
        title = (result.get("title") or "").strip()
        abstract = (result.get("abstractText") or "").strip()
        pmid = str(result.get("pmid") or "").strip() or None
        doi = (result.get("doi") or "").strip() or None
        pmcid = str(result.get("pmcid") or "").strip() or None
        extra_urls: list[str] = []
        ftl = result.get("fullTextUrlList") or {}
        ft_items = ftl.get("fullTextUrl") if isinstance(ftl, dict) else None
        if isinstance(ft_items, dict):
            ft_items = [ft_items]
        for ft in ft_items or []:
            if isinstance(ft, dict) and ft.get("url"):
                extra_urls.append(str(ft["url"]))
        if doi:
            extra_urls.append(f"https://doi.org/{doi}")
        if pmcid:
            extra_urls.append(f"https://www.ncbi.nlm.nih.gov/pmc/articles/{pmcid}/")
        source = str(result.get("source") or "MED")
        rec_id = str(result.get("id") or pmid or "")
        pub_types = _europepmc_pub_types(result)
        is_preprint = _europepmc_is_preprint(result, pub_types)
        if pmid:
            url = f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"
        else:
            url = f"https://europepmc.org/article/{source}/{rec_id}"
        if is_preprint and "preprint" not in [t.lower() for t in pub_types]:
            pub_types = list(pub_types) + ["Preprint"]
        if is_preprint:
            tier = "preprint"
        else:
            tier = infer_source_tier(url, publication_types=pub_types)
        out.append({
            "title": title,
            "abstract": abstract,
            "pubdate": str(result.get("pubYear") or result.get("firstPublicationDate") or ""),
            "url": url,
            "pmid": pmid,
            "doi": doi,
            "pmcid": pmcid,
            "extra_urls": extra_urls,
            "publication_types": pub_types,
            "source": tier,
            "source_tier": tier,
            "provider": "europepmc",
            "retrieval_tier": "native",
            "evidence_content_type": "abstract",
        })
    return out


def europepmc_candidates(query_en: str, retmax: int = CANDIDATE_POOL_SIZE) -> list[dict]:
    """Aynı search_query_en ile Europe PMC core araması (abstractText dahil)."""
    q = (query_en or "").strip()
    if not q:
        return []
    params = {
        "query": q,
        "format": "json",
        "resultType": "core",
        "pageSize": retmax,
    }
    try:
        r = requests.get(EUROPEPMC_SEARCH, params=params, timeout=15)
        r.raise_for_status()
        return parse_europepmc_search_json(r.json())
    except (requests.RequestException, ValueError) as e:
        print(f"[evidence] Europe PMC hatası: {e}")
        return []


def _strip_markup(text: str) -> str:
    cleaned = re.sub(r"<[^>]+>", " ", text or "")
    cleaned = html_lib.unescape(cleaned)
    return re.sub(r"\s+", " ", cleaned).strip()


def parse_medlineplus_xml(xml_text: str) -> list[dict]:
    """
    MedlinePlus wsearch XML → evidence aday listesi.

    Şema (canlı doğrulandı): nlmSearchResult/list/document[@url]
      content[@name=title], content[@name=FullSummary], content[@name=snippet]
    """
    out = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        print(f"[evidence] MedlinePlus XML parse hatası: {e}")
        return out
    for doc in root.findall(".//document"):
        url = (doc.get("url") or "").strip()
        title_el = doc.find("./content[@name='title']")
        summary_el = doc.find("./content[@name='FullSummary']")
        snippet_el = doc.find("./content[@name='snippet']")
        title = _strip_markup("".join(title_el.itertext()) if title_el is not None else "")
        summary = _strip_markup("".join(summary_el.itertext()) if summary_el is not None else "")
        snippet = _strip_markup("".join(snippet_el.itertext()) if snippet_el is not None else "")
        abstract = summary or snippet
        if not url or not (title or abstract):
            continue
        tier = infer_source_tier(url) if url else "guideline"
        if tier == "other":
            tier = "guideline"  # NIH tüketici sağlığı — allowlist dışı URL olursa da guideline
        out.append({
            "title": title,
            "abstract": abstract,
            "pubdate": "",
            "url": url,
            "source": tier,
            "source_tier": tier,
            "provider": "medlineplus",
            "retrieval_tier": "native",
            "evidence_content_type": "abstract",
        })
    return out


def _medlineplus_query_variants(query_en: str) -> list[str]:
    """
    MedlinePlus tüketici konu dizini uzun PubMed sorgularında 0 döner
    ('blueberry insulin sensitivity microvascular diabetes' → count=0,
    'potassium' → 19). Tam sorguyu dene, sonra 2-3 token ve katalog anahtarları.
    """
    q = (query_en or "").strip()
    if not q:
        return []
    tokens = re.findall(r"[A-Za-z]{3,}", q)
    variants = [q]
    if len(tokens) >= 3:
        variants.append(" ".join(tokens[:3]))
    if len(tokens) >= 2:
        variants.append(" ".join(tokens[:2]))
    catalog = {
        "kidney", "potassium", "diabetes", "insulin", "vitamin",
        "glomerular", "filtration", "creatinine", "sodium",
        "oxalate", "fiber", "hydration", "blood", "pressure",
        "hyperkalemia", "dialysis", "gfr",
    }
    hits = [t for t in tokens if t.lower() in catalog]
    if len(hits) >= 2:
        variants.append(" ".join(hits[:3]))
    if hits:
        variants.append(hits[0])
    seen: set[str] = set()
    out: list[str] = []
    for v in variants:
        key = v.lower()
        if key not in seen:
            seen.add(key)
            out.append(v)
    return out


def medlineplus_candidates(query_en: str, retmax: int = 5) -> list[dict]:
    """NIH MedlinePlus healthTopics — API anahtarı yok."""
    merged: list[dict] = []
    for q in _medlineplus_query_variants(query_en):
        params = {"db": "healthTopics", "term": q, "retmax": retmax}
        try:
            r = requests.get(MEDLINEPLUS_SEARCH, params=params, timeout=15)
            r.raise_for_status()
            batch = parse_medlineplus_xml(r.text)
        except requests.RequestException as e:
            print(f"[evidence] MedlinePlus hatası: {e}")
            continue
        merged = _merge_candidates(merged, batch)
        if len(merged) >= retmax:
            break
    return merged[:retmax]


def _approx_tokens(text: str) -> int:
    return max(1, int(len((text or "").split()) * 1.3))


def _split_abstract_chunks(text: str, max_words: int = 120) -> list[str]:
    text = re.sub(r"\s+", " ", (text or "").strip())
    if not text:
        return []
    sentences = re.split(r"(?<=[.!?])\s+", text)
    chunks: list[str] = []
    buf: list[str] = []
    buf_words = 0
    for sent in sentences:
        w = len(sent.split())
        if buf and buf_words + w > max_words:
            chunks.append(" ".join(buf))
            buf = [sent]
            buf_words = w
        else:
            buf.append(sent)
            buf_words += w
    if buf:
        chunks.append(" ".join(buf))
    return chunks or [text]


def best_evidence_snippet(
    claim_text: str,
    abstract: str,
    *,
    min_tokens: int = 500,
    max_tokens: int = 1000,
) -> str:
    """
    Tam abstract yerine iddiaya (cosine) en yakın 500–1000 token alt-parça.
    Embedder yoksa ilk max_tokens kelimeye düşer.

    NLI evidence_text / partial_caveat girdisi olarak KULLANMAYIN.
    Claude escalate paketini kısaltmak için var. NLI+caveat çok-parça join
    bekler; tek-parça snippet #1282/#905 güvenlik kontrolünü bozar
    (pipeline/03_factcheck.py, utils/nli.should_escalate).
    """
    abstract = (abstract or "").strip()
    if not abstract:
        return ""
    chunks = _split_abstract_chunks(abstract)
    if len(chunks) == 1 and _approx_tokens(chunks[0]) <= max_tokens:
        return chunks[0]

    embedder = _get_embedder()
    if embedder is None or len(chunks) == 1:
        words = abstract.split()
        cap = int(max_tokens / 1.3)
        return " ".join(words[:cap])

    import numpy as np
    claim_vec = embedder.encode([claim_text])[0]
    chunk_vecs = embedder.encode(chunks)
    norms = np.linalg.norm(chunk_vecs, axis=1) * np.linalg.norm(claim_vec) + 1e-9
    sims = (chunk_vecs @ claim_vec) / norms
    order = sorted(range(len(chunks)), key=lambda i: sims[i], reverse=True)

    picked: list[str] = []
    total = 0
    for idx in order:
        piece = chunks[idx]
        t = _approx_tokens(piece)
        if total + t > max_tokens and picked:
            break
        picked.append(piece)
        total += t
        if total >= min_tokens:
            break
    if not picked:
        return chunks[order[0]]
    return " ".join(picked)


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


def _attach_rerank_scores(claim_text: str, candidates: list[dict]) -> list[dict]:
    """Dense cosine skorunu adaya rerank_score olarak yazar; kesmez. Embedder yoksa no-op."""
    if not candidates:
        return candidates
    embedder = _get_embedder()
    if embedder is None:
        return candidates
    texts = [f"{c.get('title') or ''} {c.get('abstract') or ''}".strip() for c in candidates]
    try:
        import numpy as np
        claim_vec = embedder.encode([claim_text])[0]
        cand_vecs = embedder.encode(texts)
        norms = np.linalg.norm(cand_vecs, axis=1) * np.linalg.norm(claim_vec) + 1e-9
        sims = (cand_vecs @ claim_vec) / norms
        for item, score in zip(candidates, sims):
            item["rerank_score"] = float(score)
    except Exception as e:
        print(f"[evidence] dense rerank hatası ({e}), sparse sıralamayla devam ediliyor")
    return candidates


def _dense_rerank(claim_text: str, candidates: list[dict], top_k: int) -> list[dict]:
    """
    Adayları claim_text'e (orijinal dildeki iddia — model çok dilli olduğu için
    çeviriye gerek yok) anlamca en yakın olacak şekilde yeniden sıralar.
    Embedder yoksa veya aday listesi boşsa, PubMed'in kendi sıralamasından
    ilk top_k'yı döner (zarif düşüş — sistem hiçbir durumda çökmez).
    """
    if not candidates:
        return []
    _attach_rerank_scores(claim_text, candidates)
    if any(c.get("rerank_score") is not None for c in candidates):
        ranked = sorted(
            candidates,
            key=lambda c: float(c.get("rerank_score") or 0.0),
            reverse=True,
        )
        return ranked[:top_k]
    return candidates[:top_k]


def parse_serper_search_json(payload: dict) -> list[dict]:
    """Serper organic JSON → evidence aday listesi (HTTP yok)."""
    organic = (payload or {}).get("organic") or []
    if isinstance(organic, dict):
        organic = [organic]
    out = []
    for item in organic:
        if not isinstance(item, dict):
            continue
        title = (item.get("title") or "").strip()
        snippet = (item.get("snippet") or "").strip()
        url = (item.get("link") or item.get("url") or "").strip()
        if not url or not (title or snippet):
            continue
        tier = infer_source_tier(url)
        out.append({
            "title": title,
            "abstract": snippet,
            "pubdate": "",
            "url": url,
            "source": tier,
            "source_tier": tier,
            "provider": "serper",
            "retrieval_tier": "serper",
            "evidence_content_type": "search_snippet",
        })
    return out


def retrieve_serper_evidence(
    search_query_en: str,
    retmax: int = CANDIDATE_POOL_SIZE,
) -> list[dict]:
    """search_query_en ile Serper.dev organic arama. Sayfa gövdesi çekilmez."""
    q = (search_query_en or "").strip()
    if not q:
        return []
    key = (os.environ.get("SERPER_API_KEY") or "").strip()
    if not key:
        print("[evidence] SERPER_API_KEY yok — Serper atlandı")
        return []
    try:
        r = requests.post(
            SERPER_SEARCH,
            headers={"X-API-KEY": key, "Content-Type": "application/json"},
            json={"q": q, "num": retmax},
            timeout=15,
        )
        r.raise_for_status()
        return parse_serper_search_json(r.json())
    except (requests.RequestException, ValueError) as e:
        print(f"[evidence] Serper hatası: {e}")
        return []


def retrieve_pubmed_evidence(claim_text: str, search_query_en: str | None = None,
                             category: str | None = None) -> list[dict]:
    """
    Hibrit kanıt getirme (geriye uyumlu API).

    Önce retrieve_hybrid_evidence dener; boşsa eski tek-sorgu PubMed yoluna düşer.
    """
    evidence, path, _meta = retrieve_hybrid_evidence(claim_text, search_query_en, category)
    if evidence:
        if path != "pubmed":
            print(f"[evidence] hibrit yol: {path} ({len(evidence)} parça)")
        return evidence

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

    with_abstract = [abstracts[p] for p in pmids if p in abstracts and abstracts[p]["abstract"]]
    candidates = with_abstract or [abstracts[p] for p in pmids if p in abstracts]

    return _dense_rerank(claim_text, candidates, FINAL_EVIDENCE_COUNT)
