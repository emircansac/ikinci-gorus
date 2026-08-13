import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from utils.nutrition_lookup import (
    is_nutrition_quantity_claim,
    try_nutrition_factcheck,
    lookup_nutrition_evidence,
)


def test_spinach_potassium_claim_detected():
    text = "Çiğ ıspanak 100 gramda yaklaşık 550 mg potasyum içerir"
    assert is_nutrition_quantity_claim(text)


def test_spinach_potassium_verdict():
    text = "Çiğ ıspanak 100 gramda yaklaşık 550 mg potasyum içerir"
    result = try_nutrition_factcheck(text)
    assert result is not None
    assert result["final_verdict"] == "doğrulanmış"
    assert result["source_tier"] == "usda_cache_static"
    assert "canlı FDC" in result["reasoning"] or "statik cache" in result["reasoning"]
    assert "USDA FoodData Central (canlı" not in result["reasoning"]


def test_non_nutrition_claim_returns_none():
    text = "GFR böbrek fonksiyonunun temel ölçütüdür"
    assert try_nutrition_factcheck(text) is None


def test_lookup_evidence_has_usda_url():
    text = "Salatalık %95 su içerir"
    ev = lookup_nutrition_evidence(text)
    assert ev
    assert ev[0]["source_tier"] == "usda_cache_static"
    assert "static nutrition cache" in ev[0]["abstract"]
    assert "USDA FoodData Central live lookup" not in ev[0]["abstract"]
    assert "USDA FoodData Central reference" not in ev[0]["abstract"]


def test_cherry_gi_not_attributed_to_usda():
    text = "Taze kirazın glisemik indeksi 27'dir ve glisemik yükü 6'dır"
    ev = lookup_nutrition_evidence(text)
    assert ev
    assert ev[0]["source_tier"] == "usda_cache_static"
    assert "Glycemic index" in ev[0]["abstract"]
    assert "USDA FoodData Central live lookup" not in ev[0]["abstract"]
    assert "USDA FoodData Central reference" not in ev[0]["title"]
    assert not ev[0]["title"].startswith("USDA FDC (live)")
    fc = try_nutrition_factcheck(text)
    assert fc is not None
    assert fc["source_tier"] == "usda_cache_static"
    assert "USDA FoodData Central (canlı" not in fc["reasoning"]
