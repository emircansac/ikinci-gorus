"""static_reference kademesi — sahte/genel URL'ler primary_study alamaz."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from utils.evidence_retrieval import GUIDELINE_SNIPPETS, retrieve_guideline_snippets
from utils.factcheck_calibrate import TIER_CONF_CAP, infer_source_tier


def test_pubmed_homepage_is_static_reference_not_primary():
    assert infer_source_tier("https://pubmed.ncbi.nlm.nih.gov/") == "static_reference"
    assert infer_source_tier("https://pubmed.ncbi.nlm.nih.gov") == "static_reference"
    assert infer_source_tier("https://www.pubmed.ncbi.nlm.nih.gov/") == "static_reference"


def test_pubmed_with_pmid_stays_primary_study():
    assert infer_source_tier("https://pubmed.ncbi.nlm.nih.gov/37432140/") == "primary_study"
    assert infer_source_tier("https://pubmed.ncbi.nlm.nih.gov/123") == "primary_study"


def test_static_reference_conf_cap_matches_other():
    assert TIER_CONF_CAP["static_reference"] == TIER_CONF_CAP["other"]


def test_guideline_snippets_tier_audit_table():
    """GUIDELINE_SNIPPETS: spesifik URL → guideline; genel PubMed → static_reference."""
    expected = [
        ("NKF: Potassium and Your CKD Diet", "https://www.kidney.org/atoz/content/potassium", "guideline"),
        ("NKF: Potassium and Your CKD Diet — leaching", "https://www.kidney.org/atoz/content/potassium", "guideline"),
        ("KDIGO CKD Classification", "https://kdigo.org/guidelines/ckd-evaluation-and-management/", "guideline"),
        ("Clinical pharmacokinetics of dietary nitrate", "https://pubmed.ncbi.nlm.nih.gov/", "static_reference"),
        ("Oxalate reduction by cooking/leaching", "https://pubmed.ncbi.nlm.nih.gov/", "static_reference"),
    ]
    assert len(GUIDELINE_SNIPPETS) == len(expected)
    for snip, (title_prefix, url, tier) in zip(GUIDELINE_SNIPPETS, expected):
        assert snip["title"].startswith(title_prefix.split("—")[0].strip())
        assert snip["url"] == url
        assert snip["source_tier"] == tier
        assert infer_source_tier(url) == tier or (tier == "guideline" and infer_source_tier(url) == "guideline")


def test_retrieve_guideline_snippets_returns_static_reference_for_beetroot():
    snips = retrieve_guideline_snippets(
        "beetroot nitrate juice potassium",
        "mekanizma",
        claim_text="pancar nitrat suyu potasyum",
    )
    assert len(snips) == 1
    assert snips[0]["source_tier"] == "static_reference"
    assert snips[0]["provider"] == "guideline_snippet"
    assert snips[0]["url"] == "https://pubmed.ncbi.nlm.nih.gov/"
    assert snips[0]["source_tier"] not in ("primary_study", "guideline")
