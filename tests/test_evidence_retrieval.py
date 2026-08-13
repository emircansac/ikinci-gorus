import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from utils.evidence_retrieval import (
    apply_key_term_filter,
    filter_candidates_by_key_terms,
    key_terms_from_query,
    parse_europepmc_search_json,
    parse_medlineplus_xml,
    parse_pubmed_efetch_xml,
)
from utils.factcheck_calibrate import source_tier_from_publication_types


PUBMED_XML = """<?xml version="1.0"?>
<PubmedArticleSet>
  <PubmedArticle>
    <MedlineCitation>
      <PMID>37214237</PMID>
      <Article>
        <ArticleTitle>Meta-analysis example title</ArticleTitle>
        <Abstract><AbstractText>Meta-analysis abstract.</AbstractText></Abstract>
        <PublicationTypeList>
          <PublicationType UI="D017418">Meta-Analysis</PublicationType>
          <PublicationType UI="D016428">Journal Article</PublicationType>
          <PublicationType UI="D016454">Review</PublicationType>
        </PublicationTypeList>
      </Article>
    </MedlineCitation>
    <PubmedData>
      <ArticleIdList>
        <ArticleId IdType="pubmed">37214237</ArticleId>
        <ArticleId IdType="doi">10.3390/nu15132844</ArticleId>
        <ArticleId IdType="pmc">PMC10343521</ArticleId>
      </ArticleIdList>
    </PubmedData>
  </PubmedArticle>
  <PubmedArticle>
    <MedlineCitation>
      <PMID>42583491</PMID>
      <Article>
        <ArticleTitle>Case report example title</ArticleTitle>
        <Abstract><AbstractText>Single patient case.</AbstractText></Abstract>
        <PublicationTypeList>
          <PublicationType>Case Reports</PublicationType>
          <PublicationType>Journal Article</PublicationType>
        </PublicationTypeList>
      </Article>
    </MedlineCitation>
  </PubmedArticle>
  <PubmedArticle>
    <MedlineCitation>
      <PMID>23182013</PMID>
      <Article>
        <ArticleTitle>Journal article example title</ArticleTitle>
        <Abstract><AbstractText>Ordinary primary study.</AbstractText></Abstract>
        <PublicationTypeList>
          <PublicationType UI="D016428">Journal Article</PublicationType>
        </PublicationTypeList>
      </Article>
    </MedlineCitation>
  </PubmedArticle>
</PubmedArticleSet>
"""

MEDLINEPLUS_XML = """<?xml version="1.0"?>
<nlmSearchResult>
  <term>GFR kidney</term>
  <count>1</count>
  <list num="1" start="0" per="5">
    <document rank="0" url="https://medlineplus.gov/kidneytests.html">
      <content name="title"><span class="qt1">Kidney</span> Tests</content>
      <content name="FullSummary"><p>GFR tells how well your kidneys are filtering.</p></content>
      <content name="snippet">Glomerular filtration rate (GFR)</content>
    </document>
  </list>
</nlmSearchResult>
"""


def test_parse_pubmed_publication_types_map_to_tiers():
    parsed = parse_pubmed_efetch_xml(PUBMED_XML)
    assert parsed["37214237"]["publication_types"] == [
        "Meta-Analysis", "Journal Article", "Review",
    ]
    assert parsed["37214237"]["source_tier"] == "systematic_review"
    assert parsed["37214237"]["doi"] == "10.3390/nu15132844"
    assert parsed["37214237"]["pmcid"] == "PMC10343521"
    assert "https://doi.org/10.3390/nu15132844" in parsed["37214237"]["extra_urls"]
    assert parsed["42583491"]["publication_types"] == ["Case Reports", "Journal Article"]
    assert parsed["42583491"]["source_tier"] == "case_report"
    assert parsed["23182013"]["publication_types"] == ["Journal Article"]
    assert parsed["23182013"]["source_tier"] == "primary_study"
    assert source_tier_from_publication_types(
        parsed["37214237"]["publication_types"]
    ) == parsed["37214237"]["source_tier"]


def test_parse_europepmc_core_and_preprint():
    payload = {
        "resultList": {
            "result": [
                {
                    "id": "42191861",
                    "source": "MED",
                    "pmid": "42191861",
                    "title": "Blueberry RCT",
                    "abstractText": "Dose-dependent glucose effects.",
                    "pubYear": "2026",
                    "doi": "10.1007/s00394-026-03974-0",
                    "pmcid": "PMC12345678",
                    "fullTextUrlList": {
                        "fullTextUrl": [
                            {
                                "url": "https://link.springer.com/article/10.1007/s00394-026-03974-0",
                            }
                        ]
                    },
                    "pubTypeList": {
                        "pubType": [
                            "research-article",
                            "Randomized Controlled Trial",
                            "Journal Article",
                        ]
                    },
                },
                {
                    "id": "PPR1287161",
                    "source": "PPR",
                    "title": "Finerenone preprint",
                    "abstractText": "Non-diabetic CKD.",
                    "pubYear": "2026",
                    "pubTypeList": {"pubType": ["Preprint"]},
                },
            ]
        }
    }
    items = parse_europepmc_search_json(payload)
    assert items[0]["provider"] == "europepmc"
    assert items[0]["pmid"] == "42191861"
    assert items[0]["pmcid"] == "PMC12345678"
    assert "https://link.springer.com/article/10.1007/s00394-026-03974-0" in items[0]["extra_urls"]
    assert items[0]["source_tier"] == "primary_study"
    assert "Randomized Controlled Trial" in items[0]["publication_types"]
    assert items[1]["source_tier"] == "preprint"
    assert items[1]["url"].startswith("https://europepmc.org/article/PPR/")


def test_medlineplus_query_variants_shorten_academic_query():
    from utils.evidence_retrieval import _medlineplus_query_variants
    variants = _medlineplus_query_variants(
        "normal glomerular filtration rate 120 ml"
    )
    assert variants[0] == "normal glomerular filtration rate 120 ml"
    assert "glomerular filtration" in variants or "glomerular" in variants


def test_parse_medlineplus_health_topics():
    items = parse_medlineplus_xml(MEDLINEPLUS_XML)
    assert len(items) == 1
    assert items[0]["url"] == "https://medlineplus.gov/kidneytests.html"
    assert items[0]["source_tier"] == "guideline"
    assert items[0]["provider"] == "medlineplus"
    assert "GFR" in items[0]["abstract"] or "filtering" in items[0]["abstract"]
    assert "<span" not in items[0]["title"]
    assert items[0]["title"] == "Kidney Tests"


def test_key_terms_blueberry_not_diabetes():
    terms = key_terms_from_query("blueberry insulin sensitivity microvascular diabetes")
    assert "blueberry" in terms
    assert "vaccinium" in terms
    assert "diabetes" not in terms
    assert "insulin" not in terms


def test_filter_drops_generic_diabetes_keeps_vaccinium():
    """[752] cosine genel diyabet sayfasını öne alıyordu; filtre rerank'tan önce eker."""
    diabetes = {
        "title": "Diabetes",
        "abstract": "Diabetes means your blood glucose levels are too high. Insulin helps glucose.",
        "url": "https://medlineplus.gov/diabetes.html",
    }
    gustatory = {
        "title": "Gustatory and Olfactory Perception in Type II Diabetic Patients",
        "abstract": "Taste and smell changes in type 2 diabetes; insulin sensitivity not measured.",
        "url": "https://pubmed.ncbi.nlm.nih.gov/41587888/",
    }
    vaccinium = {
        "title": "Vaccinium as Potential Therapy for Diabetes and Microvascular Complications.",
        "abstract": "Vaccinium berries (blueberry, cranberry) and microvascular diabetic complications.",
        "url": "https://pubmed.ncbi.nlm.nih.gov/37432140/",
    }
    kept, meta = filter_candidates_by_key_terms(
        [diabetes, gustatory, vaccinium],
        "blueberry insulin sensitivity microvascular diabetes",
    )
    assert meta["applied"] is True
    assert meta["dropped"] == 2
    assert kept == [vaccinium]
    urls = {c["url"] for c in kept}
    assert "https://medlineplus.gov/diabetes.html" not in urls


def test_filter_keeps_vaccinium_without_english_blueberry():
    only_latin = {
        "title": "Vaccinium oldhamii fruits improve insulin resistance",
        "abstract": "Fruit extract improved insulin resistance in mice.",
        "url": "https://pubmed.ncbi.nlm.nih.gov/41377596/",
    }
    kept, _ = filter_candidates_by_key_terms(
        [only_latin],
        "blueberry insulin sensitivity microvascular diabetes",
    )
    assert kept == [only_latin]


def test_key_terms_use_claim_text_without_hand_synonym():
    """El sözlüğünde yoksa bile Türkçe iddia varlığını anahtar olarak tut."""
    terms = key_terms_from_query(
        "cinnamon blood glucose diabetes",
        "Tarçın tozu tok kanda şekeri düşürür",
    )
    assert "cinnamon" in terms
    assert "tarçın" in terms
    assert "diabetes" not in terms


def test_filter_keeps_latin_genus_via_stem_prefix():
    """El sözlüğünde yoksa bile 6 harflik kök bilimsel adı tutar (curcumin ↔ Curcuma)."""
    paper = {
        "title": "Curcuma longa extract and fasting glucose",
        "abstract": "Rhizome extract in type 2 diabetes.",
        "url": "https://pubmed.ncbi.nlm.nih.gov/1/",
    }
    generic = {
        "title": "Diabetes",
        "abstract": "Blood glucose and insulin.",
        "url": "https://medlineplus.gov/diabetes.html",
    }
    kept, meta = filter_candidates_by_key_terms(
        [generic, paper],
        "curcumin insulin sensitivity diabetes",
    )
    assert meta["dropped"] == 1
    assert kept == [paper]


def test_filter_zero_hits_falls_back_to_weak_cosine_list():
    """0 key-term eşleşmesi sessiz [] değil; cosine listesine zayıf etiketle döner."""
    generic = {
        "title": "Diabetes",
        "abstract": "Blood glucose and insulin.",
        "url": "https://medlineplus.gov/diabetes.html",
    }
    kept, meta = apply_key_term_filter(
        [generic],
        "blueberry insulin sensitivity microvascular diabetes",
    )
    assert meta["weak_fallback"] is True
    assert len(kept) == 1
    assert kept[0]["url"] == generic["url"]
    assert kept[0]["weak_key_term_match"] == 1
    raw, raw_meta = filter_candidates_by_key_terms(
        [generic],
        "blueberry insulin sensitivity microvascular diabetes",
    )
    assert raw == []
    assert raw_meta["kept"] == 0


def test_merge_enriches_doi_and_publisher_url():
    from utils.evidence_retrieval import _merge_candidates
    pubmed = {
        "url": "https://pubmed.ncbi.nlm.nih.gov/37432140/",
        "pmid": "37432140",
        "title": "Vaccinium",
    }
    epmc = {
        "url": "https://europepmc.org/article/MED/37432140",
        "pmid": "37432140",
        "doi": "10.3390/nu15132844",
        "pmcid": "PMC10343521",
        "extra_urls": ["https://www.mdpi.com/2072-6643/15/13/2844"],
    }
    merged = _merge_candidates([pubmed], [epmc])
    assert len(merged) == 1
    assert merged[0]["doi"] == "10.3390/nu15132844"
    assert merged[0]["pmcid"] == "PMC10343521"
    assert "https://www.mdpi.com/2072-6643/15/13/2844" in merged[0]["extra_urls"]
