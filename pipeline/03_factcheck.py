"""
AŞAMA 3: Hibrit fact-checking.

Akış:
  1. Her iddia için ucuz NLI ilk filtresi çalışır (utils/nli.py, HF modeli, yerel/ücretsiz)
  2. should_escalate() kuralına göre:
        - initial_risk == high              -> her zaman Claude+web_search'e gönder
        - NLI belirsiz veya düşük güvenli    -> Claude+web_search'e gönder
        - NLI net ve yüksek güvenli          -> ucuz sonucu kaydet, LLM'e gitme (maliyet tasarrufu)
  3. Yüksek riskli/escalate edilen SONUÇLAR mutlaka insan onayına düşecek şekilde
     human_reviewed=0 olarak işaretlenir; otomasyonun "incelemeye gerek yok" kararı
     auto_accepted=1 ile ayrı taşınır (bkz. README "İnsan onayı" bölümü).
  4. LLM JSON'u utils/factcheck_calibrate.py ile kırpılır (tersine verdict,
     Wikipedia yüksek güven, 0.55 varsayılan kümesi). Ham reasoning hem DB'ye
     hem data/factcheck_debug.jsonl'e yazılır.

Kullanım:
    python pipeline/03_factcheck.py [--limit 100] [--skip-nli]
    python pipeline/03_factcheck.py --recheck-ids 96,110 --skip-nli
    python pipeline/03_factcheck.py --batch-submit --limit 10 --skip-nli
    python pipeline/03_factcheck.py --batch-submit --limit 10 --dump-payload data/batch_payload.json
    python pipeline/03_factcheck.py --batch-retrieve --wait

--skip-nli: HF modelini kurmadıysanız (torch/transformers ağır), doğrudan her
            iddiayı Claude+web_search'e gönderir. Daha pahalı ama kurulum gerektirmez.
--recheck-ids: belirtilen claim_id'leri yeniden değerlendir (eski verdict ancak
yeni sonuç başarılı olursa üzerine yazılır). Arşivli iddialar da dahil edilebilir.
--batch-submit: escalate edilecek iddiaları Message Batches API'ye gönder;
            verdict yazılmaz. Senkron --recheck-ids yolu durur.
--batch-retrieve: kayıtlı batch sonuçlarını çekip mevcut kalibrasyonla DB'ye yazar.
--dump-payload: --batch-submit ile payload'ı diske yaz, API'ye gönderme.

Normal kuyruk yalnızca archived_at IS NULL iddiaları işler — v2 re-extraction
sonrası superseded_* ile arşivlenen eski iddialar tekrar fact-check edilmez.
"""
import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent))
from utils.db import get_conn
from utils.claude_client import (
    escalate_factcheck,
    build_batch_request,
    parse_escalate_response,
    submit_message_batch,
    retrieve_message_batch,
    iter_batch_results,
    _usage_dict,
    summarize_cache_roles,
)
from utils.factcheck_calibrate import calibrate_factcheck
from utils.extraction_store import ACTIVE_CLAIM_WHERE
from utils.claim_library import lookup_library, ensure_library_table
from utils.nutrition_lookup import try_nutrition_factcheck
from utils.evidence_retrieval import (
    retrieve_hybrid_evidence,
    assess_evidence_sufficiency,
    collect_specificity_nli_scores,
    classify_evidence_expectation,
    FINAL_EVIDENCE_COUNT,
    EPISTEMIC_NO_DIRECT,
)
from utils.factcheck_review import (
    HIGH_RISK_HUMAN_REVIEW_CATEGORIES,
    is_drug_interaction_claim,
    compute_needs_human,
    apply_verdict_reasoning_mismatch,
    review_flags as _review_flags,
    PACKAGE_ONLY_FORCED_FLAG,
)

ROOT = Path(__file__).parent.parent
DEBUG_LOG = ROOT / "data" / "factcheck_debug.jsonl"
PENDING_BATCHES = ROOT / "data" / "pending_batches.json"

# Geriye dönük test import'ları (utils.factcheck_review'a yönlendirildi)
__all__ = [
    "HIGH_RISK_HUMAN_REVIEW_CATEGORIES",
    "is_drug_interaction_claim",
    "compute_needs_human",
    "_review_flags",
]


def _append_debug_log(record: dict) -> None:
    DEBUG_LOG.parent.mkdir(parents=True, exist_ok=True)
    with DEBUG_LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def _merge_library_review_flag(final: dict, library_review_hit: dict | None) -> None:
    if not library_review_hit:
        return
    extra = "library_flag_review"
    flags = final.get("calibration_flags") or ""
    if extra not in flags.split(","):
        final["calibration_flags"] = f"{flags},{extra}" if flags else extra


def _merge_package_only_flag(final: dict, force_package_only: bool) -> None:
    if not force_package_only:
        return
    extra = PACKAGE_ONLY_FORCED_FLAG
    flags = {f.strip() for f in (final.get("calibration_flags") or "").split(",") if f.strip()}
    if extra in flags:
        return
    raw = final.get("calibration_flags") or ""
    final["calibration_flags"] = f"{raw},{extra}" if raw else extra


def _append_calibration_flag(final: dict, extra: str) -> None:
    extra = (extra or "").strip()
    if not extra:
        return
    flags = {f.strip() for f in (final.get("calibration_flags") or "").split(",") if f.strip()}
    if extra in flags:
        return
    raw = final.get("calibration_flags") or ""
    final["calibration_flags"] = f"{raw},{extra}" if raw else extra


def _merge_tier_flags(
    final: dict,
    specificity_tier: str | None,
    epistemic_class: str | None,
) -> None:
    tier = (specificity_tier or "none").strip() or "none"
    _append_calibration_flag(final, f"specificity_tier:{tier}")
    if epistemic_class == EPISTEMIC_NO_DIRECT:
        _append_calibration_flag(final, EPISTEMIC_NO_DIRECT)


def _parse_recheck_ids(raw: str) -> list[int]:
    ids = []
    for part in (raw or "").split(","):
        part = part.strip()
        if not part:
            continue
        ids.append(int(part))
    return ids


def _load_pending_batches() -> dict:
    if not PENDING_BATCHES.exists():
        return {"batches": []}
    return json.loads(PENDING_BATCHES.read_text(encoding="utf-8"))


def _save_pending_batches(data: dict) -> None:
    PENDING_BATCHES.parent.mkdir(parents=True, exist_ok=True)
    PENDING_BATCHES.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _write_verdict(conn, *, claim_id, nli_label, nli_conf, nli_snippet, escalated_flag,
                   final, human_reviewed, auto_accepted, library_match) -> None:
    conn.execute("""
        INSERT OR REPLACE INTO verdicts (claim_id, nli_label, nli_confidence, nli_evidence_snippet,
                               escalated, final_verdict, confidence, source_url,
                               reasoning, source_directness, evidence_stance, source_tier,
                               calibration_flags, human_reviewed, auto_accepted, library_match)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (claim_id, nli_label, nli_conf, nli_snippet, escalated_flag,
          final["final_verdict"], final["confidence"], final["source_url"],
          final["reasoning"], final["source_directness"], final["evidence_stance"],
          final["source_tier"], final["calibration_flags"],
          human_reviewed, auto_accepted, library_match))
    conn.commit()


def _finalize_escalated(
    conn,
    *,
    claim_id,
    claim_text,
    category,
    initial_risk,
    evidence,
    retrieval_path,
    force_package_only,
    specificity_ok,
    strong_match,
    nli_label,
    nli_conf,
    nli_snippet,
    library_review_hit,
    library_match,
    raw_result,
    usage=None,
    specificity_tier="none",
    epistemic_class=None,
) -> None:
    """Mevcut senkron escalate sonrası yol — kalibrasyon/needs_human değişmez."""
    parse_failed = bool(raw_result.get("parse_failed"))
    calibrated = (
        calibrate_factcheck(raw_result, evidence=evidence)
        if not parse_failed else raw_result
    )
    final = {
        "final_verdict": None, "confidence": None, "source_url": None,
        "reasoning": None, "source_directness": None, "evidence_stance": None,
        "source_tier": None, "calibration_flags": "",
    }
    for k in final:
        if k in calibrated:
            final[k] = calibrated.get(k)
    _merge_package_only_flag(final, force_package_only)
    _merge_tier_flags(final, specificity_tier, epistemic_class)
    if force_package_only and not parse_failed:
        calibrated["needs_human"] = True
        calibrated["calibration_flags"] = final.get("calibration_flags") or ""
    _append_debug_log({
        "claim_id": claim_id,
        "claim_text": claim_text,
        "cite_source": calibrated.get("cite_source"),
        "retrieval_path": retrieval_path,
        "retrieval_tiers": [e.get("retrieval_tier") for e in (evidence or [])],
        "package_urls": [e.get("url") for e in (evidence or [])],
        "force_package_only": force_package_only,
        "specificity_ok": specificity_ok,
        "strong_match": strong_match,
        "specificity_tier": specificity_tier,
        "epistemic_class": epistemic_class,
        "raw": {
            "final_verdict": raw_result.get("final_verdict"),
            "confidence": raw_result.get("confidence"),
            "reasoning": raw_result.get("reasoning"),
            "source_url": raw_result.get("source_url"),
            "source_directness": raw_result.get("source_directness"),
            "evidence_stance": raw_result.get("evidence_stance"),
            "source_tier": raw_result.get("source_tier"),
        },
        "calibrated": {
            "final_verdict": final["final_verdict"],
            "confidence": final["confidence"],
            "calibration_flags": final["calibration_flags"],
            "source_tier": final["source_tier"],
            "source_directness": final["source_directness"],
            "evidence_stance": final["evidence_stance"],
            "cite_source": calibrated.get("cite_source"),
        },
        "usage": usage,
    })
    _merge_library_review_flag(final, library_review_hit)
    apply_verdict_reasoning_mismatch(final)
    needs_human = compute_needs_human(
        category=category,
        initial_risk=initial_risk,
        claim_text=claim_text,
        parse_failed=parse_failed,
        final_verdict=final["final_verdict"],
        escalated_flag=1,
        calibrated=calibrated,
        source_directness=final["source_directness"],
        library_review_hit=library_review_hit,
        calibration_flags=final.get("calibration_flags"),
    )
    human_reviewed, auto_accepted = _review_flags(needs_human=needs_human)
    _write_verdict(
        conn,
        claim_id=claim_id,
        nli_label=nli_label,
        nli_conf=nli_conf,
        nli_snippet=nli_snippet,
        escalated_flag=1,
        final=final,
        human_reviewed=human_reviewed,
        auto_accepted=auto_accepted,
        library_match=library_match,
    )
    flag = "🔴 İNSAN ONAYI BEKLİYOR" if needs_human else "✓"
    conf_s = f"{final['confidence']:.2f}" if final["confidence"] is not None else "—"
    print(f"  [{claim_id}] {final['final_verdict']} conf={conf_s} "
          f"tier={final['source_tier'] or '—'} cite={calibrated.get('cite_source') or '—'} "
          f"path={retrieval_path or '—'} "
          f"direct={final['source_directness'] or '—'} "
          f"stance={final['evidence_stance'] or '—'} (esc=1) {flag}")
    if final["reasoning"]:
        print(f"           {final['reasoning'][:240]}")
    if final["calibration_flags"]:
        print(f"           kalibrasyon: {final['calibration_flags']}")


def _pending_claim_ids() -> set[int]:
    ids: set[int] = set()
    for rec in _load_pending_batches().get("batches") or []:
        if rec.get("applied"):
            continue
        for cid in rec.get("claim_ids") or []:
            ids.add(int(cid))
    return ids


def _flush_batch_submit(jobs: list[dict], args) -> None:
    if not jobs:
        print("[batch] escalate edilecek iddia yok — gönderim yok")
        return
    already = _pending_claim_ids()
    filtered = [j for j in jobs if int(j["claim_id"]) not in already]
    skipped = len(jobs) - len(filtered)
    if skipped:
        print(f"[batch] zaten pending olan {skipped} iddia atlandı")
    jobs = filtered
    if not jobs:
        print("[batch] yeni iddia kalmadı")
        return
    requests = [
        build_batch_request(
            j["claim_id"],
            j["claim_text"],
            j.get("evidence"),
            force_package_only=bool(j.get("force_package_only")),
            specificity_tier=j.get("specificity_tier"),
            epistemic_class=j.get("epistemic_class"),
        )
        for j in jobs
    ]
    payload = {"requests": requests}
    if args.dump_payload:
        path = Path(args.dump_payload)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[batch] payload yazıldı (API yok): {path} n={len(requests)}")
        return
    batch = submit_message_batch(requests)
    rec = {
        "batch_id": batch.id,
        "processing_status": getattr(batch, "processing_status", None),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "applied": False,
        "claim_ids": [j["claim_id"] for j in jobs],
        "jobs": jobs,
    }
    data = _load_pending_batches()
    data.setdefault("batches", []).append(rec)
    _save_pending_batches(data)
    print(
        f"[batch] gönderildi id={batch.id} n={len(requests)} "
        f"status={rec['processing_status']}"
    )
    print(f"[batch] kayıt: {PENDING_BATCHES}")


def _run_batch_retrieve(conn, args) -> None:
    data = _load_pending_batches()
    pending = [b for b in data.get("batches") or [] if not b.get("applied")]
    if not pending:
        print("[batch] bekleyen (applied=false) batch yok")
        return
    for rec in pending:
        batch_id = rec["batch_id"]
        status = retrieve_message_batch(batch_id)
        proc = getattr(status, "processing_status", None)
        print(f"[batch] {batch_id} status={proc}")
        started = time.time()
        while args.wait and proc != "ended":
            if time.time() - started > args.wait_timeout:
                print(f"[batch] timeout {args.wait_timeout}s — sonra tekrar --batch-retrieve")
                _save_pending_batches(data)
                return
            time.sleep(max(5, args.poll_interval))
            status = retrieve_message_batch(batch_id)
            proc = getattr(status, "processing_status", None)
            counts = getattr(status, "request_counts", None)
            print(f"[batch] {batch_id} status={proc} counts={counts}")
        if proc != "ended":
            print(f"[batch] {batch_id} henüz bitmedi (status={proc})")
            continue
        jobs_by_id = {str(j["claim_id"]): j for j in rec.get("jobs") or []}
        ok = failed = 0
        usage_sum = {
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
        }
        usage_by_custom_id: dict[str, dict] = {}
        for result in iter_batch_results(batch_id):
            cid = str(getattr(result, "custom_id", "") or "")
            job = jobs_by_id.get(cid)
            if not job:
                usage_by_custom_id[cid] = {
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "cache_creation_input_tokens": 0,
                    "cache_read_input_tokens": 0,
                    "result_type": "unknown_custom_id",
                }
                print(f"[batch] bilinmeyen custom_id={cid}")
                failed += 1
                continue
            message = getattr(getattr(result, "result", None), "message", None)
            if message is None:
                rtype = getattr(getattr(result, "result", None), "type", None)
                usage_by_custom_id[cid] = {
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "cache_creation_input_tokens": 0,
                    "cache_read_input_tokens": 0,
                    "result_type": str(rtype),
                }
                print(f"  [{cid}] batch result type={rtype} — atlandı")
                failed += 1
                continue
            usage = _usage_dict(getattr(message, "usage", None))
            usage_by_custom_id[cid] = usage
            print(
                f"  [{cid}] usage write={usage.get('cache_creation_input_tokens')} "
                f"read={usage.get('cache_read_input_tokens')} "
                f"input={usage.get('input_tokens')} output={usage.get('output_tokens')}"
            )
            for key in usage_sum:
                usage_sum[key] += int(usage.get(key) or 0)
            raw_result = parse_escalate_response(message)
            _finalize_escalated(conn, raw_result=raw_result, usage=usage, **{
                k: job[k] for k in (
                    "claim_id", "claim_text", "category", "initial_risk", "evidence",
                    "retrieval_path", "force_package_only", "specificity_ok",
                    "strong_match", "nli_label", "nli_conf", "nli_snippet",
                    "library_review_hit", "library_match",
                )
            }, specificity_tier=job.get("specificity_tier") or "none",
               epistemic_class=job.get("epistemic_class"))
            ok += 1
        cache_summary = summarize_cache_roles(usage_by_custom_id)
        rec["applied"] = True
        rec["applied_at"] = datetime.now(timezone.utc).isoformat()
        rec["processing_status"] = "ended"
        rec["usage_sum"] = usage_sum
        rec["usage_by_custom_id"] = usage_by_custom_id
        rec["cache_roles"] = cache_summary
        rec["ok"] = ok
        rec["failed"] = failed
        _save_pending_batches(data)
        print(f"[batch] uygulandı {batch_id}: ok={ok} failed={failed} usage={usage_sum}")
        print(
            f"[batch] cache custom_id: write>0={cache_summary['n_write_gt0']} "
            f"read>0={cache_summary['n_read_gt0']} both={cache_summary['n_both']} "
            f"write_only={cache_summary['n_write_only']} "
            f"read_only={cache_summary['n_read_only']} none={cache_summary['n_none']}"
        )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=200)
    ap.add_argument("--skip-nli", action="store_true")
    ap.add_argument("--video-id", default=None, help="yalnızca bu video_id'deki iddiaları işle")
    ap.add_argument("--recheck-ids", default="",
                    help="virgülle ayrılmış claim_id listesini yeniden fact-check et")
    ap.add_argument("--batch-submit", action="store_true",
                    help="escalate iddialarını Batch API'ye gönder (verdict yazma)")
    ap.add_argument("--batch-retrieve", action="store_true",
                    help="bekleyen batch sonuçlarını çek ve mevcut kalibrasyonla DB'ye yaz")
    ap.add_argument("--dump-payload", default="",
                    help="--batch-submit: istek JSON'unu yaz, Anthropic'e gönderme")
    ap.add_argument("--wait", action="store_true",
                    help="--batch-retrieve: processing_status=ended olana kadar bekle")
    ap.add_argument("--wait-timeout", type=int, default=1800)
    ap.add_argument("--poll-interval", type=int, default=20)
    args = ap.parse_args()
    if args.batch_submit and args.batch_retrieve:
        raise SystemExit("--batch-submit ve --batch-retrieve aynı anda kullanılamaz")

    conn = get_conn()
    ensure_library_table(conn)
    if args.batch_retrieve:
        _run_batch_retrieve(conn, args)
        conn.close()
        return
    recheck_ids = _parse_recheck_ids(args.recheck_ids)
    if recheck_ids:
        placeholders = ",".join("?" * len(recheck_ids))
        # Eski verdict silinmez — başarılı INSERT OR REPLACE üzerine yazar.
        # (API bakiyesi bitince silmek #96/#110'u veri_eksik bırakmıştı.)
        rows = conn.execute(f"""
            SELECT c.claim_id, c.claim_text, c.search_query_en, c.category, c.initial_risk
            FROM claims c
            WHERE c.claim_id IN ({placeholders})
            ORDER BY CASE c.initial_risk WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END,
                     c.claim_id
        """, recheck_ids).fetchall()
        print(f"[factcheck] yeniden değerlendirilecek: {len(rows)} iddia ({recheck_ids})")
    else:
        video_clause = "AND c.video_id = ?" if args.video_id else ""
        params: list = []
        if args.video_id:
            params.append(args.video_id)
        params.append(args.limit)
        rows = conn.execute(f"""
            SELECT c.claim_id, c.claim_text, c.search_query_en, c.category, c.initial_risk
            FROM claims c
            LEFT JOIN verdicts vr ON vr.claim_id = c.claim_id
            WHERE vr.claim_id IS NULL
              AND c.{ACTIVE_CLAIM_WHERE}
              {video_clause}
            ORDER BY CASE c.initial_risk WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END,
                     c.claim_id
            LIMIT ?
        """, params).fetchall()
        scope = f" video={args.video_id}" if args.video_id else ""
        print(f"[factcheck] işlenecek iddia sayısı: {len(rows)}{scope}")

    nli_check = should_escalate = None
    if not args.skip_nli:
        from utils.nli import nli_check as _nli_check, should_escalate as _should_escalate
        nli_check, should_escalate = _nli_check, _should_escalate

    ok, failed = 0, 0
    batch_jobs: list[dict] = []
    for row in rows:
        claim_id, claim_text, search_query_en, category, initial_risk = (
            row["claim_id"], row["claim_text"], row["search_query_en"], row["category"], row["initial_risk"])
        nli_label, nli_conf, nli_snippet = None, None, None
        no_evidence_found = False
        do_escalate = True
        library_match = 0
        evidence: list[dict] = []
        retrieval_path = ""

        lib_hit = lookup_library(conn, claim_text)
        library_review_hit = None
        if lib_hit and lib_hit.get("match_tier") == "flag_review" and not recheck_ids:
            library_review_hit = lib_hit
            print(
                f"  [{claim_id}] library flag_review "
                f"origin={lib_hit.get('origin_claim_id')} "
                f"cosine={float(lib_hit.get('match_score') or 0):.4f} "
                f"lexical={float(lib_hit.get('match_jaccard') or 0):.3f} "
                f"why={lib_hit.get('match_reason')} "
                f"(Claude bypass yok)"
            )
            _append_debug_log({
                "claim_id": claim_id,
                "claim_text": claim_text,
                "library_flag_review": {
                    "origin_claim_id": lib_hit.get("origin_claim_id"),
                    "match_score": lib_hit.get("match_score"),
                    "match_jaccard": lib_hit.get("match_jaccard"),
                    "match_reason": lib_hit.get("match_reason"),
                    "library_claim_text": (lib_hit.get("claim_text") or "")[:200],
                },
            })
        if lib_hit and lib_hit.get("match_tier") == "auto" and not recheck_ids:
            library_match = 1
            final = {
                "final_verdict": lib_hit["final_verdict"],
                "confidence": lib_hit["confidence"],
                "source_url": lib_hit.get("source_url"),
                "reasoning": (
                    f"Kütüphane eşleşmesi (origin claim_id={lib_hit.get('origin_claim_id')}): "
                    f"{lib_hit.get('reasoning', '')[:300]}"
                ),
                "source_directness": "direct",
                "evidence_stance": "supports" if lib_hit["final_verdict"] == "doğrulanmış" else "contradicts",
                "source_tier": lib_hit.get("source_tier") or "guideline",
                "calibration_flags": "library_match",
            }
            escalated_flag = 0
            parse_failed = False
            calibrated = {}
            apply_verdict_reasoning_mismatch(final)
            needs_human = compute_needs_human(
                category=category,
                initial_risk=initial_risk,
                claim_text=claim_text,
                parse_failed=False,
                final_verdict=final["final_verdict"],
                escalated_flag=0,
                calibrated={},
                source_directness=final.get("source_directness"),
                library_review_hit=library_review_hit,
                calibration_flags=final.get("calibration_flags"),
            )
            human_reviewed, auto_accepted = _review_flags(needs_human=needs_human)
            conn.execute("""
                INSERT OR REPLACE INTO verdicts (claim_id, nli_label, nli_confidence, nli_evidence_snippet,
                                       escalated, final_verdict, confidence, source_url,
                                       reasoning, source_directness, evidence_stance, source_tier,
                                       calibration_flags, human_reviewed, auto_accepted, library_match)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (claim_id, None, None, "(verified_claim_library eşleşmesi)", escalated_flag,
                  final["final_verdict"], final["confidence"], final["source_url"],
                  final["reasoning"], final["source_directness"], final["evidence_stance"],
                  final["source_tier"], final["calibration_flags"],
                  human_reviewed, auto_accepted, library_match))
            conn.commit()
            ok += 1
            print(f"  [{claim_id}] {final['final_verdict']} (kütüphane) library_match=1")
            continue

        if not args.skip_nli:
            nut_result = try_nutrition_factcheck(claim_text)
            if nut_result and nut_result.get("final_verdict") in ("doğrulanmış", "yanlış"):
                final = {k: nut_result.get(k) for k in (
                    "final_verdict", "confidence", "source_url", "reasoning",
                    "source_directness", "evidence_stance", "source_tier", "calibration_flags")}
                _merge_library_review_flag(final, library_review_hit)
                apply_verdict_reasoning_mismatch(final)
                needs_human = compute_needs_human(
                    category=category,
                    initial_risk=initial_risk,
                    claim_text=claim_text,
                    parse_failed=False,
                    final_verdict=final["final_verdict"],
                    escalated_flag=0,
                    calibrated={},
                    source_directness=final.get("source_directness"),
                    library_review_hit=library_review_hit,
                    calibration_flags=final.get("calibration_flags"),
                    extra_needs_human=bool(nut_result.get("needs_human")),
                )
                human_reviewed, auto_accepted = _review_flags(needs_human=needs_human)
                conn.execute("""
                    INSERT OR REPLACE INTO verdicts (claim_id, nli_label, nli_confidence, nli_evidence_snippet,
                                           escalated, final_verdict, confidence, source_url,
                                           reasoning, source_directness, evidence_stance, source_tier,
                                           calibration_flags, human_reviewed, auto_accepted, library_match)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (claim_id, None, None, f"({final.get('source_tier') or 'nutrition'})", 0,
                      final["final_verdict"], final["confidence"], final["source_url"],
                      final["reasoning"], final["source_directness"], final["evidence_stance"],
                      final["source_tier"], final.get("calibration_flags", ""),
                      human_reviewed, auto_accepted, 0))
                conn.commit()
                ok += 1
                flag = "🔴 İNSAN ONAYI BEKLİYOR" if needs_human else "✓"
                print(f"  [{claim_id}] {final['final_verdict']} ({final.get('source_tier')}) {flag}")
                continue

        evidence, retrieval_path = retrieve_hybrid_evidence(
            claim_text, search_query_en=search_query_en, category=category)
        if retrieval_path and retrieval_path != "pubmed":
            print(f"[evidence] hibrit yol: {retrieval_path} ({len(evidence)} parça)")
        if not args.skip_nli:
            if evidence:
                nli_slice = evidence[:FINAL_EVIDENCE_COUNT]
                evidence_text = " ".join(f"{e['title']} {e.get('abstract', '')}".strip() for e in nli_slice)
                nli_result = nli_check(claim_text, evidence_text)
                nli_label, nli_conf = nli_result["nli_label"], nli_result["nli_confidence"]
                nli_snippet = evidence_text[:500]
                do_escalate = should_escalate(nli_result, initial_risk, evidence_text=evidence_text)
            else:
                no_evidence_found = True
                do_escalate = True
                nli_snippet = "(hibrit retrieval: kanıt bulunamadı — otomatik escalate edildi)"
        else:
            if not evidence:
                no_evidence_found = True

        final = {
            "final_verdict": None, "confidence": None, "source_url": None,
            "reasoning": None, "source_directness": None, "evidence_stance": None,
            "source_tier": None, "calibration_flags": "",
        }
        escalated_flag = 0
        parse_failed = False
        calibrated = {}
        try:
            if do_escalate:
                escalated_flag = 1
                suff = (
                    assess_evidence_sufficiency(evidence, claim_text, search_query_en)
                    if evidence else None
                )
                force_package_only = bool(suff and suff.strong_match)
                scores = collect_specificity_nli_scores(claim_text, evidence or [])
                epistemic_class = classify_evidence_expectation(claim_text, scores)
                specificity_tier = suff.specificity_tier if suff else "none"
                if force_package_only:
                    print(
                        f"[evidence] strong_match — web_search kapalı "
                        f"(specificity_ok={suff.specificity_ok})"
                    )
                elif specificity_tier == "supportive":
                    print(
                        f"[evidence] supportive — web_search açık, paket öncelikli "
                        f"(tier={specificity_tier})"
                    )
                if epistemic_class:
                    print(f"[evidence] epistemic={epistemic_class}")
                job = {
                    "claim_id": claim_id,
                    "claim_text": claim_text,
                    "category": category,
                    "initial_risk": initial_risk,
                    "evidence": list(evidence or []),
                    "retrieval_path": retrieval_path,
                    "force_package_only": force_package_only,
                    "specificity_ok": None if suff is None else suff.specificity_ok,
                    "strong_match": None if suff is None else suff.strong_match,
                    "specificity_tier": specificity_tier,
                    "epistemic_class": epistemic_class,
                    "nli_label": nli_label,
                    "nli_conf": nli_conf,
                    "nli_snippet": nli_snippet,
                    "library_review_hit": (
                        json.loads(json.dumps(dict(library_review_hit), default=str))
                        if library_review_hit else None
                    ),
                    "library_match": library_match,
                }
                job["evidence"] = json.loads(json.dumps(job["evidence"], default=str))
                if args.batch_submit:
                    batch_jobs.append(job)
                    print(
                        f"  [{claim_id}] batch kuyruğu "
                        f"(force_package_only={force_package_only})"
                    )
                    continue
                raw_result = escalate_factcheck(
                    claim_text, evidence=evidence, force_package_only=force_package_only,
                    specificity_tier=specificity_tier, epistemic_class=epistemic_class,
                )
                _finalize_escalated(conn, raw_result=raw_result, **job)
                ok += 1
                continue
            else:
                # ucuz filtre yeterince güvenliydi, LLM'e gitmeden NLI etiketini kullan
                # (kalibrasyonun indirect→tartışmalı kuralı NLI yoluna uygulanmaz —
                # aksi halde her ucuz sonuç insan kuyruğuna düşer)
                final["final_verdict"] = {"SUPPORTS": "doğrulanmış", "REFUTES": "yanlış"}.get(nli_label, "belirsiz")
                final["confidence"] = nli_conf
                final["reasoning"] = (
                    f"NLI ilk filtresi: {nli_label} (güven {nli_conf:.2f}); "
                    "LLM'e escalate edilmedi."
                )
                final["source_directness"] = "indirect"
                final["evidence_stance"] = (
                    {"SUPPORTS": "supports", "REFUTES": "contradicts"}.get(nli_label, "insufficient")
                )
                final["source_tier"] = (
                    (evidence[0].get("source_tier") or evidence[0].get("source"))
                    if evidence else "primary_study"
                ) or "primary_study"
                calibrated = {}
        except Exception as e:
            # Tek iddianın API hatası tüm batch'i durdurmasın; bu satır verdicts'e hiç
            # yazılmaz, bir sonraki çalıştırmada tekrar denenir (WHERE vr.claim_id IS NULL).
            print(f"  [{claim_id}] !! hata, atlandı (tekrar denenecek): {e}")
            failed += 1
            continue

        # parse_failed veya no_evidence_found ise insan onayı olmadan asla "temiz" sayılmaz.
        _merge_library_review_flag(final, library_review_hit)
        apply_verdict_reasoning_mismatch(final)
        needs_human = compute_needs_human(
            category=category,
            initial_risk=initial_risk,
            claim_text=claim_text,
            parse_failed=parse_failed,
            final_verdict=final["final_verdict"],
            escalated_flag=escalated_flag,
            calibrated=calibrated,
            source_directness=final["source_directness"],
            library_review_hit=library_review_hit,
            calibration_flags=final.get("calibration_flags"),
        )
        human_reviewed, auto_accepted = _review_flags(needs_human=needs_human)
        conn.execute("""
            INSERT OR REPLACE INTO verdicts (claim_id, nli_label, nli_confidence, nli_evidence_snippet,
                                   escalated, final_verdict, confidence, source_url,
                                   reasoning, source_directness, evidence_stance, source_tier,
                                   calibration_flags, human_reviewed, auto_accepted, library_match)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (claim_id, nli_label, nli_conf, nli_snippet, escalated_flag,
              final["final_verdict"], final["confidence"], final["source_url"],
              final["reasoning"], final["source_directness"], final["evidence_stance"],
              final["source_tier"], final["calibration_flags"],
              human_reviewed, auto_accepted, library_match))
        conn.commit()
        ok += 1
        flag = "🔴 İNSAN ONAYI BEKLİYOR" if needs_human else "✓"
        conf_s = f"{final['confidence']:.2f}" if final["confidence"] is not None else "—"
        print(f"  [{claim_id}] {final['final_verdict']} conf={conf_s} "
              f"tier={final['source_tier'] or '—'} cite={calibrated.get('cite_source') or '—'} "
              f"path={retrieval_path or '—'} "
              f"direct={final['source_directness'] or '—'} "
              f"stance={final['evidence_stance'] or '—'} (esc={escalated_flag}) {flag}")
        if final["reasoning"]:
            print(f"           {final['reasoning'][:240]}")
        if final["calibration_flags"]:
            print(f"           kalibrasyon: {final['calibration_flags']}")

    if args.batch_submit:
        _flush_batch_submit(batch_jobs, args)

    print(f"\n[factcheck] {ok} iddia işlendi, {failed} iddia hata verdi (tekrar denenecek).")
    print(f"[factcheck] ham reasoning -> {DEBUG_LOG}")

    conn.close()
    print("[factcheck] tamamlandı. İnsan onayı bekleyen iddialar human_reviewed=0 ile işaretlendi — "
          "auto_accepted=1 yalnızca otomasyon kararını gösterir (bkz. README).")


if __name__ == "__main__":
    main()
