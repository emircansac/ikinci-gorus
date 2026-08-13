"""
Embedding tabanlı iddia tekilleştirme (Aşama 2 çıkışı).

Yalnızca embedding cosine yeterli değildir — aynı konudaki farklı iddialar
(ör. genel potasyum uyarısı vs. ıspanak mg değeri) yüksek cosine alabilir.
Bu yüzden birleştirme için hem cosine hem token Jaccard eşiği gerekir.
Şablonlu sayısal iddialarda (GI+GL, porsiyon+mg) ayırt edici sayı farklıysa
cosine/Jaccard ne olursa olsun birleşme yapılmaz (numeric_values_conflict).

İki katmanlı pipeline:
  1. Chunk-local dedup (her parça içinde)
  2. Global window dedup (son N tutulan iddiaya karşı)
  3. Recap duplicate filter (recap parçasında önceki tekrarları sil; vaka muaf)

Eşikler: data/dedup_threshold.json (calibrate_dedup_threshold.py ile üretilir).
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

import numpy as np

from utils.text_similarity import normalize

DATA_DIR = Path(__file__).parent.parent / "data"
THRESHOLD_PATH = DATA_DIR / "dedup_threshold.json"
DEFAULT_THRESHOLD = 0.82
DEFAULT_LEXICAL_THRESHOLD = 0.35
DEFAULT_WINDOW_SIZE = int(os.environ.get("CLAIM_DEDUPE_WINDOW", "12"))

# Konu-tekrarı: aynı anchor kelimeleri paylaşan paraphrase'ler için daha düşük lexical eşik
TOPIC_DEDUP_RULES: list[dict] = [
    {
        "name": "homosistein",
        "keywords": ["homosistein", "homosiste"],
        "cosine_floor": 0.72,
        "cosine_strong": 0.80,
        "lexical_floor": 0.08,
    },
    {
        "name": "potasyum_leaching",
        "keywords": ["potasyum", "%70"],
        "require_all_keywords": True,
        "cosine_floor": 0.75,
        "cosine_strong": 0.82,
        "lexical_floor": 0.12,
    },
    {
        "name": "gfr",
        "keywords": ["gfr", "glomerüler filtrasyon", "glomeruler filtrasyon"],
        "cosine_floor": 0.78,
        "cosine_strong": 0.85,
        "lexical_floor": 0.18,
    },
    {
        "name": "recap_vegetables",
        "keywords": ["ıspanak", "pancar", "pazı", "domates", "kabak", "biber", "lahana", "salatalık"],
        "cosine_floor": 0.78,
        "cosine_strong": 0.82,
        "lexical_floor": 0.20,
        "min_keyword_hits": 2,
    },
    {
        "name": "b12",
        "keywords": ["b12", "vitamin b12", "kobalamin", "lunula"],
        "cosine_floor": 0.75,
        "cosine_strong": 0.82,
        "lexical_floor": 0.10,
    },
    {
        "name": "parkinson",
        "keywords": ["parkinson", "dopamin", "koku"],
        "cosine_floor": 0.78,
        "cosine_strong": 0.84,
        "lexical_floor": 0.12,
    },
]
MODEL_NAME = os.environ.get(
    "CLAIM_DEDUP_MODEL",
    "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
)

_CASE_SUBJECT = re.compile(
    r"(?:\d+\s*yaş(?:ında|ındaki)?|\d+\s*yaşındaki|hasta|vaka|hastam|mehmet|marcos|ines|zeynep|ayşe|fatma)",
    re.IGNORECASE,
)
_CASE_OUTCOME = re.compile(
    r"(?:\d+[.,]\d+|\d+\s*(?:mg|yıl|evre|gfr|kreatinin|potasyum|mmol|seviye|tanı))",
    re.IGNORECASE,
)

_model: Any = None


def _load_threshold_file() -> dict:
    if not THRESHOLD_PATH.is_file():
        return {}
    try:
        return json.loads(THRESHOLD_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, TypeError):
        return {}


def get_threshold() -> float:
    data = _load_threshold_file()
    if "threshold" in data:
        return float(data["threshold"])
    return float(os.environ.get("CLAIM_DEDUPE_THRESHOLD", str(DEFAULT_THRESHOLD)))


def get_lexical_threshold() -> float:
    data = _load_threshold_file()
    if "lexical_threshold" in data:
        return float(data["lexical_threshold"])
    return float(
        os.environ.get("CLAIM_DEDUPE_LEXICAL_THRESHOLD", str(DEFAULT_LEXICAL_THRESHOLD))
    )


def threshold_metadata() -> dict:
    data = _load_threshold_file()
    if data:
        return data
    return {
        "threshold": get_threshold(),
        "lexical_threshold": get_lexical_threshold(),
        "calibrated": False,
        "source": "default",
    }


def token_jaccard(a: str, b: str) -> float:
    sa, sb = set(a.split()), set(b.split())
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


_CLAIM_NUMBER_RE = re.compile(r"\d+(?:[.,]\d+)?")
_NUMERIC_GUARD_MIN_DISTINCT = 2
_NUMERIC_SMALL = 100.0
_NUMERIC_REL_TOL = 0.05
_NUMERIC_SMALL_ABS_TOL = 0.51


def extract_claim_numbers(text: str) -> list[float]:
    """İddia metnindeki sayıları sırayla döndürür (GI 42, GL 4 → [42.0, 4.0])."""
    out: list[float] = []
    for m in _CLAIM_NUMBER_RE.finditer(text or ""):
        raw = m.group().replace(",", ".")
        try:
            out.append(float(raw))
        except ValueError:
            continue
    return out


def _numbers_approx_equal(a: float, b: float) -> bool:
    if max(abs(a), abs(b)) < _NUMERIC_SMALL:
        return abs(a - b) <= _NUMERIC_SMALL_ABS_TOL
    denom = max(abs(a), abs(b), 1e-9)
    return abs(a - b) / denom <= _NUMERIC_REL_TOL


def _unmatched_numbers(src: list[float], pool: list[float]) -> list[float]:
    return [n for n in src if not any(_numbers_approx_equal(n, p) for p in pool)]


def numeric_values_conflict(text_a: str, text_b: str) -> bool:
    """
    Şablonlu sayısal iddialarda ayırt edici sayı farklıysa birleşmeyi engelle.

    İki metinde de en az iki ayrı sayı varsa (GI+GL, porsiyon+mg, doz+süre)
    ve her iki tarafta da karşı tarafta eşi olmayan bir sayı varsa True.
    Cosine/Jaccard'a bakılmaz — şeftali GI=42/GL=4 vs armut GI=38/GL=4.
    """
    a = extract_claim_numbers(text_a)
    b = extract_claim_numbers(text_b)
    uniq_a, uniq_b = list(dict.fromkeys(a)), list(dict.fromkeys(b))
    if len(uniq_a) < _NUMERIC_GUARD_MIN_DISTINCT or len(uniq_b) < _NUMERIC_GUARD_MIN_DISTINCT:
        return False
    return bool(_unmatched_numbers(uniq_a, uniq_b)) and bool(_unmatched_numbers(uniq_b, uniq_a))


def is_case_narrative(text: str) -> bool:
    """Hasta adı/yaşı + ölçülebilir sonuç — recap filtresinden muaf."""
    t = text or ""
    return bool(_CASE_SUBJECT.search(t) and _CASE_OUTCOME.search(t))


def _get_model():
    global _model
    if _model is None:
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as e:
            raise ImportError(
                "sentence-transformers gerekli: pip install sentence-transformers "
                "(model indirmek için huggingface.co erişimi)"
            ) from e
        _model = SentenceTransformer(MODEL_NAME)
    return _model


def embed_texts(texts: list[str]) -> np.ndarray:
    """Metin listesini L2-normalize embedding vektörlerine çevirir."""
    if not texts:
        return np.empty((0, 0))
    model = _get_model()
    return np.asarray(model.encode(texts, normalize_embeddings=True, show_progress_bar=False))


def is_duplicate_pair(
    text_a: str,
    text_b: str,
    emb_a: np.ndarray,
    emb_b: np.ndarray,
    *,
    threshold: float | None = None,
    lexical_threshold: float | None = None,
) -> bool:
    """İki iddia paraphrase/tekrar mı — embedding + lexical birlikte."""
    threshold = get_threshold() if threshold is None else threshold
    lexical_threshold = (
        get_lexical_threshold() if lexical_threshold is None else lexical_threshold
    )
    if numeric_values_conflict(text_a, text_b):
        return False
    cosine = float(np.dot(emb_a, emb_b))
    if cosine < threshold:
        return False
    return token_jaccard(text_a, text_b) >= lexical_threshold


def _dedupe_with_window(
    candidates: list[tuple[dict, str]],
    embeddings: np.ndarray,
    *,
    window: int | None,
    threshold: float,
    lexical_threshold: float,
) -> list[dict]:
    """Sırayı koruyarak tekilleştirir; window=None ise tüm kept listesine bakar."""
    out: list[dict] = []
    kept_embs: list[np.ndarray] = []
    kept_texts: list[str] = []

    for (claim, text), emb in zip(candidates, embeddings):
        compare_range = (
            range(max(0, len(kept_embs) - window), len(kept_embs))
            if window is not None
            else range(len(kept_embs))
        )
        duplicate = False
        for j in compare_range:
            if is_duplicate_pair(
                text,
                kept_texts[j],
                emb,
                kept_embs[j],
                threshold=threshold,
                lexical_threshold=lexical_threshold,
            ):
                duplicate = True
                break
        if duplicate:
            continue
        out.append(claim)
        kept_embs.append(emb)
        kept_texts.append(text)
    return out


def _prepare_candidates(claims: list[dict]) -> list[tuple[dict, str]]:
    candidates: list[tuple[dict, str]] = []
    seen_exact: set[str] = set()
    for c in claims:
        text = normalize(c.get("claim_text") or "")
        if not text or text in seen_exact:
            continue
        seen_exact.add(text)
        candidates.append((c, text))
    return candidates


def dedupe_claims(
    claims: list[dict],
    threshold: float | None = None,
    lexical_threshold: float | None = None,
) -> list[dict]:
    """
    Sırayı koruyarak tekilleştirir: önce tam normalize eşleşme, sonra
    embedding cosine + token Jaccard (ikisi birlikte). Tüm önceki iddialara bakar.
    """
    threshold = get_threshold() if threshold is None else threshold
    lexical_threshold = (
        get_lexical_threshold() if lexical_threshold is None else lexical_threshold
    )
    candidates = _prepare_candidates(claims)
    if len(candidates) <= 1:
        return [c for c, _ in candidates]

    texts = [t for _, t in candidates]
    embeddings = embed_texts(texts)
    return _dedupe_with_window(
        candidates,
        embeddings,
        window=None,
        threshold=threshold,
        lexical_threshold=lexical_threshold,
    )


def _claim_matches_topic(text: str, rule: dict) -> bool:
    t = normalize(text)
    kws = rule["keywords"]
    if rule.get("require_all_keywords"):
        return all(kw in t for kw in kws)
    hits = sum(1 for kw in kws if kw in t)
    min_hits = rule.get("min_keyword_hits", 1)
    return hits >= min_hits


def _is_topic_duplicate(
    text_a: str,
    text_b: str,
    emb_a: np.ndarray,
    emb_b: np.ndarray,
    rule: dict,
) -> bool:
    if numeric_values_conflict(text_a, text_b):
        return False
    cosine = float(np.dot(emb_a, emb_b))
    strong = rule.get("cosine_strong")
    if strong is not None and cosine >= strong:
        return True
    if cosine < rule["cosine_floor"]:
        return False
    return token_jaccard(text_a, text_b) >= rule["lexical_floor"]


def dedupe_topic_repeats(claims: list[dict]) -> list[dict]:
    """Aynı konu anchor'ına sahip paraphrase tekrarlarını birleştirir (düşük lexical eşik)."""
    candidates = _prepare_candidates(claims)
    if len(candidates) <= 1:
        return [c for c, _ in candidates]

    texts = [t for _, t in candidates]
    embeddings = embed_texts(texts)
    drop: set[int] = set()

    for rule in TOPIC_DEDUP_RULES:
        topic_indices = [
            i for i, (_, text) in enumerate(candidates)
            if i not in drop and _claim_matches_topic(text, rule)
        ]
        kept_in_topic: list[int] = []
        for i in topic_indices:
            text_i = texts[i]
            emb_i = embeddings[i]
            duplicate = False
            for j in kept_in_topic:
                if _is_topic_duplicate(text_i, texts[j], emb_i, embeddings[j], rule):
                    duplicate = True
                    break
            if duplicate:
                drop.add(i)
            else:
                kept_in_topic.append(i)

    return [candidates[i][0] for i in range(len(candidates)) if i not in drop]


def dedupe_claims_local(
    claims: list[dict],
    threshold: float | None = None,
    lexical_threshold: float | None = None,
) -> list[dict]:
    """Tek chunk içinde hybrid dedup."""
    return dedupe_claims(claims, threshold=threshold, lexical_threshold=lexical_threshold)


def dedupe_claims_window(
    claims: list[dict],
    window: int = DEFAULT_WINDOW_SIZE,
    threshold: float | None = None,
    lexical_threshold: float | None = None,
) -> list[dict]:
    """Global dedup — yalnızca son `window` tutulan iddiaya karşı karşılaştırır."""
    threshold = get_threshold() if threshold is None else threshold
    lexical_threshold = (
        get_lexical_threshold() if lexical_threshold is None else lexical_threshold
    )
    candidates = _prepare_candidates(claims)
    if len(candidates) <= 1:
        return [c for c, _ in candidates]

    texts = [t for _, t in candidates]
    embeddings = embed_texts(texts)
    return _dedupe_with_window(
        candidates,
        embeddings,
        window=window,
        threshold=threshold,
        lexical_threshold=lexical_threshold,
    )


RECAP_DUPLICATE_COSINE_STRONG = 0.80
RECAP_DUPLICATE_LEXICAL = 0.12


def _is_recap_duplicate(
    text_a: str,
    text_b: str,
    emb_a: np.ndarray,
    emb_b: np.ndarray,
    *,
    threshold: float,
    lexical_threshold: float,
) -> bool:
    if numeric_values_conflict(text_a, text_b):
        return False
    cosine = float(np.dot(emb_a, emb_b))
    if cosine >= RECAP_DUPLICATE_COSINE_STRONG:
        return True
    if cosine < threshold:
        return False
    return token_jaccard(text_a, text_b) >= RECAP_DUPLICATE_LEXICAL


def filter_recap_duplicates(
    recap_claims: list[dict],
    prior_claims: list[dict],
    *,
    threshold: float | None = None,
    lexical_threshold: float | None = None,
) -> tuple[list[dict], list[str]]:
    """
    Recap chunk iddialarından önceki metinlerle duplicate olanları çıkar.
    is_case_narrative olan iddialar muaf kalır.
    Dönüş: (filtered_claims, removed_texts)
    """
    threshold = get_threshold() if threshold is None else threshold
    lexical_threshold = (
        get_lexical_threshold() if lexical_threshold is None else lexical_threshold
    )
    if not recap_claims:
        return [], []

    prior_candidates = _prepare_candidates(prior_claims)
    if not prior_candidates:
        return recap_claims, []

    prior_texts = [t for _, t in prior_candidates]
    prior_embs = embed_texts(prior_texts)

    out: list[dict] = []
    removed: list[str] = []

    recap_candidates = _prepare_candidates(recap_claims)
    recap_texts = [t for _, t in recap_candidates]
    recap_embs = embed_texts(recap_texts) if recap_texts else np.empty((0, 0))

    for (claim, text), emb in zip(recap_candidates, recap_embs):
        if is_case_narrative(claim.get("claim_text") or ""):
            out.append(claim)
            continue
        duplicate = False
        for kept_text, kept_emb in zip(prior_texts, prior_embs):
            if _is_recap_duplicate(
                text,
                kept_text,
                emb,
                kept_emb,
                threshold=threshold,
                lexical_threshold=lexical_threshold,
            ):
                duplicate = True
                break
        if duplicate:
            removed.append(claim.get("claim_text") or text)
        else:
            out.append(claim)

    return out, removed


def dedupe_pipeline(
    chunk_lists: list[dict],
    *,
    window: int = DEFAULT_WINDOW_SIZE,
    threshold: float | None = None,
    lexical_threshold: float | None = None,
) -> list[dict]:
    """
    İki katmanlı dedup: chunk-local → birleştir → window → recap filter.

    chunk_lists: [{"chunk_index": int, "is_recap": bool, "claims": [...]}, ...]
    """
    threshold = get_threshold() if threshold is None else threshold
    lexical_threshold = (
        get_lexical_threshold() if lexical_threshold is None else lexical_threshold
    )

    local_deduped: list[dict] = []
    for chunk in chunk_lists:
        raw = chunk.get("claims") or []
        local = dedupe_claims_local(raw, threshold=threshold, lexical_threshold=lexical_threshold)
        local = dedupe_topic_repeats(local)
        chunk["claims_local"] = local
        local_deduped.extend(local)

    windowed = dedupe_claims_window(
        local_deduped,
        window=window,
        threshold=threshold,
        lexical_threshold=lexical_threshold,
    )
    topic_filtered = dedupe_topic_repeats(windowed)

    # Recap post-filter: son recap chunk'taki iddiaları prior ile karşılaştır
    recap_chunks = [c for c in chunk_lists if c.get("is_recap")]
    if not recap_chunks:
        return topic_filtered

    recap_chunk = recap_chunks[-1]
    recap_local = recap_chunk.get("claims_local") or []
    if not recap_local:
        return topic_filtered

    recap_texts = {normalize(c.get("claim_text") or "") for c in recap_local}
    non_recap_claims: list[dict] = []
    for chunk in chunk_lists:
        if chunk.get("is_recap"):
            continue
        non_recap_claims.extend(chunk.get("claims_local") or [])
    prior_windowed = dedupe_topic_repeats(
        dedupe_claims_window(
            non_recap_claims,
            window=window,
            threshold=threshold,
            lexical_threshold=lexical_threshold,
        )
    )

    filtered_recap, _removed = filter_recap_duplicates(
        recap_local,
        prior_windowed,
        threshold=threshold,
        lexical_threshold=lexical_threshold,
    )
    recap_chunk["claims_recap_filtered"] = filtered_recap

    without_recap = [
        c for c in topic_filtered
        if normalize(c.get("claim_text") or "") not in recap_texts
    ]
    return without_recap + filtered_recap
