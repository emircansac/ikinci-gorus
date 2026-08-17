"""
measurement_50 752-benzeri iddialarda decomposition + bileşen puanlama.

Kayıtlı pending_batches evidence kullanılır — PubMed/Serper/Claude yok.
Yerel nli_check (assess_evidence_sufficiency) çalışır.

Kullanım:
    python pipeline/15_decompose_offline.py
    python pipeline/15_decompose_offline.py --claim-ids 357,801,813
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent))

from utils.db import get_conn
from utils.evidence_retrieval import component_has_tier_gap, score_component_evidence
from utils.reviewer_summary import decompose_claim_for_retrieval

ROOT = Path(__file__).parent.parent
PENDING = ROOT / "data" / "pending_batches.json"
OUT_MD = ROOT / "data" / "measurement_50" / "decompose_report.md"
OUT_JSON = ROOT / "data" / "measurement_50" / "decompose_offline.json"

DEFAULT_IDS = (357, 801, 813, 901, 956, 978, 1006, 1043, 1129, 1168)


def _latest_jobs(claim_ids: list[int]) -> dict[int, dict]:
    wanted = set(claim_ids)
    latest: dict[int, dict] = {}
    if not PENDING.exists():
        raise SystemExit(f"pending_batches yok: {PENDING}")
    data = json.loads(PENDING.read_text(encoding="utf-8"))
    for rec in data.get("batches") or []:
        for job in rec.get("jobs") or []:
            cid = job.get("claim_id")
            if cid in wanted:
                latest[int(cid)] = job
    return latest


def _search_queries(claim_ids: list[int]) -> dict[int, str]:
    out: dict[int, str] = {}
    conn = get_conn()
    try:
        ph = ",".join("?" * len(claim_ids))
        rows = conn.execute(
            f"SELECT claim_id, search_query_en FROM claims WHERE claim_id IN ({ph})",
            claim_ids,
        ).fetchall()
        for row in rows:
            out[int(row["claim_id"])] = row["search_query_en"] or ""
    finally:
        conn.close()
    return out


def _md_escape(text: str) -> str:
    return (text or "").replace("|", "/").replace("\n", " ")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--claim-ids",
        default=",".join(str(i) for i in DEFAULT_IDS),
    )
    parser.add_argument("--out", default=str(OUT_MD))
    args = parser.parse_args()
    claim_ids = [int(x.strip()) for x in args.claim_ids.split(",") if x.strip()]

    jobs = _latest_jobs(claim_ids)
    queries = _search_queries(claim_ids)
    rows: list[dict] = []

    for cid in claim_ids:
        job = jobs.get(cid)
        if not job:
            rows.append({
                "claim_id": cid,
                "skipped": True,
                "reason": "pending_batches'te evidence yok",
            })
            print(f"[{cid}] pending evidence yok — atlandı")
            continue
        text = job.get("claim_text") or ""
        evidence = job.get("evidence") or []
        query = queries.get(cid) or ""
        parts = decompose_claim_for_retrieval(text)
        cmap = score_component_evidence(text, evidence, query)
        comps = (cmap or {}).get("components") or []
        whole = (cmap or {}).get("whole") or {}
        gap = component_has_tier_gap(comps) if len(comps) >= 2 else False
        row = {
            "claim_id": cid,
            "skipped": False,
            "claim_text": text,
            "n_evidence": len(evidence),
            "parts": parts,
            "split_ok": len(parts) >= 2,
            "whole_tier": whole.get("tier"),
            "job_specificity_tier": job.get("specificity_tier"),
            "components": comps,
            "tier_gap": gap,
            "map": cmap,
        }
        rows.append(row)
        split_s = " | ".join(p[:60] for p in parts)
        tiers = ",".join(c.get("tier") or "?" for c in comps) or "—"
        print(
            f"[{cid}] split={len(parts)} gap={gap} "
            f"whole={whole.get('tier')} comps={tiers} :: {split_s}"
        )

    n = len([r for r in rows if not r.get("skipped")])
    n_split = sum(1 for r in rows if r.get("split_ok"))
    n_gap = sum(1 for r in rows if r.get("tier_gap"))

    payload = {
        "ids": claim_ids,
        "n": n,
        "n_split_ok": n_split,
        "n_tier_gap": n_gap,
        "rows": rows,
    }
    OUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        "# measurement_50 — bileşik iddia decomposition (offline)",
        "",
        "Kayıtlı `pending_batches.json` kanıt paketleri. Yeni PubMed/Serper/Claude yok; "
        "yerel `assess_evidence_sufficiency` (NLI) bileşen bazında yeniden puanlandı.",
        "",
        f"- İşlenen: **{n}/{len(claim_ids)}**",
        f"- Heuristik ≥2 parça: **{n_split}/{n}**",
        f"- Anlamlı tier farkı (biri direct/supportive, diğeri background/none): **{n_gap}/{n}**",
        "",
        "## Özet tablo",
        "",
        "| ID | split | whole | bileşen tier'ları | gap |",
        "|----|-------|-------|-------------------|-----|",
    ]
    for r in rows:
        if r.get("skipped"):
            lines.append(f"| {r['claim_id']} | — | — | atlandı | — |")
            continue
        tiers = ", ".join(
            f"{c.get('tier')}(kept={c.get('kept')})"
            for c in r.get("components") or []
        ) or "—"
        lines.append(
            f"| {r['claim_id']} | {len(r.get('parts') or [])} | "
            f"{r.get('whole_tier') or '—'} | {tiers} | "
            f"{'evet' if r.get('tier_gap') else 'hayır'} |"
        )

    lines += ["", "## Her ID"]
    for r in rows:
        cid = r["claim_id"]
        lines.append(f"\n### #{cid}")
        if r.get("skipped"):
            lines.append(f"- {r.get('reason')}")
            continue
        lines.append(f"- **claim:** {_md_escape(r.get('claim_text') or '')}")
        if not r.get("split_ok"):
            lines.append("- **split:** 1 parça — decomposition katkı yok (heuristik)")
        else:
            for i, part in enumerate(r.get("parts") or [], 1):
                lines.append(f"- **parça {i}:** {_md_escape(part)}")
        whole_t = r.get("whole_tier")
        lines.append(f"- **whole tier:** {whole_t} (job specificity_tier={r.get('job_specificity_tier')})")
        for i, c in enumerate(r.get("components") or [], 1):
            titles = "; ".join(
                (x.get("title") or "")[:80] for x in (c.get("candidates") or [])[:3]
            )
            lines.append(
                f"- **bileşen {i}** tier=`{c.get('tier')}` reason=`{c.get('reason')}` "
                f"kept={c.get('kept')}"
                + (f" — {titles}" if titles else "")
            )
        if r.get("tier_gap"):
            lines.append("- **gap:** evet — decomposition yeni bilgi ekliyor")
        elif r.get("split_ok"):
            lines.append("- **gap:** hayır — bileşenler aynı kademe bandında")

    lines += [
        "",
        "## Canlı deneme",
        "",
        "Bu dosya offline koşumda doldurulur; anlamlı gap olan 2–3 ID "
        "`--recheck-ids` ile ayrıca eklenir.",
        "",
    ]
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"\nözet: split={n_split}/{n} gap={n_gap}/{n} -> {out_path}")


if __name__ == "__main__":
    main()
