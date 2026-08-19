"""Geriye dönük shadow relevance — eşik/gate yok, yalnızca skor."""
from __future__ import annotations

import importlib.util
import json
import os
import sys
from collections import Counter
from pathlib import Path

os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from utils.evidence_retrieval import shadow_relevance_debug_fields

GOLDEN = (865, 905, 961, 1282)
REF_865 = 0.2672559916973114
MISSING = "missing/not_available"


def _load_nli19():
    spec = importlib.util.spec_from_file_location(
        "nli19", ROOT / "pipeline" / "19_nli_phase2.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _percentile(xs: list[float], p: float) -> float | None:
    if not xs:
        return None
    s = sorted(xs)
    if len(s) == 1:
        return float(s[0])
    k = (len(s) - 1) * p
    lo = int(k)
    hi = min(lo + 1, len(s) - 1)
    if lo == hi:
        return float(s[lo])
    frac = k - lo
    return float(s[lo] + (s[hi] - s[lo]) * frac)


def _fmt(x: float | None, digits: int = 3) -> str:
    if x is None:
        return "—"
    return f"{x:.{digits}f}"


def main() -> None:
    nli19 = _load_nli19()
    ids = nli19.load_554_ids()
    print(f"[relevance-shadow] 554 ID yüklendi, DB+paket…", flush=True)
    rows = nli19.load_rows(ids)
    eligible = [r for r in rows if r.get("escalated") == 1]
    jobs = nli19._load_latest_jobs({r["claim_id"] for r in eligible})
    print(f"[relevance-shadow] eligible escalated={len(eligible)}", flush=True)

    scores: list[float] = []
    basis_counts: Counter[str] = Counter()
    per_claim: dict[int, dict] = {}
    n_missing = 0

    for i, r in enumerate(eligible, 1):
        cid = int(r["claim_id"])
        job = jobs.get(cid)
        evidence = list((job or {}).get("evidence") or [])
        src = r.get("source_url")
        if src == MISSING:
            src = None
        fields = shadow_relevance_debug_fields(r.get("claim_text") or "", src, evidence)
        score = fields.get("relevance_score")
        basis = fields.get("relevance_basis") or MISSING
        basis_counts[basis] += 1
        if score is None:
            n_missing += 1
        else:
            scores.append(float(score))
        per_claim[cid] = {
            "relevance_score": score,
            "relevance_basis": basis,
            "relevance_evidence_title": fields.get("relevance_evidence_title"),
            "nli_label": r.get("nli_label"),
            "nli_confidence": r.get("nli_confidence"),
            "final_verdict": r.get("final_verdict"),
        }
        if i % 100 == 0:
            print(f"[relevance-shadow] {i}/{len(eligible)}", flush=True)

    p25 = _percentile(scores, 0.25)
    p50 = _percentile(scores, 0.50)
    p75 = _percentile(scores, 0.75)
    mean = (sum(scores) / len(scores)) if scores else None

    golden_rows = []
    for cid in GOLDEN:
        rec = per_claim.get(cid) or {}
        golden_rows.append({"claim_id": cid, **rec})

    s865 = (per_claim.get(865) or {}).get("relevance_score")
    match_865 = (
        s865 is not None and abs(float(s865) - REF_865) < 5e-4
    )

    payload = {
        "eligible_n": len(eligible),
        "n_scored": len(scores),
        "n_missing": n_missing,
        "basis": dict(basis_counts),
        "p25": p25,
        "p50": p50,
        "p75": p75,
        "mean": mean,
        "min": min(scores) if scores else None,
        "max": max(scores) if scores else None,
        "golden": golden_rows,
        "ref_865": REF_865,
        "match_865": match_865,
        "per_claim": {str(k): v for k, v in per_claim.items()},
    }

    out_json = Path(__file__).with_name("relevance_shadow.json")
    out_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        "# Shadow relevance — geriye dönük skor (eşik yok)",
        "",
        "Kod: `compute_evidence_relevance` + Ölçüm 3 cited/proxy seçimi. "
        "Gate kurulmadı. should_escalate / needs_human / calibration_flags değişmedi.",
        "",
        f"- Kohort: 554 Dilim 1–5, eligible = escalated=1 → **n={len(eligible)}**",
        f"- Skor hesaplanan: **{len(scores)}**",
        f"- Hesaplanamayan: **{n_missing}**",
        f"- basis: cited_package_item={basis_counts.get('cited_package_item', 0)}, "
        f"proxy={basis_counts.get('proxy_relevance_exact_cited_not_tracked', 0)}, "
        f"missing={basis_counts.get(MISSING, 0)}",
        "",
        "## Dağılım",
        "",
        "| | değer |",
        "|---|---:|",
        f"| n | {len(scores)} |",
        f"| p25 | {_fmt(p25)} |",
        f"| p50 | {_fmt(p50)} |",
        f"| p75 | {_fmt(p75)} |",
        f"| min | {_fmt(payload['min'])} |",
        f"| max | {_fmt(payload['max'])} |",
        "",
        "Eşik önerisi yok — kanal geneline geçilene kadar veri toplama.",
        "",
        "## Golden case'ler",
        "",
        "| id | NLI | conf | Claude | basis | relevance | kanıt |",
        "|---|---|---|---|---|---:|---|",
    ]
    for rec in golden_rows:
        cid = rec["claim_id"]
        score = rec.get("relevance_score")
        lines.append(
            f"| #{cid} | {rec.get('nli_label')} | {rec.get('nli_confidence')} | "
            f"{rec.get('final_verdict')} | {rec.get('relevance_basis')} | "
            f"{_fmt(score) if score is not None else '—'} | "
            f"{rec.get('relevance_evidence_title') or '—'} |"
        )
    extra = ""
    if s865 is not None:
        extra = f" (hesaplanan {s865:.6f}, ref {REF_865:.6f})"
    lines += [
        "",
        f"**#865 referans 0.267:** {'eşleşti' if match_865 else 'EŞLEŞMEDİ'}{extra}",
        "",
        "Kaynak: DB `claims`+`verdicts`, `data/pending_batches.json` paketleri.",
    ]
    out_md = Path(__file__).with_name("relevance_shadow.md")
    out_md.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines), flush=True)
    print(f"\n[relevance-shadow] yazıldı: {out_md}", flush=True)


if __name__ == "__main__":
    main()
