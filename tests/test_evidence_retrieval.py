import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from utils.evidence_retrieval import (
    apply_key_term_filter,
    assess_evidence_sufficiency,
    classify_evidence_expectation,
    classify_specificity_tier,
    collect_specificity_nli_scores,
    component_has_tier_gap,
    filter_candidates_by_key_terms,
    key_terms_from_query,
    parse_europepmc_search_json,
    parse_medlineplus_xml,
    parse_pubmed_efetch_xml,
    parse_serper_search_json,
    retrieve_hybrid_evidence,
    score_component_evidence,
    key_terms_from_query,
    parse_europepmc_search_json,
    parse_medlineplus_xml,
    parse_pubmed_efetch_xml,
    parse_serper_search_json,
    retrieve_hybrid_evidence,
    EPISTEMIC_NO_DIRECT,
)
from utils.factcheck_calibrate import infer_source_tier, source_tier_from_publication_types


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
    assert parsed["23182013"]["retrieval_tier"] == "native"
    assert parsed["23182013"]["evidence_content_type"] == "abstract"
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
    assert items[0]["retrieval_tier"] == "native"
    assert items[0]["evidence_content_type"] == "abstract"
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
    assert items[0]["retrieval_tier"] == "native"
    assert items[0]["evidence_content_type"] == "abstract"
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


BLUEBERRY_CLAIM = "Yaban mersini insülin direncini düşürür"
BLUEBERRY_QUERY = "blueberry insulin sensitivity microvascular diabetes"
PUBMED_BLUEBERRY = {
    "title": "Vaccinium as Potential Therapy for Diabetes and Microvascular Complications.",
    "abstract": "Vaccinium berries (blueberry, cranberry) and microvascular diabetic complications.",
    "url": "https://pubmed.ncbi.nlm.nih.gov/37432140/",
    "publication_types": ["Journal Article"],
}


def _nli(label: str, conf: float):
    return {"nli_label": label, "nli_confidence": conf, "raw": {}}


def test_assess_generic_nih_not_sufficient_for_blueberry(monkeypatch):
    """Yüksek kademeli alakasız NIH sayfası tek başına yeterli sayılmaz."""
    nli_calls = []
    monkeypatch.setattr(
        "utils.nli.nli_check",
        lambda *a, **k: nli_calls.append(a) or _nli("SUPPORTS", 0.99),
    )
    nih = {
        "title": "Diabetes",
        "abstract": "Diabetes means your blood glucose levels are too high. Insulin helps glucose.",
        "url": "https://medlineplus.gov/diabetes.html",
    }
    suff = assess_evidence_sufficiency([nih], BLUEBERRY_CLAIM, BLUEBERRY_QUERY)
    assert suff.relevance_ok is False
    assert suff.quality_ok is False
    assert suff.sufficient is False
    assert suff.reason == "weak_key_term_match"
    assert suff.specificity_ok is False
    assert suff.strong_match is False
    assert suff.specificity_tier == "none"
    assert nli_calls == []


def test_assess_relevant_other_not_sufficient(monkeypatch):
    """Alakalı ama düşük kademeli kaynak tek başına yeterli sayılmaz. NLI çağrılmaz."""
    nli_calls = []
    monkeypatch.setattr(
        "utils.nli.nli_check",
        lambda *a, **k: nli_calls.append(a) or _nli("SUPPORTS", 0.99),
    )
    blog = {
        "title": "Blueberry smoothie blog",
        "abstract": "Blueberries and insulin sensitivity anecdotal tips.",
        "url": "https://example.com/blueberry-insulin",
    }
    suff = assess_evidence_sufficiency([blog], BLUEBERRY_CLAIM, BLUEBERRY_QUERY)
    assert infer_source_tier(blog["url"]) == "other"
    assert suff.relevance_ok is True
    assert suff.quality_ok is False
    assert suff.sufficient is False
    assert suff.reason == "low_tier"
    assert suff.specificity_ok is False
    assert suff.strong_match is False
    assert suff.specificity_tier == "background"
    assert nli_calls == []


def test_assess_relevant_pubmed_sufficient(monkeypatch):
    """sufficient tanımı değişmez: NLI NEI olsa bile relevance×tier yeter."""
    monkeypatch.setattr("utils.nli.nli_check", lambda *a, **k: _nli("NOT_ENOUGH_INFO", 0.99))
    suff = assess_evidence_sufficiency([PUBMED_BLUEBERRY], BLUEBERRY_CLAIM, BLUEBERRY_QUERY)
    assert suff.relevance_ok is True
    assert suff.quality_ok is True
    assert suff.sufficient is True
    assert suff.reason == "ok"
    assert suff.best_tier == "primary_study"
    assert suff.specificity_ok is False
    assert suff.strong_match is False
    assert suff.specificity_tier == "background"


def test_assess_empty_is_no_evidence():
    suff = assess_evidence_sufficiency([], BLUEBERRY_CLAIM, BLUEBERRY_QUERY)
    assert suff.reason == "no_evidence"
    assert suff.sufficient is False
    assert suff.relevance_ok is False
    assert suff.quality_ok is False
    assert suff.specificity_ok is False
    assert suff.strong_match is False
    assert suff.specificity_tier == "none"


def test_specificity_supports_high_conf_is_strong_match(monkeypatch):
    seen = []

    def fake_nli(claim, evidence):
        seen.append((claim, evidence))
        return _nli("SUPPORTS", 0.9)

    monkeypatch.setattr("utils.nli.nli_check", fake_nli)
    suff = assess_evidence_sufficiency([PUBMED_BLUEBERRY], BLUEBERRY_CLAIM, BLUEBERRY_QUERY)
    assert suff.sufficient is True
    assert suff.specificity_ok is True
    assert suff.strong_match is True
    assert suff.specificity_tier == "direct"
    assert seen[0][0] == BLUEBERRY_CLAIM
    assert "blueberry" in seen[0][1].lower()


def test_specificity_refutes_high_conf_is_strong_match(monkeypatch):
    monkeypatch.setattr("utils.nli.nli_check", lambda *a, **k: _nli("REFUTES", 0.88))
    suff = assess_evidence_sufficiency([PUBMED_BLUEBERRY], BLUEBERRY_CLAIM, BLUEBERRY_QUERY)
    assert suff.sufficient is True
    assert suff.specificity_ok is True
    assert suff.strong_match is True
    assert suff.specificity_tier == "direct"


def test_specificity_nei_or_low_conf_not_strong_match(monkeypatch):
    monkeypatch.setattr("utils.nli.nli_check", lambda *a, **k: _nli("NOT_ENOUGH_INFO", 0.99))
    suff = assess_evidence_sufficiency([PUBMED_BLUEBERRY], BLUEBERRY_CLAIM, BLUEBERRY_QUERY)
    assert suff.sufficient is True
    assert suff.specificity_ok is False
    assert suff.strong_match is False
    assert suff.specificity_tier == "background"

    monkeypatch.setattr("utils.nli.nli_check", lambda *a, **k: _nli("SUPPORTS", 0.74))
    suff = assess_evidence_sufficiency([PUBMED_BLUEBERRY], BLUEBERRY_CLAIM, BLUEBERRY_QUERY)
    assert suff.sufficient is True
    assert suff.specificity_ok is False
    assert suff.strong_match is False
    assert suff.specificity_tier == "supportive"


def test_specificity_uses_highest_rerank_score(monkeypatch):
    seen = []

    def fake_nli(claim, evidence):
        seen.append(evidence)
        return _nli("SUPPORTS", 0.9)

    monkeypatch.setattr("utils.nli.nli_check", fake_nli)
    low = {
        **PUBMED_BLUEBERRY,
        "abstract": "Low score blueberry insulin abstract.",
        "rerank_score": 0.2,
    }
    high = {
        **PUBMED_BLUEBERRY,
        "pmid": "999",
        "url": "https://pubmed.ncbi.nlm.nih.gov/999/",
        "abstract": "High score blueberry insulin abstract.",
        "rerank_score": 0.91,
    }
    suff = assess_evidence_sufficiency([low, high], BLUEBERRY_CLAIM, BLUEBERRY_QUERY)
    assert suff.strong_match is True
    assert suff.specificity_tier == "direct"
    assert seen == ["High score blueberry insulin abstract."]


def test_score_component_evidence_tier_gap(monkeypatch):
    claim = (
        "Protein ve yağ birlikte tüketildiğinde glisemi daha yavaş yükselir "
        "ve yürüyüş sonrası insülin duyarlılığı artar."
    )
    query = "protein fat glycemic walking insulin"

    def fake_nli(claim_text, _evidence):
        if "glisemi" in (claim_text or ""):
            return _nli("SUPPORTS", 0.9)
        return _nli("NOT_ENOUGH_INFO", 0.4)

    monkeypatch.setattr("utils.nli.nli_check", fake_nli)
    cand = {
        "title": "Protein fat and postprandial glycemia; walking insulin sensitivity",
        "abstract": (
            "Protein and fat slow glycemic rise. Walking after meals improves "
            "insulin sensitivity in adults."
        ),
        "url": "https://pubmed.ncbi.nlm.nih.gov/11111111/",
        "source_tier": "primary_study",
        "publication_types": ["Journal Article"],
        "rerank_score": 0.8,
    }
    out = score_component_evidence(claim, [cand], query)
    assert out
    assert len(out["components"]) == 2
    assert out["components"][0]["tier"] == "direct"
    assert out["components"][1]["tier"] == "background"
    assert component_has_tier_gap(out["components"]) is True


def test_score_component_evidence_skips_non_compound():
    assert score_component_evidence("Çay ve kahve uyku kaçırır.", [], "tea coffee") == {}


def test_classify_specificity_tier_four_levels():
    none_nli = None
    assert classify_specificity_tier("no_evidence", False, False, none_nli) == "none"
    assert classify_specificity_tier("weak_key_term_match", False, False, none_nli) == "none"
    assert classify_specificity_tier(
        "low_tier", True, False, none_nli
    ) == "background"
    assert classify_specificity_tier(
        "ok", True, True, _nli("NOT_ENOUGH_INFO", 0.9)
    ) == "background"
    assert classify_specificity_tier(
        "ok", True, True, _nli("SUPPORTS", 0.42)
    ) == "background"
    assert classify_specificity_tier(
        "ok", True, True, _nli("SUPPORTS", 0.5)
    ) == "supportive"
    assert classify_specificity_tier(
        "ok", True, True, _nli("REFUTES", 0.74)
    ) == "supportive"
    assert classify_specificity_tier(
        "ok", True, True, _nli("SUPPORTS", 0.75)
    ) == "direct"
    assert classify_specificity_tier(
        "ok", True, True, _nli("REFUTES", 0.91)
    ) == "direct"


def test_classify_evidence_expectation_pool():
    claim = "kahve kas kaybı"
    assert classify_evidence_expectation(claim, []) == EPISTEMIC_NO_DIRECT
    assert classify_evidence_expectation(claim, None) == EPISTEMIC_NO_DIRECT
    weak = [_nli("SUPPORTS", 0.42), _nli("NOT_ENOUGH_INFO", 0.36)]
    assert classify_evidence_expectation(claim, weak) == EPISTEMIC_NO_DIRECT
    mixed = [_nli("SUPPORTS", 0.42), _nli("REFUTES", 0.51)]
    assert classify_evidence_expectation(claim, mixed) is None
    strong = [_nli("SUPPORTS", 0.91)]
    assert classify_evidence_expectation(claim, strong) is None


def test_collect_specificity_nli_scores_all_candidates(monkeypatch):
    seen = []

    def fake_nli(claim, evidence):
        seen.append(evidence)
        return _nli("SUPPORTS", 0.4)

    monkeypatch.setattr("utils.nli.nli_check", fake_nli)
    cands = [
        {**PUBMED_BLUEBERRY, "abstract": "first abstract blueberry.", "rerank_score": 0.9},
        {
            **PUBMED_BLUEBERRY,
            "pmid": "2",
            "abstract": "second abstract blueberry.",
            "rerank_score": 0.1,
        },
    ]
    scores = collect_specificity_nli_scores(BLUEBERRY_CLAIM, cands)
    assert len(scores) == 2
    assert seen == ["first abstract blueberry.", "second abstract blueberry."]
    assert classify_evidence_expectation(BLUEBERRY_CLAIM, scores) == EPISTEMIC_NO_DIRECT


def test_parse_serper_organic_maps_snippet_and_tier():
    payload = {
        "organic": [
            {
                "title": "CDC Diabetes",
                "snippet": "Blueberry and blood glucose.",
                "link": "https://www.cdc.gov/diabetes/index.html",
            },
            {
                "title": "Random blog",
                "snippet": "My blueberry recipe",
                "link": "https://myblog.example.com/post",
            },
        ]
    }
    items = parse_serper_search_json(payload)
    assert len(items) == 2
    assert items[0]["retrieval_tier"] == "serper"
    assert items[0]["evidence_content_type"] == "search_snippet"
    assert items[0]["source_tier"] == "guideline"
    assert items[0]["abstract"] == "Blueberry and blood glucose."
    assert items[0]["provider"] == "serper"
    assert items[1]["source_tier"] == "other"


def test_hybrid_skips_serper_when_native_sufficient(monkeypatch):
    monkeypatch.setattr("utils.nli.nli_check", lambda *a, **k: _nli("NOT_ENOUGH_INFO", 0.4))
    called = {"n": 0}

    def fake_serper(q, retmax=10):
        called["n"] += 1
        return []

    native = [{
        "title": "Vaccinium as Potential Therapy for Diabetes and Microvascular Complications.",
        "abstract": "Vaccinium berries (blueberry, cranberry) and microvascular diabetic complications.",
        "url": "https://pubmed.ncbi.nlm.nih.gov/37432140/",
        "publication_types": ["Journal Article"],
        "pmid": "37432140",
        "source_tier": "primary_study",
        "provider": "pubmed",
    }]
    monkeypatch.setattr("utils.nutrition_lookup.is_nutrition_quantity_claim", lambda text: False)
    monkeypatch.setattr("utils.evidence_retrieval.retrieve_serper_evidence", fake_serper)
    monkeypatch.setattr(
        "utils.evidence_retrieval._pubmed_candidates_from_query", lambda *a, **k: native
    )
    monkeypatch.setattr("utils.evidence_retrieval.europepmc_candidates", lambda *a, **k: [])
    monkeypatch.setattr("utils.evidence_retrieval.medlineplus_candidates", lambda *a, **k: [])
    monkeypatch.setattr("utils.evidence_retrieval._attach_rerank_scores", lambda text, c: c)
    monkeypatch.setattr("utils.evidence_retrieval._dense_rerank", lambda text, c, k: c[:k])

    ev, path, _meta = retrieve_hybrid_evidence(BLUEBERRY_CLAIM, BLUEBERRY_QUERY)
    assert called["n"] == 0
    assert "serper" not in path
    assert ev
    assert ev[0]["retrieval_tier"] == "native"
    assert ev[0]["evidence_content_type"] == "abstract"


def test_hybrid_calls_serper_when_native_insufficient(monkeypatch):
    monkeypatch.setattr("utils.nli.nli_check", lambda *a, **k: _nli("NOT_ENOUGH_INFO", 0.4))
    called = {"n": 0}
    serper_item = {
        "title": "Blueberry CDC page",
        "abstract": "Blueberries and insulin sensitivity.",
        "url": "https://www.cdc.gov/diabetes/blueberry.html",
        "source_tier": "guideline",
        "provider": "serper",
        "retrieval_tier": "serper",
        "evidence_content_type": "search_snippet",
    }

    def fake_serper(q, retmax=10):
        called["n"] += 1
        return [serper_item]

    native = [{
        "title": "Diabetes",
        "abstract": "Blood glucose and insulin.",
        "url": "https://medlineplus.gov/diabetes.html",
        "source_tier": "guideline",
        "provider": "medlineplus",
    }]
    monkeypatch.setattr("utils.nutrition_lookup.is_nutrition_quantity_claim", lambda text: False)
    monkeypatch.setattr("utils.evidence_retrieval.retrieve_serper_evidence", fake_serper)
    monkeypatch.setattr(
        "utils.evidence_retrieval._pubmed_candidates_from_query", lambda *a, **k: native
    )
    monkeypatch.setattr("utils.evidence_retrieval.europepmc_candidates", lambda *a, **k: [])
    monkeypatch.setattr("utils.evidence_retrieval.medlineplus_candidates", lambda *a, **k: [])
    monkeypatch.setattr("utils.evidence_retrieval._attach_rerank_scores", lambda text, c: c)
    monkeypatch.setattr("utils.evidence_retrieval._dense_rerank", lambda text, c, k: c[:k])

    ev, path, _meta = retrieve_hybrid_evidence(BLUEBERRY_CLAIM, BLUEBERRY_QUERY)
    assert called["n"] == 1
    assert "serper" in path
    assert any(e.get("retrieval_tier") == "serper" for e in ev)
    assert any(e.get("evidence_content_type") == "search_snippet" for e in ev)
