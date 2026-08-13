import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from utils.factcheck_calibrate import (
    calibrate_factcheck,
    classify_cite_source,
    infer_source_tier,
    source_tier_from_publication_types,
    TIER_CAP_PROVISIONAL,
    TIER_CONF_CAP,
)


def test_claim_110_wikipedia_high_conf_capped():
    """Chili pepper Wikipedia sayfası potasyum kıyasını doğrudan ele almaz."""
    out = calibrate_factcheck({
        "final_verdict": "yanlış",
        "confidence": 0.85,
        "source_url": "https://en.wikipedia.org/wiki/Chili_pepper",
        "reasoning": "Wikipedia genel biber sayfası.",
        "source_directness": "indirect",
        "evidence_stance": "insufficient",
        "source_tier": "encyclopedia",
    })
    assert out["final_verdict"] in ("tartışmalı", "belirsiz")
    assert out["confidence"] <= 0.45
    assert out["source_tier"] == "encyclopedia"
    assert out["calibrated"] == 1
    assert out["needs_human"] is True


def test_wikipedia_url_only_caps_legacy_rows():
    """Eski kayıtlarda stance/directness boş; URL yine de Wikipedia tavanı uygular."""
    out = calibrate_factcheck({
        "final_verdict": "yanlış",
        "confidence": 0.85,
        "source_url": "https://en.wikipedia.org/wiki/Chili_pepper",
    })
    assert out["final_verdict"] == "tartışmalı"
    assert out["confidence"] <= 0.45
    assert "encyclopedia_binary_verdict" in out["calibration_flags"]



def test_claim_96_source_supports_but_model_says_false():
    """kidney.org leaching tavsiyesini desteklerken 'yanlış' demek tersine verdict."""
    out = calibrate_factcheck({
        "final_verdict": "yanlış",
        "confidence": 0.85,
        "source_url": "https://www.kidney.org/kidney-topics/potassium-your-ckd-diet",
        "reasoning": "Kaynak haşlama suyunun dökülmesini öneriyor.",
        "source_directness": "direct",
        "evidence_stance": "supports",
        "source_tier": "guideline",
    })
    assert out["final_verdict"] == "tartışmalı"
    assert out["confidence"] <= 0.50
    assert "inverted_verdict" in out["calibration_flags"]
    assert out["source_tier"] == "guideline"


def test_claim_108_cochrane_uncertain_untouched():
    """Kontrol: gerçekten belirsiz + düşük güven kalır."""
    out = calibrate_factcheck({
        "final_verdict": "belirsiz",
        "confidence": 0.25,
        "source_url": "https://www.cochranelibrary.com/cdsr/doi/10.1002/14651858.CD008176.pub3/full",
        "reasoning": "Sistematik derleme bu iddiayı doğrudan ölçmemiş.",
        "source_directness": "indirect",
        "evidence_stance": "insufficient",
        "source_tier": "systematic_review",
    })
    assert out["final_verdict"] == "belirsiz"
    assert out["confidence"] == 0.25
    assert out["source_tier"] == "systematic_review"
    # indirect olduğu için insan onayı ister; etiket/güven değişmez
    assert out["calibrated"] == 0


def test_default_conf_cluster_flagged_not_rewritten():
    out = calibrate_factcheck({
        "final_verdict": "tartışmalı",
        "confidence": 0.55,
        "source_url": "https://www.ncbi.nlm.nih.gov/pmc/articles/PMC10780359/",
        "reasoning": "Kanıt karışık.",
        "source_directness": "direct",
        "evidence_stance": "mixed",
        "source_tier": "primary_study",
    })
    assert out["final_verdict"] == "tartışmalı"
    assert out["confidence"] == 0.55
    assert "default_conf" in out["calibration_flags"]
    assert out["needs_human"] is True


def test_pubmed_is_primary_not_guideline():
    assert infer_source_tier("https://pubmed.ncbi.nlm.nih.gov/123") == "primary_study"
    assert infer_source_tier("https://www.ncbi.nlm.nih.gov/pmc/articles/PMC1/") == "primary_study"


def test_pubmed_homepage_is_static_reference():
    assert infer_source_tier("https://pubmed.ncbi.nlm.nih.gov/") == "static_reference"
    assert infer_source_tier("https://pubmed.ncbi.nlm.nih.gov") == "static_reference"


def test_model_cannot_label_blog_as_nutrition_db():
    """[767] nutrola.app — model nutrition_db dedi; URL allowlist other zorlar."""
    out = calibrate_factcheck({
        "final_verdict": "yanlış",
        "confidence": 0.65,
        "source_url": "https://nutrola.app/en/blog/fruit-ranked-by-glycemic-load",
        "source_directness": "direct",
        "evidence_stance": "contradicts",
        "source_tier": "nutrition_db",
    })
    assert out["source_tier"] == "other"
    assert "tier_url:nutrition_db->other" in out["calibration_flags"]
    assert out["confidence"] <= 0.65


def test_model_cannot_upgrade_pubmed_to_systematic_review():
    assert infer_source_tier(
        "https://pubmed.ncbi.nlm.nih.gov/37214237/",
        claimed="systematic_review",
    ) == "primary_study"
    out = calibrate_factcheck({
        "final_verdict": "belirsiz",
        "confidence": 0.20,
        "source_url": "https://pubmed.ncbi.nlm.nih.gov/37214237/",
        "source_tier": "systematic_review",
        "source_directness": "indirect",
        "evidence_stance": "insufficient",
    })
    assert out["source_tier"] == "primary_study"
    assert "tier_url:systematic_review->primary_study" in out["calibration_flags"]


def test_unknown_host_is_other_even_if_model_says_guideline():
    out = calibrate_factcheck({
        "final_verdict": "tartışmalı",
        "confidence": 0.40,
        "source_url": "https://diabetesfoodhub.org/blog/should-people-diabetes-eat-fruit",
        "source_tier": "guideline",
        "source_directness": "indirect",
        "evidence_stance": "mixed",
    })
    assert out["source_tier"] == "other"
    assert "tier_url:guideline->other" in out["calibration_flags"]


def test_ada_diabetes_org_is_guideline_from_url():
    assert infer_source_tier("https://diabetes.org/food-nutrition/reading-food-labels/fruit") == "guideline"


def test_empty_url_is_other_not_model_claim():
    assert infer_source_tier("", claimed="nutrition_db") == "other"


def test_fdc_url_is_nutrition_db_from_host():
    """Canlı FDC URL allowlist'te; usda_cache_static yalnızca nutrition_lookup kod yolu."""
    assert infer_source_tier(
        "https://fdc.nal.usda.gov/fdc-app.html#/food-details/168462/nutrients",
        claimed="usda_cache_static",
    ) == "nutrition_db"


def test_cache_tier_locked_only_outside_calibrate():
    """calibrate LLM çıktısında FDC URL → nutrition_db (model cache diyemez)."""
    out = calibrate_factcheck({
        "final_verdict": "doğrulanmış",
        "confidence": 0.82,
        "source_url": "https://fdc.nal.usda.gov/fdc-app.html#/food-details/168462/nutrients",
        "source_directness": "direct",
        "evidence_stance": "supports",
        "source_tier": "usda_cache_static",
    })
    assert out["source_tier"] == "nutrition_db"
    assert out["confidence"] <= 0.85
    assert "tier_url:usda_cache_static->nutrition_db" in out["calibration_flags"]


def test_unrelated_source_forced_uncertain():
    out = calibrate_factcheck({
        "final_verdict": "yanlış",
        "confidence": 0.9,
        "source_url": "https://example.com/unrelated",
        "source_directness": "unrelated",
        "evidence_stance": "insufficient",
    })
    assert out["final_verdict"] == "belirsiz"
    assert out["confidence"] <= 0.30


def test_mdpi_ajcn_oup_are_primary_study():
    """MADDE 1: bu turda [745]/[752] other'a düşen hakemli domain'ler."""
    assert infer_source_tier("https://www.mdpi.com/2076-3921/10/8/1306") == "primary_study"
    assert infer_source_tier(
        "https://ajcn.nutrition.org/article/S0002-9165(22)03206-3/fulltext"
    ) == "primary_study"
    assert infer_source_tier(
        "https://academic.oup.com/ajcn/article/116/6/1515/6763687"
    ) == "primary_study"


def test_niddk_is_guideline():
    assert infer_source_tier(
        "https://www.niddk.nih.gov/health-information/kidney-disease/chronic-kidney-disease-ckd"
    ) == "guideline"


def test_claim_745_mdpi_url_no_longer_other():
    out = calibrate_factcheck({
        "final_verdict": "tartışmalı",
        "confidence": 0.40,
        "source_url": "https://www.mdpi.com/2076-3921/10/8/1306",
        "source_directness": "direct",
        "evidence_stance": "mixed",
        "source_tier": "systematic_review",
    })
    assert out["source_tier"] == "primary_study"
    assert "tier_url:systematic_review->other" not in out["calibration_flags"]
    assert "tier_url:systematic_review->primary_study" in out["calibration_flags"]


def test_claim_752_ajcn_url_no_longer_other():
    out = calibrate_factcheck({
        "final_verdict": "tartışmalı",
        "confidence": 0.50,
        "source_url": "https://ajcn.nutrition.org/article/S0002-9165(22)03206-3/fulltext",
        "source_directness": "direct",
        "evidence_stance": "mixed",
        "source_tier": "primary_study",
    })
    assert out["source_tier"] == "primary_study"
    assert "tier_url:primary_study->other" not in out["calibration_flags"]


def test_publication_type_metadata_not_model_claim():
    """MADDE 2: XML PublicationType yükseltir; model beyanı hâlâ yok sayılır."""
    url = "https://pubmed.ncbi.nlm.nih.gov/37214237/"
    assert infer_source_tier(url, claimed="systematic_review") == "primary_study"
    assert infer_source_tier(
        url, publication_types=["Meta-Analysis", "Journal Article", "Review"]
    ) == "systematic_review"
    assert infer_source_tier(
        url, publication_types=["Case Reports", "Journal Article"]
    ) == "case_report"
    assert infer_source_tier(
        url, publication_types=["Randomized Controlled Trial", "Journal Article"]
    ) == "primary_study"


def test_source_tier_from_publication_types_priority():
    assert source_tier_from_publication_types(["Systematic Review"]) == "systematic_review"
    assert source_tier_from_publication_types(["Meta-Analysis"]) == "systematic_review"
    assert source_tier_from_publication_types(["Case Reports"]) == "case_report"
    assert source_tier_from_publication_types(["Journal Article"]) == "primary_study"
    assert source_tier_from_publication_types(["Preprint"]) == "preprint"
    assert source_tier_from_publication_types([]) is None


def test_case_report_cap_is_provisional_below_primary():
    assert "case_report" in TIER_CAP_PROVISIONAL
    assert TIER_CONF_CAP["case_report"] < TIER_CONF_CAP["primary_study"]
    out = calibrate_factcheck(
        {
            "final_verdict": "tartışmalı",
            "confidence": 0.90,
            "source_url": "https://pubmed.ncbi.nlm.nih.gov/42583491/",
            "source_directness": "direct",
            "evidence_stance": "mixed",
        },
        publication_types=["Case Reports", "Journal Article"],
    )
    assert out["source_tier"] == "case_report"
    assert out["confidence"] == TIER_CONF_CAP["case_report"]
    assert "tier_cap:case_report:provisional" in out["calibration_flags"]


def test_medlineplus_and_preprint_hosts():
    assert infer_source_tier("https://medlineplus.gov/kidneytests.html") == "guideline"
    assert infer_source_tier("https://www.biorxiv.org/content/10.1101/2024.01.01.123") == "preprint"
    assert infer_source_tier("https://europepmc.org/article/PPR/PPR1287161") == "preprint"
    assert infer_source_tier("https://europepmc.org/article/MED/42191861") == "primary_study"


def test_classify_cite_source_healio_is_override():
    """[652] pakette PubMed varken healio.com → web_search_override, sessiz değil."""
    package = [
        {"url": "https://pubmed.ncbi.nlm.nih.gov/12631359/", "pmid": "12631359"},
        {"url": "https://pubmed.ncbi.nlm.nih.gov/37432140/", "pmid": "37432140"},
    ]
    assert classify_cite_source(
        "https://www.healio.com/news/nephrology/20191002/preparation-method-developed-to-lower-potassium-in-potatoes-for-patients-with-ckd",
        package,
    ) == "web_search_override"
    assert classify_cite_source(
        "https://pubmed.ncbi.nlm.nih.gov/37432140/",
        package,
    ) == "retrieval_cited"
    assert classify_cite_source("https://example.com/x", []) == "web_search_only"
    assert classify_cite_source("https://example.com/x", None) is None


def test_model_cannot_claim_retrieval_cited():
    package = [{"url": "https://pubmed.ncbi.nlm.nih.gov/37432140/", "pmid": "37432140"}]
    out = calibrate_factcheck(
        {
            "final_verdict": "tartışmalı",
            "confidence": 0.4,
            "source_url": "https://www.healio.com/news/foo",
            "source_directness": "direct",
            "evidence_stance": "mixed",
            "cite_source": "retrieval_cited",
        },
        evidence=package,
    )
    assert out["cite_source"] == "web_search_override"
    assert "web_search_override" in out["calibration_flags"]
    assert out["needs_human"] is True


def test_pubmed_url_matches_package_pmid():
    """Europe PMC pubmed URL'si ile aynı PMID retrieval_cited sayılır."""
    package = [{"url": "https://europepmc.org/article/MED/37432140", "pmid": "37432140"}]
    assert classify_cite_source(
        "https://pubmed.ncbi.nlm.nih.gov/37432140/",
        package,
    ) == "retrieval_cited"


def test_doi_pmcid_publisher_url_count_as_retrieval_cited():
    """Aynı makale DOI/PMC/yayınevi URL'siyle alıntılanırsa override değil."""
    package = [{
        "url": "https://pubmed.ncbi.nlm.nih.gov/37432140/",
        "pmid": "37432140",
        "doi": "10.3390/nu15132844",
        "pmcid": "PMC10343521",
        "extra_urls": [
            "https://www.mdpi.com/2072-6643/15/13/2844",
        ],
    }]
    assert classify_cite_source("https://doi.org/10.3390/nu15132844", package) == "retrieval_cited"
    assert classify_cite_source("https://dx.doi.org/10.3390/nu15132844", package) == "retrieval_cited"
    assert classify_cite_source(
        "https://pmc.ncbi.nlm.nih.gov/articles/PMC10343521/",
        package,
    ) == "retrieval_cited"
    assert classify_cite_source(
        "https://www.mdpi.com/2072-6643/15/13/2844",
        package,
    ) == "retrieval_cited"
    assert classify_cite_source(
        "https://www.healio.com/news/nephrology/unrelated",
        package,
    ) == "web_search_override"


def test_weak_key_term_match_flag_is_not_auto_fail():
    package = [{
        "url": "https://pubmed.ncbi.nlm.nih.gov/1/",
        "pmid": "1",
        "weak_key_term_match": 1,
    }]
    out = calibrate_factcheck(
        {
            "final_verdict": "tartışmalı",
            "confidence": 0.4,
            "source_url": "https://pubmed.ncbi.nlm.nih.gov/1/",
            "source_directness": "direct",
            "evidence_stance": "mixed",
        },
        evidence=package,
    )
    assert out["cite_source"] == "retrieval_cited"
    assert "weak_key_term_match" in out["calibration_flags"]
    assert out["needs_human"] is False
