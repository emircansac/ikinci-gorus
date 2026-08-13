"""
Gölge test: ucuz NLI filtresinin ilk tahmini vs Claude (escalated) ground truth.

API çağrısı yok — yerel NLI modeli + DB'deki mevcut verdict/snippet kullanılır.

Kullanım:
    ./venv/bin/python pipeline/11_nli_shadow_test.py
    ./venv/bin/python pipeline/11_nli_shadow_test.py --skip-rerun
    ./venv/bin/python pipeline/11_nli_shadow_test.py --video-ids P4m9F9mykQ8,odZgEDFDmbE
    ./venv/bin/python pipeline/11_nli_shadow_test.py --claim-ids 671,690
    ./venv/bin/python pipeline/11_nli_shadow_test.py --reanalyze data/nli_shadow_test.json
    ./venv/bin/python pipeline/11_nli_shadow_test.py --audit-partial-rule data/nli_shadow_test.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent))
from utils.db import get_conn
from utils.evidence_retrieval import retrieve_pubmed_evidence, FINAL_EVIDENCE_COUNT
from utils.nli import nli_check, should_escalate
from utils.reasoning_patterns import evidence_has_partial_caveat

DEFAULT_VIDEO_IDS = ("P4m9F9mykQ8", "odZgEDFDmbE", "bZsorXWeLhM")
OUT_PATH = Path(__file__).parent.parent / "data" / "nli_shadow_test.json"

NLI_TO_VERDICT = {
    "SUPPORTS": "doğrulanmış",
    "REFUTES": "yanlış",
    "NOT_ENOUGH_INFO": "belirsiz",
}
BINARY_VERDICTS = frozenset({"doğrulanmış", "yanlış"})
CLAUDE_TARTISMALI = "tartışmalı"

# NLI 3 etiket → 3 Claude sınıfı; tartışmalı için NLI karşılığı yok.
METRIC_NOTES = (
    "exact_match_rate: nli_mapped == claude_verdict, tüm kohort paydası; "
    "tartışmalı Claude verdict exact match alamaz (yapısal tavan). "
    "binary_agreement_rate: her iki taraf doğrulanmış/yanlış VE eşit — payda yine tüm kohort; "
    "tartışmalı iddialar binary_agree=False sayılır. "
    "binary_agree_given_both_binary: yalnızca karşılaştırılabilir binary çiftler."
)


def _coarse3(verdict: str) -> str:
    if verdict == "doğrulanmış":
        return "support"
    if verdict == "yanlış":
        return "refute"
    return "uncertain"


def _load_cohort(conn, *, video_ids: tuple[str, ...], claim_ids: list[int] | None) -> list[dict]:
    if claim_ids:
        placeholders = ",".join("?" * len(claim_ids))
        rows = conn.execute(f"""
            SELECT c.claim_id, c.claim_text, c.search_query_en, c.category, c.initial_risk,
                   c.video_id,
                   vr.nli_label AS stored_nli, vr.nli_confidence AS stored_conf,
                   vr.nli_evidence_snippet, vr.final_verdict, vr.confidence AS claude_conf,
                   vr.escalated
            FROM claims c
            JOIN verdicts vr ON vr.claim_id = c.claim_id
            WHERE c.claim_id IN ({placeholders})
              AND vr.escalated = 1 AND vr.final_verdict IS NOT NULL
            ORDER BY c.claim_id
        """, claim_ids).fetchall()
    else:
        placeholders = ",".join("?" * len(video_ids))
        rows = conn.execute(f"""
            SELECT c.claim_id, c.claim_text, c.search_query_en, c.category, c.initial_risk,
                   c.video_id,
                   vr.nli_label AS stored_nli, vr.nli_confidence AS stored_conf,
                   vr.nli_evidence_snippet, vr.final_verdict, vr.confidence AS claude_conf,
                   vr.escalated
            FROM claims c
            JOIN verdicts vr ON vr.claim_id = c.claim_id
            WHERE c.video_id IN ({placeholders})
              AND vr.escalated = 1 AND vr.final_verdict IS NOT NULL
            ORDER BY c.claim_id
        """, video_ids).fetchall()
    return [dict(r) for r in rows]


def _evidence_text(row: dict) -> tuple[str, str]:
    """(evidence_text, source) — source: snippet | retrieved | empty"""
    snippet = (row.get("nli_evidence_snippet") or "").strip()
    if len(snippet) > 20 and "eşleşmesi" not in snippet.lower():
        return snippet[:1800], "snippet"

    evidence = retrieve_pubmed_evidence(
        row["claim_text"],
        search_query_en=row.get("search_query_en"),
        category=row.get("category"),
    )
    if evidence:
        nli_slice = evidence[:FINAL_EVIDENCE_COUNT]
        text = " ".join(
            f"{e['title']} {e.get('abstract', '')}".strip() for e in nli_slice
        )
        return text[:1800], "retrieved"
    return "", "empty"


def evaluate_row(row: dict, *, skip_rerun: bool) -> dict:
    claim_id = row["claim_id"]
    claude_verdict = row["final_verdict"]
    evidence_text = ""

    if skip_rerun and row.get("stored_nli"):
        nli_label = row["stored_nli"]
        nli_conf = float(row["stored_conf"] or 0)
        evidence_source = "stored_only"
        evidence_text = (row.get("nli_evidence_snippet") or "").strip()
    else:
        evidence_text, evidence_source = _evidence_text(row)
        if not evidence_text:
            return {
                "claim_id": claim_id,
                "video_id": row["video_id"],
                "claim_text": row["claim_text"][:120],
                "skipped": True,
                "skip_reason": "no_evidence",
                "claude_verdict": claude_verdict,
                "stored_nli": row.get("stored_nli"),
            }
        nli_result = nli_check(row["claim_text"], evidence_text)
        nli_label = nli_result["nli_label"]
        nli_conf = nli_result["nli_confidence"]

    nli_mapped = NLI_TO_VERDICT.get(nli_label, "belirsiz")
    nli_result_dict = {"nli_label": nli_label, "nli_confidence": nli_conf}
    risk = row.get("initial_risk") or "medium"
    would_escalate = should_escalate(nli_result_dict, risk, evidence_text=evidence_text)
    partial_caveat = evidence_has_partial_caveat(evidence_text)

    exact_match = nli_mapped == claude_verdict
    binary_agree = (
        nli_mapped in BINARY_VERDICTS
        and claude_verdict in BINARY_VERDICTS
        and nli_mapped == claude_verdict
    )
    skip_regret = (
        not would_escalate
        and nli_mapped in BINARY_VERDICTS
        and claude_verdict in BINARY_VERDICTS
        and nli_mapped != claude_verdict
    )

    stored_changed = None
    if row.get("stored_nli") and not skip_rerun:
        stored_mapped = NLI_TO_VERDICT.get(row["stored_nli"], "belirsiz")
        stored_changed = stored_mapped != nli_mapped or row["stored_nli"] != nli_label

    return {
        "claim_id": claim_id,
        "video_id": row["video_id"],
        "category": row.get("category"),
        "initial_risk": row.get("initial_risk"),
        "claim_text": row["claim_text"][:120],
        "skipped": False,
        "evidence_source": evidence_source if not skip_rerun else "stored_only",
        "stored_nli": row.get("stored_nli"),
        "stored_conf": row.get("stored_conf"),
        "rerun_nli": nli_label,
        "rerun_conf": nli_conf,
        "nli_mapped": nli_mapped,
        "claude_verdict": claude_verdict,
        "claude_conf": row.get("claude_conf"),
        "exact_match": exact_match,
        "binary_agree": binary_agree,
        "would_escalate": would_escalate,
        "partial_evidence_caveat": partial_caveat,
        "skip_regret": skip_regret,
        "stored_vs_rerun_changed": stored_changed,
    }


def _confusion_matrix(evaluated: list[dict]) -> dict:
    """NLI mapped × Claude verdict hücre sayıları."""
    cells: dict[str, int] = {}
    for r in evaluated:
        key = f"{r['nli_mapped']}→{r['claude_verdict']}"
        cells[key] = cells.get(key, 0) + 1
    claude_dist = {}
    nli_dist = {}
    for r in evaluated:
        claude_dist[r["claude_verdict"]] = claude_dist.get(r["claude_verdict"], 0) + 1
        nli_dist[r["nli_mapped"]] = nli_dist.get(r["nli_mapped"], 0) + 1
    return {"cells": cells, "claude_verdict_dist": claude_dist, "nli_mapped_dist": nli_dist}


def _stored_delta_breakdown(evaluated: list[dict]) -> dict:
    stored = [r for r in evaluated if r.get("stored_nli") is not None]
    changed = [r for r in stored if r.get("stored_vs_rerun_changed")]
    unchanged = [r for r in stored if not r.get("stored_vs_rerun_changed")]

    def _by_source(rows: list[dict]) -> dict[str, int]:
        out: dict[str, int] = {}
        for r in rows:
            src = r.get("evidence_source") or "unknown"
            out[src] = out.get(src, 0) + 1
        return out

    transitions: dict[str, int] = {}
    for r in changed:
        key = f"{r.get('stored_nli')}→{r.get('rerun_nli')}"
        transitions[key] = transitions.get(key, 0) + 1

    return {
        "stored_with_label": len(stored),
        "changed_count": len(changed),
        "unchanged_count": len(unchanged),
        "changed_by_evidence_source": _by_source(changed),
        "unchanged_by_evidence_source": _by_source(unchanged),
        "label_transitions": transitions,
        "note": (
            "changed_by_evidence_source=snippet → aynı DB snippet, farklı etiket; "
            "retrieval drift değil. retrieved → canlı PubMed (stored olmayan iddialar)."
        ),
    }


def _summarize(results: list[dict]) -> dict:
    evaluated = [r for r in results if not r.get("skipped")]
    n = len(evaluated)
    if not n:
        return {"n": 0}

    exact = sum(1 for r in evaluated if r["exact_match"])
    binary = sum(1 for r in evaluated if r["binary_agree"])
    would_skip = sum(1 for r in evaluated if not r["would_escalate"])
    regrets = [r for r in evaluated if r["skip_regret"]]

    comparable = [r for r in evaluated if r["claude_verdict"] != CLAUDE_TARTISMALI]
    exact_comparable = sum(1 for r in comparable if r["exact_match"])

    binary_claude = [r for r in evaluated if r["claude_verdict"] in BINARY_VERDICTS]
    exact_binary_claude = sum(
        1 for r in binary_claude if r["nli_mapped"] == r["claude_verdict"]
    )

    both_binary = [
        r for r in evaluated
        if r["nli_mapped"] in BINARY_VERDICTS and r["claude_verdict"] in BINARY_VERDICTS
    ]
    agree_both_binary = sum(
        1 for r in both_binary if r["nli_mapped"] == r["claude_verdict"]
    )

    coarse_agree = sum(
        1 for r in evaluated if _coarse3(r["nli_mapped"]) == _coarse3(r["claude_verdict"])
    )

    tartismali_n = sum(1 for r in evaluated if r["claude_verdict"] == CLAUDE_TARTISMALI)
    belirsiz_exact = sum(
        1 for r in evaluated if r["exact_match"] and r["claude_verdict"] == "belirsiz"
    )

    by_video: dict[str, dict] = {}
    for r in evaluated:
        vid = r["video_id"]
        bucket = by_video.setdefault(vid, {"n": 0, "exact_match": 0, "skip_regret": 0})
        bucket["n"] += 1
        if r["exact_match"]:
            bucket["exact_match"] += 1
        if r["skip_regret"]:
            bucket["skip_regret"] += 1

    stored_breakdown = _stored_delta_breakdown(evaluated)

    return {
        "n": n,
        "metric_notes": METRIC_NOTES,
        "skipped_no_evidence": sum(1 for r in results if r.get("skipped")),
        "exact_match_rate": round(exact / n, 3),
        "exact_match_count": exact,
        "binary_agreement_rate": round(binary / n, 3),
        "binary_agreement_count": binary,
        "exact_match_comparable": {
            "numerator": exact_comparable,
            "denominator": len(comparable),
            "rate": round(exact_comparable / len(comparable), 3) if comparable else 0,
            "note": "Claude≠tartışmalı alt küme — tartışmalı yapısal tavan hariç",
        },
        "exact_match_binary_claude_only": {
            "numerator": exact_binary_claude,
            "denominator": len(binary_claude),
            "rate": round(exact_binary_claude / len(binary_claude), 3) if binary_claude else 0,
        },
        "binary_agree_given_both_binary": {
            "numerator": agree_both_binary,
            "denominator": len(both_binary),
            "rate": round(agree_both_binary / len(both_binary), 3) if both_binary else 0,
            "note": "Her iki taraf da doğrulanmış/yanlış — adil binary karşılaştırma",
        },
        "coarse_3class_agreement": {
            "numerator": coarse_agree,
            "denominator": n,
            "rate": round(coarse_agree / n, 3),
            "note": "doğrulanmış/yanlış/belirsiz+tartışmalı→uncertain",
        },
        "structural_ceiling": {
            "claude_tartismali_count": tartismali_n,
            "claude_tartismali_rate": round(tartismali_n / n, 3),
            "exact_match_impossible_count": tartismali_n,
            "belirsiz_exact_match_count": belirsiz_exact,
            "binary_lt_exact_explanation": (
                f"exact({exact}) - binary({binary}) = {exact - binary} "
                f"belirsiz==belirsiz eşleşmeleri binary sayılmaz"
            ),
        },
        "confusion_matrix": _confusion_matrix(evaluated),
        "would_skip_escalation": would_skip,
        "would_skip_escalation_rate": round(would_skip / n, 3),
        "skip_regret_count": len(regrets),
        "skip_regret_rate": round(len(regrets) / n, 3),
        "by_video": by_video,
        "stored_vs_rerun_delta": stored_breakdown["changed_count"],
        "stored_with_label": stored_breakdown["stored_with_label"],
        "stored_delta_breakdown": stored_breakdown,
        "cohort_note": "Yalnızca escalated=1 — NLI'nın zaten şüpheli bulduğu en zor iddialar",
    }


def _escalate_before_partial(nli_label: str, conf: float, risk: str) -> bool:
    """Yeni kısmi-kanıt kuralı hariç mevcut escalation."""
    if risk == "high":
        return True
    if nli_label == "NOT_ENOUGH_INFO":
        return True
    if conf < 0.75:
        return True
    return False


def audit_partial_escalation_rule(json_path: Path) -> dict:
    """
    Gölge test JSON + DB snippet ile kısmi-kanıt escalation kuralının offline denetimi.
    API / NLI re-run yok.
    """
    prior = json.loads(json_path.read_text(encoding="utf-8"))
    results = [r for r in prior.get("results", []) if not r.get("skipped")]

    conn = get_conn()
    snippets: dict[int, str] = {}
    for r in results:
        row = conn.execute(
            "SELECT nli_evidence_snippet FROM verdicts WHERE claim_id = ?",
            (r["claim_id"],),
        ).fetchone()
        snippets[r["claim_id"]] = (row[0] or "").strip() if row else ""
    conn.close()

    mismatch_key = {"nli_mapped": "doğrulanmış", "claude_verdict": "tartışmalı"}
    mismatch = [r for r in results if r["nli_mapped"] == mismatch_key["nli_mapped"]
                and r["claude_verdict"] == mismatch_key["claude_verdict"]]
    agreeing = [r for r in results if r not in mismatch]

    def _eval_row(r: dict, *, use_snippet: bool) -> dict:
        snip = snippets.get(r["claim_id"], "") if use_snippet else ""
        nli = r["rerun_nli"]
        conf = float(r["rerun_conf"])
        risk = r.get("initial_risk") or "medium"
        nli_dict = {"nli_label": nli, "nli_confidence": conf}
        old_skip = not _escalate_before_partial(nli, conf, risk)
        new_esc = should_escalate(nli_dict, risk, evidence_text=snip)
        caveat = evidence_has_partial_caveat(snip)
        newly = new_esc and old_skip
        return {
            "claim_id": r["claim_id"],
            "claim_text": r.get("claim_text", ""),
            "rerun_conf": conf,
            "snippet_len": len(snip),
            "partial_caveat": caveat,
            "old_would_skip": old_skip,
            "new_would_escalate": new_esc,
            "newly_escalated_by_rule": newly,
            "snippet_preview": snip[:220],
        }

    mismatch_eval = [_eval_row(r, use_snippet=True) for r in mismatch]
    agreeing_eval = [_eval_row(r, use_snippet=True) for r in agreeing]

    newly_mismatch = [e for e in mismatch_eval if e["newly_escalated_by_rule"]]
    false_pos = [e for e in agreeing_eval if e["newly_escalated_by_rule"]]
    caveat_mismatch = [e for e in mismatch_eval if e["partial_caveat"]]
    offline_snip = sum(1 for e in mismatch_eval if e["snippet_len"] > 20)

    # Yüksek-güven senaryosu: rerun SUPPORTS + conf>=0.75 varsayımı
    hyp_mismatch = [e for e in mismatch_eval if e["partial_caveat"] and e["snippet_len"] > 20]
    hyp_fp = [
        e for e in agreeing_eval
        if e["partial_caveat"]
        and e["snippet_len"] > 20
        and next(r["rerun_nli"] for r in agreeing if r["claim_id"] == e["claim_id"]) in ("SUPPORTS", "REFUTES")
    ]

    report = {
        "source_json": str(json_path),
        "mismatch_cohort": "doğrulanmış→tartışmalı",
        "mismatch_n": len(mismatch),
        "agreeing_n": len(agreeing),
        "offline_snippet_available": f"{offline_snip}/{len(mismatch)}",
        "partial_caveat_in_snippet": len(caveat_mismatch),
        "newly_escalated_actual_rerun": {
            "count": len(newly_mismatch),
            "note": "Gerçek rerun conf — kohortta conf<0.75 olduğu için çoğu zaten escalate",
            "examples": newly_mismatch[:5],
        },
        "false_positives_among_agreeing": {
            "count": len(false_pos),
            "examples": false_pos[:5],
        },
        "hypothetical_high_conf_scenario": {
            "note": "SUPPORTS/REFUTES + conf>=0.75 olsaydı kural kaçını yakalardı",
            "mismatch_partial_hits": len(hyp_mismatch),
            "agreeing_partial_hits": len(hyp_fp),
            "mismatch_examples": hyp_mismatch[:3],
            "agreeing_fp_examples": hyp_fp[:3],
        },
        "partial_caveat_examples": caveat_mismatch[:5],
    }
    return report


def _print_partial_audit(report: dict) -> None:
    print(f"[partial_audit] kohort {report['mismatch_cohort']} n={report['mismatch_n']}")
    print(f"[partial_audit] offline snippet: {report['offline_snippet_available']}")
    print(f"[partial_audit] kanıtta kısmi uyarı (26 küme): {report['partial_caveat_in_snippet']}")
    act = report["newly_escalated_actual_rerun"]
    print(f"[partial_audit] yeni kural → ek escalation (gerçek conf): {act['count']}")
    fp = report["false_positives_among_agreeing"]
    print(f"[partial_audit] false positive (uyumlu kohort): {fp['count']}")
    hyp = report["hypothetical_high_conf_scenario"]
    print(f"[partial_audit] hipotetik yüksek güven: mismatch hit={hyp['mismatch_partial_hits']} "
          f"agreeing fp={hyp['agreeing_partial_hits']}")
    for ex in report.get("partial_caveat_examples", [])[:3]:
        print(f"  örnek [{ex['claim_id']}] conf={ex['rerun_conf']}: {ex['snippet_preview'][:120]}...")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--video-ids", default=",".join(DEFAULT_VIDEO_IDS))
    ap.add_argument("--claim-ids", default="", help="virgülle claim_id (video filtresini override eder)")
    ap.add_argument("--skip-rerun", action="store_true",
                    help="Yalnızca DB'deki stored nli_label ile karşılaştır (model yüklemez)")
    ap.add_argument("--reanalyze", default="",
                    help="Mevcut JSON raporunu yeniden özetle (NLI re-run yok), örn. data/nli_shadow_test.json")
    ap.add_argument("--audit-partial-rule", default="",
                    help="Kısmi-kanıt escalation kuralını gölge JSON + DB snippet ile offline denetle")
    ap.add_argument("--out", default=str(OUT_PATH))
    args = ap.parse_args()

    if args.audit_partial_rule:
        src = Path(args.audit_partial_rule)
        report = audit_partial_escalation_rule(src)
        _print_partial_audit(report)
        audit_out = Path(args.out) if args.out != str(OUT_PATH) else src.parent / "nli_partial_rule_audit.json"
        audit_out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[partial_audit] rapor -> {audit_out}")
        return

    if args.reanalyze:
        src = Path(args.reanalyze)
        prior = json.loads(src.read_text(encoding="utf-8"))
        results = prior.get("results", [])
        summary = _summarize(results)
        report = {**prior, "summary": summary, "reanalyzed_from": str(src)}
        out_path = Path(args.out)
        out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        _print_summary(summary, out_path)
        return

    claim_ids = [int(x.strip()) for x in args.claim_ids.split(",") if x.strip()] or None
    video_ids = tuple(v.strip() for v in args.video_ids.split(",") if v.strip())

    conn = get_conn()
    cohort = _load_cohort(conn, video_ids=video_ids, claim_ids=claim_ids)
    conn.close()

    print(f"[nli_shadow] kohort: {len(cohort)} escalated iddia")
    results = [evaluate_row(row, skip_rerun=args.skip_rerun) for row in cohort]
    summary = _summarize(results)

    regrets = [r for r in results if r.get("skip_regret")]
    disagreements = [
        r for r in results
        if not r.get("skipped") and not r.get("exact_match")
    ][:15]

    report = {
        "video_ids": list(video_ids) if not claim_ids else None,
        "claim_ids": claim_ids,
        "skip_rerun": args.skip_rerun,
        "methodology": {
            "nli_mapping": NLI_TO_VERDICT,
            "exact_match": "nli_mapped == claude_verdict",
            "binary_agree": "both in {doğrulanmış, yanlış} and equal",
            "cohort_filter": "escalated=1 AND final_verdict IS NOT NULL",
        },
        "summary": summary,
        "skip_regret_examples": regrets[:10],
        "disagreement_examples": disagreements,
        "results": results,
    }

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    _print_summary(summary, out_path)


def _print_summary(summary: dict, out_path: Path) -> None:
    n = summary.get("n", 0)
    print(f"[nli_shadow] n={n} "
          f"exact={summary.get('exact_match_rate', 0)} "
          f"comparable={summary.get('exact_match_comparable', {}).get('rate', 0)} "
          f"both_binary={summary.get('binary_agree_given_both_binary', {}).get('rate', 0)}")
    ceiling = summary.get("structural_ceiling", {})
    print(f"[nli_shadow] tartışmalı={ceiling.get('claude_tartismali_count')} "
          f"({ceiling.get('claude_tartismali_rate', 0):.0%} exact imkansız)")
    delta = summary.get("stored_delta_breakdown", {})
    if delta.get("stored_with_label"):
        print(f"[nli_shadow] stored delta {delta.get('changed_count')}/{delta.get('stored_with_label')} "
              f"by_source={json.dumps(delta.get('changed_by_evidence_source', {}), ensure_ascii=False)}")
    print(f"[nli_shadow] rapor -> {out_path}")


if __name__ == "__main__":
    main()
