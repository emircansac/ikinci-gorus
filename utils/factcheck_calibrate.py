"""
Fact-check çıktısını modele bırakmadan önce sunucu tarafında kalibre et.

Neden: escalate_factcheck JSON'u ham haliyle kaydedilince üç tekrarlayan hata
görüldü (claim #96, #110 ve tartışmalı@0.55 kümesi):

  1. Kaynak iddiayı desteklerken model "yanlış" diyebiliyor (tersine verdict).
  2. Wikipedia gibi dolaylı/genel sayfalara %85 güven bağlanabiliyor.
  3. Birbirinden bağımsız mekanizmalarda confidence tam 0.55'e yığılabiliyor
    10|     (model "emin değilim" varsayılanı).  tartşmalı + 0.55 → suspicion 61.0.

Bu modül LLM'i değiştirmez; gelen JSON'u kaynak kalitesi / stance tutarlılığı
kurallarıyla kırpar ve bayrak üretir. Prompt iyileştirmesi ayrı (claude_client).
"""
from __future__ import annotations

import re
from urllib.parse import urlparse

VALID_VERDICTS = {"doğrulanmış", "yanlış", "tartışmalı", "belirsiz"}
VALID_DIRECTNESS = {"direct", "indirect", "unrelated"}
VALID_STANCE = {"supports", "contradicts", "mixed", "insufficient"}
VALID_TIERS = {
    "guideline", "systematic_review", "primary_study",
    "case_report", "preprint",
    "nutrition_db", "usda_cache_static", "encyclopedia", "other",
}

TIER_CONF_CAP = {
    "encyclopedia": 0.45,
    "other": 0.65,
    "preprint": 0.70,       # peer-review yok; primary_study altı
    "case_report": 0.75,    # provisional, kalibre edilmedi — primary_study (0.85) bir altı
    "primary_study": 0.85,
    "nutrition_db": 0.85,
    "usda_cache_static": 0.75,
    "systematic_review": 0.92,
    "guideline": 0.95,
}

# Sayısal tavan henüz kalibre edilmedi; bayrakta ":provisional" yazılır.
TIER_CAP_PROVISIONAL = frozenset({"case_report"})

# tartşmalı için modelin sık düştüğü "varsayılan emin değilim" değerleri
DEFAULT_UNCERTAIN_CONF = {0.5, 0.55, 0.6}

CITE_SOURCES = ("retrieval_cited", "web_search_override", "web_search_only")

ENCYCLOPEDIA_HOSTS = (
    "wikipedia.org",
    "wikiwand.com",
)
GUIDELINE_HOSTS = (
    "who.int",
    "cdc.gov",
    "fda.gov",
    "diabetes.org",
    "kidney.org",
    "kidneyfund.org",
    "kdigo.org",
    "nice.org.uk",
    "ema.europa.eu",
    "mayoclinic.org",
    "health.harvard.edu",
    "niddk.nih.gov",       # MADDE 1
    "medlineplus.gov",     # MADDE 4 — NIH tüketici sağlığı
)
REVIEW_HOSTS = (
    "cochranelibrary.com",
    "cochrane.org",
)
NUTRITION_DB_HOSTS = (
    "fdc.nal.usda.gov",
    "nal.usda.gov",
    "nutritionvalue.org",
)
PRIMARY_HOSTS = (
    "pubmed.ncbi.nlm.nih.gov",
    "pmc.ncbi.nlm.nih.gov",
    "ncbi.nlm.nih.gov",
    "doi.org",
    "nature.com",
    "thelancet.com",
    "nejm.org",
    "bmj.com",
    "sciencedirect.com",
    "springer.com",
    "wiley.com",
    "tandfonline.com",
    "journals.physiology.org",
    "mdpi.com",                 # MADDE 1
    "ajcn.nutrition.org",       # MADDE 1
    "academic.oup.com",         # MADDE 1
    "europepmc.org",            # MADDE 3
)
PREPRINT_HOSTS = (
    "biorxiv.org",
    "medrxiv.org",
    "arxiv.org",
)

_DOI_RE = re.compile(r"(10\.\d{4,9}/[-._;()/:A-Z0-9]+)", re.IGNORECASE)


def _host(url: str) -> str:
    try:
        host = (urlparse(url).netloc or "").lower()
    except Exception:
        return ""
    if host.startswith("www."):
        host = host[4:]
    return host


def _host_matches(host: str, needles: tuple[str, ...]) -> bool:
    return any(host == n or host.endswith("." + n) for n in needles)


def source_tier_from_publication_types(publication_types: list[str] | None) -> str | None:
    """
    PubMed <PublicationType> / Europe PMC pubType metadata → source_tier.

    Model beyanı değil; veritabanının resmi sınıflandırması.
    Boş liste → None (çağıran URL varsayılanına düşer).
    """
    if not publication_types:
        return None
    norms: list[str] = []
    for raw in publication_types:
        if not raw:
            continue
        norms.append(re.sub(r"[-_]+", " ", str(raw).lower()).strip())
    if not norms:
        return None
    if any("preprint" in n for n in norms):
        return "preprint"
    if any("systematic review" in n or "meta analysis" in n for n in norms):
        return "systematic_review"
    if any(n == "case reports" or n == "case report" or n.startswith("case report")
           for n in norms):
        return "case_report"
    # Randomized Controlled Trial ve diğer etiketler → primary_study
    return "primary_study"


def _is_preprint_url(url: str) -> bool:
    host = _host(url or "")
    if _host_matches(host, PREPRINT_HOSTS):
        return True
    lowered = (url or "").lower()
    if "europepmc.org" in host and "/ppr" in lowered:
        return True
    return False


def infer_source_tier(
    url: str,
    claimed: str | None = None,
    publication_types: list[str] | None = None,
) -> str:
    """
    Kaynak kademesi URL domain'inden; PubMed/Europe PMC için isteğe bağlı
    publication_types metadata'sı (model beyanı değil).

    `claimed` (modelin JSON'daki source_tier beyanı) yok sayılır.
    [767] nutrola.app'i nutrition_db yazmıştı; bilinmeyen host o yüzden
    claimed'e düşmemeli — other.
    """
    host = _host(url or "")
    if not host:
        return "other"
    if _host_matches(host, ENCYCLOPEDIA_HOSTS):
        return "encyclopedia"
    if _host_matches(host, REVIEW_HOSTS):
        return "systematic_review"
    if _host_matches(host, NUTRITION_DB_HOSTS):
        return "nutrition_db"
    if _host_matches(host, GUIDELINE_HOSTS):
        return "guideline"
    if _is_preprint_url(url):
        return "preprint"
    if _host_matches(host, PRIMARY_HOSTS):
        meta = source_tier_from_publication_types(publication_types)
        if meta:
            return meta
        return "primary_study"
    return "other"


def _as_float(value) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _round_conf(value: float) -> float:
    return round(max(0.0, min(1.0, value)), 2)


def _normalize_doi(value: str) -> str:
    raw = (value or "").strip().lower().rstrip(".")
    raw = raw.replace("https://doi.org/", "").replace("http://doi.org/", "")
    raw = raw.replace("https://dx.doi.org/", "").replace("http://dx.doi.org/", "")
    raw = raw.removeprefix("doi:")
    return raw.rstrip("/")


def _normalize_pmcid(value: str) -> str:
    raw = (value or "").strip().lower()
    if not raw:
        return ""
    if not raw.startswith("pmc"):
        raw = "pmc" + raw
    return raw


def _cite_ids_from_url(url: str) -> set[str]:
    """URL'den karşılaştırma anahtarları: normalize URL, PMID, PMCID, DOI."""
    raw = (url or "").strip()
    if not raw:
        return set()
    u = raw.lower().split("#")[0]
    u_path = u.split("?")[0].rstrip("/")
    u_path = u_path.replace("http://", "https://").replace("://www.", "://")
    ids = {u_path}
    for m in re.finditer(r"pubmed\.ncbi\.nlm\.nih.gov/(\d+)", u):
        ids.add("pmid:" + m.group(1))
    for m in re.finditer(r"europepmc\.org/article/med/(\d+)", u):
        ids.add("pmid:" + m.group(1))
    for m in re.finditer(
        r"(?:pmc\.ncbi\.nlm\.nih\.gov/articles/|ncbi\.nlm\.nih\.gov/pmc/articles/|europepmc\.org/articles/)(pmc\d+)",
        u,
    ):
        ids.add("pmc:" + m.group(1))
    for m in re.finditer(r"europepmc\.org/article/pmc/(\d+)", u):
        ids.add("pmc:pmc" + m.group(1))
    for m in _DOI_RE.finditer(u):
        ids.add("doi:" + _normalize_doi(m.group(1)))
    return {i for i in ids if i}


def _cite_ids_from_evidence_item(item: dict) -> set[str]:
    """Paketteki bir parçanın tüm kimlikleri: url, pmid, pmcid, doi, yayınevi linkleri."""
    ids = _cite_ids_from_url(item.get("url") or "")
    pmid = str(item.get("pmid") or "").strip()
    if pmid:
        ids.add("pmid:" + pmid)
        ids.add(f"https://pubmed.ncbi.nlm.nih.gov/{pmid}")
    pmcid = _normalize_pmcid(str(item.get("pmcid") or ""))
    if pmcid:
        ids.add("pmc:" + pmcid)
        ids.add(f"https://www.ncbi.nlm.nih.gov/pmc/articles/{pmcid}")
        ids.add(f"https://europepmc.org/articles/{pmcid}")
    doi = _normalize_doi(str(item.get("doi") or ""))
    if doi:
        ids.add("doi:" + doi)
        ids.add("https://doi.org/" + doi)
        ids.add("https://dx.doi.org/" + doi)
    extra = item.get("extra_urls") or item.get("publisher_urls") or []
    if isinstance(extra, str):
        extra = [extra]
    for extra_url in extra:
        ids |= _cite_ids_from_url(extra_url)
    return ids


def classify_cite_source(source_url: str, evidence: list[dict] | None) -> str | None:
    """
    Model beyanı değil: source_url paket URL/PMID/PMCID/DOI ile kesişiyor mu.

    None → çağıran paket vermedi (kütüphane/besin yolu); bayrak yazılmaz.
    """
    if evidence is None:
        return None
    if not evidence:
        return "web_search_only"
    package: set[str] = set()
    for item in evidence:
        package |= _cite_ids_from_evidence_item(item)
    cited = _cite_ids_from_url(source_url)
    if cited & package:
        return "retrieval_cited"
    return "web_search_override"


def calibrate_factcheck(
    raw: dict,
    *,
    publication_types: list[str] | None = None,
    evidence: list[dict] | None = None,
) -> dict:
    """
    Ham LLM JSON'unu kalibre et.

    Dönüş, girdi alanlarının üzerine yazar ve ekler:
      source_tier, source_directness, evidence_stance (normalize),
      calibration_flags (virgüllü), calibrated (0/1), needs_human (bool)
    Ham reasoning silinmez.
    """
    verdict = (raw.get("final_verdict") or "").strip() or None
    if verdict not in VALID_VERDICTS:
        verdict = None
    conf = _as_float(raw.get("confidence"))
    url = (raw.get("source_url") or "").strip()
    reasoning = (raw.get("reasoning") or "").strip()
    directness = (raw.get("source_directness") or "").strip() or None
    if directness not in VALID_DIRECTNESS:
        directness = None
    stance = (raw.get("evidence_stance") or "").strip() or None
    if stance not in VALID_STANCE:
        stance = None
    claimed = raw.get("source_tier") if raw.get("source_tier") in VALID_TIERS else None
    # publication_types yalnızca çağıran verir (retrieval metadata). LLM JSON'undan okunmaz.
    tier = infer_source_tier(url, publication_types=publication_types)

    flags: list[str] = []
    changed = False
    orig_verdict = verdict

    if claimed and claimed != tier:
        flags.append(f"tier_url:{claimed}->{tier}")
        changed = True

    if conf is None and verdict is not None:
        conf = 0.5
        flags.append("missing_confidence")
        changed = True

    # Stance, ham etikete bakılır — sonraki kırpmalar (indirect→tartışmalı) kuralı yutmasın.
    if stance == "supports" and orig_verdict == "yanlış":
        # Yön doğru olabilir ama abartı da olabilir — doğrulanmış'a çevirme (#96 leaching)
        verdict = "tartışmalı"
        if conf is None or conf > 0.50:
            conf = 0.50
        changed = True
        flags.append("inverted_verdict")
    elif stance == "contradicts" and orig_verdict == "doğrulanmış":
        verdict = "tartışmalı"
        if conf is None or conf > 0.50:
            conf = 0.50
        changed = True
        flags.append("inverted_verdict")
    elif stance == "insufficient" and orig_verdict in ("yanlış", "doğrulanmış"):
        verdict = "belirsiz"
        if conf is None or conf > 0.35:
            conf = 0.35
        changed = True
        flags.append("insufficient_evidence")
    elif stance == "mixed" and orig_verdict in ("yanlış", "doğrulanmış") and (conf or 0) > 0.60:
        verdict = "tartışmalı"
        conf = min(conf or 0.50, 0.50)
        changed = True
        flags.append("mixed_overconfident")

    # Kaynak iddiayı ele almıyorsa yüksek güvenli doğru/yanlış olamaz (#110 paterni)
    if directness == "unrelated":
        if verdict in ("yanlış", "doğrulanmış", "tartışmalı"):
            verdict = "belirsiz"
            changed = True
            flags.append("unrelated_source")
        if conf is not None and conf > 0.30:
            conf = 0.30
            changed = True
    elif directness == "indirect":
        if verdict in ("yanlış", "doğrulanmış"):
            verdict = "tartışmalı"
            changed = True
            flags.append("indirect_binary_verdict")
        if conf is not None and conf > 0.50:
            conf = 0.50
            changed = True

    # 3) Kaynak kademesi tavanı (Wikipedia genel sayfa + conf=0.85)
    cap = TIER_CONF_CAP.get(tier, 0.65)
    if conf is not None and conf > cap:
        conf = cap
        changed = True
        if tier in TIER_CAP_PROVISIONAL:
            flags.append(f"tier_cap:{tier}:provisional")
        else:
            flags.append(f"tier_cap:{tier}")
    if tier == "encyclopedia" and verdict in ("yanlış", "doğrulanmış"):
        verdict = "tartışmalı"
        changed = True
        flags.append("encyclopedia_binary_verdict")
        if conf is None or conf > cap:
            conf = cap

    # 4) Varsayılan 0.55 kümesi — değeri uydurma, insan incelemesine düşür
    if (
        verdict == "tartışmalı"
        and conf is not None
        and round(conf, 2) in DEFAULT_UNCERTAIN_CONF
    ):
        flags.append("default_conf")

    if conf is not None:
        conf = _round_conf(conf)

    cite = classify_cite_source(url, evidence)
    if cite:
        flags.append(cite)
    if evidence and any(item.get("weak_key_term_match") for item in evidence):
        flags.append("weak_key_term_match")

    needs_human = bool(
        changed
        or "default_conf" in flags
        or "inverted_verdict" in flags
        or tier == "encyclopedia"
        or directness in ("indirect", "unrelated")
        or verdict is None
        or cite == "web_search_override"
    )

    out = dict(raw)
    out["final_verdict"] = verdict
    out["confidence"] = conf
    out["source_url"] = url
    out["reasoning"] = reasoning
    out["source_directness"] = directness
    out["evidence_stance"] = stance
    out["source_tier"] = tier
    out["cite_source"] = cite
    out["calibration_flags"] = ",".join(flags)
    out["calibrated"] = 1 if changed else 0
    out["needs_human"] = needs_human
    return out
