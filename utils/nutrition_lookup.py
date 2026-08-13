"""
USDA FoodData Central + statik önbellek ile besin miktarı iddialarını doğrular.

Canlı FDC çağrısı yalnızca USDA_FDC_API_KEY varken ve cache'te potasyum yokken yapılır.
GI/GL USDA'da yoktur — cache'teki gi/gl değerleri asla "USDA FoodData Central" diye etiketlenmez.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

import requests

ROOT = Path(__file__).parent.parent
CACHE_PATH = ROOT / "data" / "nutrition_cache.json"
FDC_SEARCH = "https://api.nal.usda.gov/fdc/v1/foods/search"

LIVE_SOURCE_TIER = "nutrition_db"
CACHE_SOURCE_TIER = "usda_cache_static"

# USDA temelli referans değerler (100g çiğ, yaklaşık) — canlı sorgu değil, kopya cache
DEFAULT_CACHE = {
    "spinach": {"potassium_mg": 558, "name_tr": "ıspanak", "fdc_url": "https://fdc.nal.usda.gov/fdc-app.html#/food-details/168462/nutrients"},
    "squash": {"potassium_mg": 262, "phosphorus_mg": 38, "name_tr": "kabak", "fdc_url": "https://fdc.nal.usda.gov/fdc-app.html#/food-details/169291/nutrients"},
    "zucchini": {"potassium_mg": 261, "water_pct": 95, "name_tr": "kabak", "fdc_url": "https://fdc.nal.usda.gov/fdc-app.html#/food-details/169291/nutrients"},
    "tomato": {"potassium_mg": 237, "name_tr": "domates", "fdc_url": "https://fdc.nal.usda.gov/fdc-app.html#/food-details/170457/nutrients"},
    "beet": {"potassium_mg": 325, "name_tr": "pancar", "fdc_url": "https://fdc.nal.usda.gov/fdc-app.html#/food-details/169148/nutrients"},
    "cabbage": {"potassium_mg": 170, "phosphorus_mg": 26, "name_tr": "lahana", "fdc_url": "https://fdc.nal.usda.gov/fdc-app.html#/food-details/169975/nutrients"},
    "cucumber": {"potassium_mg": 147, "water_pct": 95, "name_tr": "salatalık", "fdc_url": "https://fdc.nal.usda.gov/fdc-app.html#/food-details/168409/nutrients"},
    "beets": {"potassium_mg": 325, "gi": 64, "gl": 5, "name_tr": "pancar"},
}

FOOD_ALIASES = {
    "ıspanak": "spinach", "ispanak": "spinach", "spinach": "spinach",
    "kabak": "zucchini", "zucchini": "zucchini", "squash": "squash",
    "domates": "tomato", "tomato": "tomato",
    "pancar": "beet", "beet": "beet", "beetroot": "beet",
    "lahana": "cabbage", "cabbage": "cabbage",
    "salatalık": "cucumber", "salatalik": "cucumber", "cucumber": "cucumber",
    "kiraz": "cherry", "guava": "guava", "avokado": "avocado",
}

_NUTRIENT_CLAIM_RE = re.compile(
    r"(\d+)\s*[-–]?\s*(\d+)?\s*mg\s*(potasyum|fosfor|sodyum|kalsiyum|demir|oksalat)?|"
    r"(\d+)\s*mg\s*(potasyum|fosfor|sodyum)|"
    r"glisemik\s*(indeks|yük)\s*[^0-9]*(\d+)|"
    r"(\d+)\s*['']?(?:in|ın)?\s*(?:alt|üst|ust)|"
    r"%\s*(\d+)\s*(?:['']?(?:i|ı)?nin\s*)?(?:üzerinde|ustunde)?\s*su",
    re.IGNORECASE,
)

_LIVE_NUTRIENT_KEYS = ("potassium_mg", "phosphorus_mg")


def _load_cache() -> dict:
    if CACHE_PATH.exists():
        try:
            return {**DEFAULT_CACHE, **json.loads(CACHE_PATH.read_text(encoding="utf-8"))}
        except (json.JSONDecodeError, OSError):
            pass
    return dict(DEFAULT_CACHE)


def is_nutrition_quantity_claim(claim_text: str) -> bool:
    t = (claim_text or "").lower()
    if _NUTRIENT_CLAIM_RE.search(t):
        return True
    return any(k in t for k in ("mg potasyum", "mg fosfor", "glisemik", "gi ", "gl ", "% su"))


def _detect_food_key(text: str) -> str | None:
    t = text.lower()
    for alias, key in FOOD_ALIASES.items():
        if alias in t:
            return key
    return None


def _fdc_search(query: str, api_key: str) -> dict | None:
    try:
        r = requests.get(
            FDC_SEARCH,
            params={"api_key": api_key, "query": query, "pageSize": 1, "dataType": "Foundation,SR Legacy"},
            timeout=12,
        )
        r.raise_for_status()
        foods = r.json().get("foods") or []
        if not foods:
            return None
        food = foods[0]
        nutrients = {n["nutrientName"].lower(): n.get("value") for n in food.get("foodNutrients", [])}
        return {
            "description": food.get("description", query),
            "potassium_mg": nutrients.get("potassium, k"),
            "phosphorus_mg": nutrients.get("phosphorus, p"),
            "fdc_id": food.get("fdcId"),
            "fdc_url": f"https://fdc.nal.usda.gov/fdc-app.html#/food-details/{food.get('fdcId')}/nutrients",
        }
    except (requests.RequestException, KeyError, TypeError):
        return None


def _field_cite(live: bool) -> str:
    if live:
        return "USDA FoodData Central live lookup"
    return "static nutrition cache; not a live USDA FoodData Central lookup"


def lookup_nutrition_evidence(claim_text: str) -> list[dict]:
    """
    Besin miktarı iddiası için kanıt parçası döner (evidence_retrieval formatı).

    source / source_tier:
      nutrition_db         — alıntılanan mg değerleri canlı FDC'den
      usda_cache_static    — cache (GI/GL her zaman; potasyum da API yoksa)
    """
    if not is_nutrition_quantity_claim(claim_text):
        return []

    cache = _load_cache()
    food_key = _detect_food_key(claim_text)
    if not food_key:
        return []

    entry = dict(cache.get(food_key, {}))
    live_keys: set[str] = set()
    api_key = os.environ.get("USDA_FDC_API_KEY", "").strip()
    if api_key and not entry.get("potassium_mg"):
        fdc = _fdc_search(food_key, api_key)
        if fdc:
            for k in _LIVE_NUTRIENT_KEYS:
                if fdc.get(k) is not None:
                    live_keys.add(k)
            entry.update(fdc)

    if not entry:
        return []

    parts = []
    cited_live = False
    cited_cache = False

    def _add(key: str, label: str) -> None:
        nonlocal cited_live, cited_cache
        val = entry.get(key)
        if val is None or val == "":
            return
        live = key in live_keys
        if live:
            cited_live = True
        else:
            cited_cache = True
        parts.append(f"{label} ~{val} ({_field_cite(live)})")

    _add("potassium_mg", "Potassium mg per 100g")
    _add("phosphorus_mg", "Phosphorus mg per 100g")
    _add("water_pct", "Water content %")
    _add("gi", "Glycemic index")
    _add("gl", "Glycemic load")

    if not parts:
        return []

    name = entry.get("name_tr", food_key)
    all_live = cited_live and not cited_cache
    tier = LIVE_SOURCE_TIER if all_live else CACHE_SOURCE_TIER
    abstract = ". ".join(parts) + f" (for {name})."
    title_prefix = "USDA FDC (live)" if all_live else "Static nutrition cache"
    url = entry.get("fdc_url") or ""
    if all_live and not url:
        url = "https://fdc.nal.usda.gov/"
    return [{
        "title": f"{title_prefix}: {entry.get('description', food_key)}",
        "abstract": abstract,
        "pubdate": "",
        "url": url,
        "source": tier,
        "source_tier": tier,
        "live_fdc": all_live,
    }]


def try_nutrition_factcheck(claim_text: str) -> dict | None:
    """
    Sayısal besin iddiasını cache/FDC referansıyla karşılaştırır.
    Dönüş: factcheck sonucu dict veya None (uygulanamaz iddia).
    """
    if not is_nutrition_quantity_claim(claim_text):
        return None

    evidence = lookup_nutrition_evidence(claim_text)
    if not evidence:
        return None

    t = claim_text.lower()
    food_key = _detect_food_key(claim_text)
    cache = _load_cache()
    ref = cache.get(food_key or "", {})
    ev0 = evidence[0]
    tier = ev0.get("source_tier") or CACHE_SOURCE_TIER
    src_label = (
        "USDA FoodData Central (canlı sorgu)"
        if tier == LIVE_SOURCE_TIER
        else "statik cache (canlı FDC çağrısı yok)"
    )

    # Potasyum mg karşılaştırması
    m = re.search(r"(\d+)\s*[-–]\s*(\d+)\s*mg\s*potasyum|(\d+)\s*mg\s*potasyum", t)
    if m and ref.get("potassium_mg"):
        claimed = int(m.group(1) or m.group(3))
        claimed_hi = int(m.group(2)) if m.group(2) else claimed
        ref_k = ref["potassium_mg"]
        mid = (claimed + claimed_hi) / 2
        ratio = mid / ref_k if ref_k else 1
        if 0.75 <= ratio <= 1.35:
            verdict, conf = "doğrulanmış", 0.82
            stance = "supports"
        elif ratio > 1.35:
            verdict, conf = "yanlış", 0.78
            stance = "contradicts"
        else:
            verdict, conf = "tartışmalı", 0.55
            stance = "mixed"
        return {
            "final_verdict": verdict,
            "confidence": conf,
            "source_url": ev0.get("url") or "",
            "reasoning": (
                f"{src_label} potasyum ~{ref_k} mg/100g; iddia {claimed}-{claimed_hi} mg. "
                f"Oran {ratio:.2f}."
            ),
            "source_directness": "direct",
            "evidence_stance": stance,
            "source_tier": tier,
            "calibration_flags": "",
            "needs_human": verdict == "tartışmalı",
        }

    # Su yüzdesi
    m = re.search(r"%\s*(\d+)|(\d+)\s*['']?(?:i|ı)?nin\s*(?:üzerinde|ustunde)\s*su", t)
    if m and ref.get("water_pct"):
        claimed = int(m.group(1) or m.group(2))
        ref_w = ref["water_pct"]
        if abs(claimed - ref_w) <= 5:
            return {
                "final_verdict": "doğrulanmış",
                "confidence": 0.85,
                "source_url": ev0.get("url") or "",
                "reasoning": f"{src_label} su içeriği ~{ref_w}%; iddia ~{claimed}%.",
                "source_directness": "direct",
                "evidence_stance": "supports",
                "source_tier": tier,
                "calibration_flags": "",
                "needs_human": False,
            }

    return {
        "final_verdict": "tartışmalı",
        "confidence": 0.5,
        "source_url": ev0.get("url") or "",
        "reasoning": (
            f"Besin verisi bulundu ({src_label}) ancak iddiadaki sayı otomatik eşleşmedi: "
            f"{ev0['abstract'][:200]}"
        ),
        "source_directness": "indirect",
        "evidence_stance": "mixed",
        "source_tier": tier,
        "calibration_flags": "nutrition_partial_match",
        "needs_human": True,
    }
