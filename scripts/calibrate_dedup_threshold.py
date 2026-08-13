#!/usr/bin/env python3
"""
Embedding + lexical dedup eşiklerini kalibre eder; sonucu data/dedup_threshold.json'a yazar.

Birleştirme kuralı: cosine >= threshold VE token_jaccard >= lexical_threshold

Gereksinim: huggingface.co erişimi (model ilk indirmede indirilir).

    cd health_misinfo_monitor\\ \\(1\\)
    ./venv/bin/python scripts/calibrate_dedup_threshold.py
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from utils.claim_dedup import (
    MODEL_NAME,
    embed_texts,
    token_jaccard,
)
from utils.text_similarity import normalize

PAIRS_PATH = Path(__file__).parent.parent / "data" / "dedup_calibration_pairs.json"
OUT_PATH = Path(__file__).parent.parent / "data" / "dedup_threshold.json"


def load_pairs() -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    data = json.loads(PAIRS_PATH.read_text(encoding="utf-8"))
    pos = [tuple(p) for p in data["positive_pairs"]]
    neg = [tuple(p) for p in data["negative_pairs"]]
    return pos, neg


def pair_features(pairs: list[tuple[str, str]]) -> list[dict]:
    rows = []
    for a, b in pairs:
        na, nb = normalize(a), normalize(b)
        embs = embed_texts([na, nb])
        rows.append(
            {
                "cosine": float(embs[0] @ embs[1]),
                "jaccard": token_jaccard(na, nb),
            }
        )
    return rows


def evaluate(
    pos: list[dict],
    neg: list[dict],
    threshold: float,
    lexical_threshold: float,
) -> tuple[float, float]:
    tp = sum(
        1
        for f in pos
        if f["cosine"] >= threshold and f["jaccard"] >= lexical_threshold
    )
    fp = sum(
        1
        for f in neg
        if f["cosine"] >= threshold and f["jaccard"] >= lexical_threshold
    )
    tpr = tp / len(pos) if pos else 0.0
    fpr = fp / len(neg) if neg else 0.0
    return tpr, fpr


def main():
    pos_raw, neg_raw = load_pairs()
    print(f"[calibrate] model: {MODEL_NAME}")
    print(f"[calibrate] positive pairs: {len(pos_raw)}, negative pairs: {len(neg_raw)}")

    pos = pair_features(pos_raw)
    neg = pair_features(neg_raw)

    pos_cos = [f["cosine"] for f in pos]
    neg_cos = [f["cosine"] for f in neg]
    pos_jac = [f["jaccard"] for f in pos]
    neg_jac = [f["jaccard"] for f in neg]

    print(
        f"[calibrate] positive cosine min={min(pos_cos):.3f} max={max(pos_cos):.3f} "
        f"jaccard min={min(pos_jac):.3f} max={max(pos_jac):.3f}"
    )
    print(
        f"[calibrate] negative cosine min={min(neg_cos):.3f} max={max(neg_cos):.3f} "
        f"jaccard min={min(neg_jac):.3f} max={max(neg_jac):.3f}"
    )

    margin = 0.02
    max_neg_jac = max(neg_jac)
    # Lexical: negatif çiftlerin en yüksek kelime örtüşmesinin hemen üstü
    lexical_threshold = round(max(max_neg_jac + 0.01, 0.35), 4)

    pos_cos_for_lex = [f["cosine"] for f in pos if f["jaccard"] >= lexical_threshold]
    max_neg_cos_at_lex = max(
        (f["cosine"] for f in neg if f["jaccard"] >= lexical_threshold),
        default=0.0,
    )

    if pos_cos_for_lex:
        candidate = round(min(pos_cos_for_lex) - margin, 4)
        embed_threshold = max(candidate, 0.75)
        while embed_threshold <= max_neg_cos_at_lex and embed_threshold < 0.99:
            embed_threshold = round(embed_threshold + 0.005, 4)
    else:
        embed_threshold = 0.82

    tpr, fpr = evaluate(pos, neg, embed_threshold, lexical_threshold)
    if fpr > 0:
        best_score = -1.0
        best_t = embed_threshold
        for step in range(75, 99):
            t = step / 100.0
            tpr_g, fpr_g = evaluate(pos, neg, t, lexical_threshold)
            if fpr_g > 0:
                continue
            score = tpr_g
            if score > best_score or (score == best_score and t > best_t):
                best_score = score
                best_t = round(t, 4)
        embed_threshold = best_t
        tpr, fpr = evaluate(pos, neg, embed_threshold, lexical_threshold)

    payload = {
        "threshold": embed_threshold,
        "lexical_threshold": lexical_threshold,
        "calibrated": True,
        "strategy": "cosine_and_token_jaccard",
        "model": MODEL_NAME,
        "calibrated_at": datetime.now(timezone.utc).isoformat(),
        "pairs_file": str(PAIRS_PATH.name),
        "metrics": {
            "positive_pairs": len(pos),
            "negative_pairs": len(neg),
            "true_positive_rate": round(tpr, 4),
            "false_positive_rate": round(fpr, 4),
            "positive_cosine": [round(x, 4) for x in pos_cos],
            "negative_cosine": [round(x, 4) for x in neg_cos],
            "positive_jaccard": [round(x, 4) for x in pos_jac],
            "negative_jaccard": [round(x, 4) for x in neg_jac],
        },
    }
    OUT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        f"[calibrate] seçilen eşikler: cosine={embed_threshold:.4f} "
        f"lexical={lexical_threshold:.4f} → {OUT_PATH}"
    )
    print(
        f"[calibrate] TPR={payload['metrics']['true_positive_rate']:.2%} "
        f"FPR={payload['metrics']['false_positive_rate']:.2%}"
    )


if __name__ == "__main__":
    main()
