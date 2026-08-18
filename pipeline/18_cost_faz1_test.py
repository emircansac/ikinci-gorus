"""
Faz 1 canlı deneme — before/after $/claim + verdict + web_search_call_count.

Kullanım:
    python pipeline/18_cost_faz1_test.py
    python pipeline/18_cost_faz1_test.py --claim-ids 752,1284,1243
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
DEBUG_LOG = ROOT / "data" / "factcheck_debug.jsonl"
OUT_MD = ROOT / "data" / "ops_reports" / "2026-08-18-cost-faz1-test.md"
DEFAULT_IDS = (752, 1284, 1243, 1247, 1248, 880)

PRICE_SYNC_IN = 2.0 / 1_000_000
PRICE_SYNC_OUT = 10.0 / 1_000_000


def _estimate_sync_cost(usage: dict | None) -> float | None:
    if not usage:
        return None
    inp = int(usage.get("input_tokens") or 0)
    out = int(usage.get("output_tokens") or 0)
    if inp == 0 and out == 0:
        return None
    return inp * PRICE_SYNC_IN + out * PRICE_SYNC_OUT


def _fmt_cost(v: float | None) -> str:
    return f"{v:.4f}" if v is not None else "—"


def _load_latest(claim_ids: list[int]) -> dict[int, dict]:
    wanted = set(claim_ids)
    latest: dict[int, dict] = {}
    if not DEBUG_LOG.is_file():
        return latest
    with DEBUG_LOG.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            cid = rec.get("claim_id")
            if cid in wanted and (rec.get("raw") or {}).get("final_verdict") is not None:
                latest[int(cid)] = rec
    return latest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--claim-ids", default=",".join(str(i) for i in DEFAULT_IDS))
    parser.add_argument("--skip-run", action="store_true", help="yalnızca mevcut logları karşılaştır")
    args = parser.parse_args()
    claim_ids = [int(x.strip()) for x in args.claim_ids.split(",") if x.strip()]

    before = _load_latest(claim_ids)
    print(f"[faz1-test] before snapshot: {len(before)}/{len(claim_ids)} iddia")

    if not args.skip_run:
        cmd = [
            sys.executable,
            str(ROOT / "pipeline" / "03_factcheck.py"),
            "--recheck-ids",
            ",".join(str(i) for i in claim_ids),
        ]
        print(f"[faz1-test] çalıştırılıyor: {' '.join(cmd)}")
        subprocess.run(cmd, cwd=ROOT, check=False)

    after = _load_latest(claim_ids)
    lines = [
        "# Faz 1 canlı test — before/after",
        "",
        f"**İddialar:** {claim_ids}",
        "",
        "| claim_id | before $ | after $ | Δ$ | before verdict | after verdict | before searches | after searches | max_budget |",
        "|---|---:|---:|---:|---|---|---|---:|---:|",
    ]
    for cid in claim_ids:
        b = before.get(cid) or {}
        a = after.get(cid) or {}
        b_cost = _estimate_sync_cost(b.get("usage"))
        a_cost = _estimate_sync_cost(a.get("usage"))
        delta = (a_cost - b_cost) if (a_cost is not None and b_cost is not None) else None
        b_verd = (b.get("calibrated") or b.get("raw") or {}).get("final_verdict")
        a_verd = (a.get("calibrated") or a.get("raw") or {}).get("final_verdict")
        delta_s = f"{delta:+.4f}" if delta is not None else "—"
        lines.append(
            f"| {cid} | {_fmt_cost(b_cost)} | {_fmt_cost(a_cost)} | {delta_s} | "
            f"{b_verd or '—'} | {a_verd or '—'} | "
            f"{b.get('web_search_call_count', '—')} | {a.get('web_search_call_count', '—')} | "
            f"{a.get('max_search_calls', '—')} |"
        )

    OUT_MD.parent.mkdir(parents=True, exist_ok=True)
    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"[faz1-test] rapor: {OUT_MD}")


if __name__ == "__main__":
    main()
