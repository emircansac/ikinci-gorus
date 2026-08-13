"""
AŞAMA 3: Hibrit fact-checking.

Akış:
  1. Her iddia için ucuz NLI ilk filtresi çalışır (utils/nli.py, HF modeli, yerel/ücretsiz)
  2. should_escalate() kuralına göre:
        - initial_risk == high              -> her zaman Claude+web_search'e gönder
        - NLI belirsiz veya düşük güvenli    -> Claude+web_search'e gönder
        - NLI net ve yüksek güvenli          -> ucuz sonucu kaydet, LLM'e gitme (maliyet tasarrufu)
  3. Yüksek riskli/escalate edilen SONUÇLAR mutlaka insan onayına düşecek şekilde
     human_reviewed=0 olarak işaretlenir (bkz. README "İnsan onayı" bölümü).
  4. LLM JSON'u utils/factcheck_calibrate.py ile kırpılır (tersine verdict,
     Wikipedia yüksek güven, 0.55 varsayılan kümesi). Ham reasoning hem DB'ye
     hem data/factcheck_debug.jsonl'e yazılır.

Kullanım:
    python pipeline/03_factcheck.py [--limit 100] [--skip-nli]
    python pipeline/03_factcheck.py --recheck-ids 96,110 --skip-nli

--skip-nli: HF modelini kurmadıysanız (torch/transformers ağır), doğrudan her
            iddiayı Claude+web_search'e gönderir. Daha pahalı ama kurulum gerektirmez.
--recheck-ids: belirtilen claim_id'leri yeniden değerlendir (eski verdict ancak
yeni sonuç başarılı olursa üzerine yazılır). Arşivli iddialar da dahil edilebilir.

Normal kuyruk yalnızca archived_at IS NULL iddiaları işler — v2 re-extraction
sonrası superseded_* ile arşivlenen eski iddialar tekrar fact-check edilmez.
"""
import argparse
import json
import re
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent))
from utils.db import get_conn
from utils.claude_client import escalate_factcheck
from utils.factcheck_calibrate import calibrate_factcheck
from utils.extraction_store import ACTIVE_CLAIM_WHERE
from utils.claim_library import lookup_library, ensure_library_table
from utils.nutrition_lookup import try_nutrition_factcheck
from utils.evidence_retrieval import retrieve_pubmed_evidence, FINAL_EVIDENCE_COUNT

ROOT = Path(__file__).parent.parent
DEBUG_LOG = ROOT / "data" / "factcheck_debug.jsonl"

# "tanı" eklendi: yanlış teşhis iddiaları (ör. "sertleşme sorununuzun kaynağı X'tir")
# risk etiketi 'medium' bile olsa insan onayı gerektirmeli — bir önceki sürümde
# sadece initial_risk='high' ya da bu üç kategoriye bakılıyordu, 'tanı' unutulmuştu.
HIGH_RISK_HUMAN_REVIEW_CATEGORIES = {"tedavi", "doz", "mucize-ürün", "tanı"}

# İlaç-etkileşimi iddiaları mekanizma kategorisinde kalabilir (#704 warfarin/lahana)
# ama insan onayı zorunlu olmalı — kategori listesi bunları kapsamaz.
_DRUG_INTERACTION_RE = re.compile(
    r"antikoag[üu]lan|warfarin|"
    r"\bdoac\b|apiksaban|rivaroksaban|dabigatran|edoksaban|"
    r"kan\s*suland[ıi]r[ıi]c[ıi]|"
    r"ila[çc].{0,40}etkile[sş]im|vitamin\s*k.{0,30}(ila[çc]|warfarin)",
    re.IGNORECASE,
)


def is_drug_interaction_claim(claim_text: str) -> bool:
    return bool(_DRUG_INTERACTION_RE.search(claim_text or ""))


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


def _parse_recheck_ids(raw: str) -> list[int]:
    ids = []
    for part in (raw or "").split(","):
        part = part.strip()
        if not part:
            continue
        ids.append(int(part))
    return ids


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=200)
    ap.add_argument("--skip-nli", action="store_true")
    ap.add_argument("--video-id", default=None, help="yalnızca bu video_id'deki iddiaları işle")
    ap.add_argument("--recheck-ids", default="",
                    help="virgülle ayrılmış claim_id listesini yeniden fact-check et")
    args = ap.parse_args()

    conn = get_conn()
    ensure_library_table(conn)
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
    for row in rows:
        claim_id, claim_text, search_query_en, category, initial_risk = (
            row["claim_id"], row["claim_text"], row["search_query_en"], row["category"], row["initial_risk"])
        nli_label, nli_conf, nli_snippet = None, None, None
        no_evidence_found = False
        do_escalate = True
        library_match = 0
        evidence: list[dict] = []

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
            needs_human = is_drug_interaction_claim(claim_text)
            conn.execute("""
                INSERT OR REPLACE INTO verdicts (claim_id, nli_label, nli_confidence, nli_evidence_snippet,
                                       escalated, final_verdict, confidence, source_url,
                                       reasoning, source_directness, evidence_stance, source_tier,
                                       calibration_flags, human_reviewed, library_match)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (claim_id, None, None, "(verified_claim_library eşleşmesi)", escalated_flag,
                  final["final_verdict"], final["confidence"], final["source_url"],
                  final["reasoning"], final["source_directness"], final["evidence_stance"],
                  final["source_tier"], final["calibration_flags"],
                  0 if needs_human else 1, library_match))
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
                needs_human = (
                    category in HIGH_RISK_HUMAN_REVIEW_CATEGORIES
                    or initial_risk == "high"
                    or is_drug_interaction_claim(claim_text)
                    or nut_result.get("needs_human")
                )
                _merge_library_review_flag(final, library_review_hit)
                conn.execute("""
                    INSERT OR REPLACE INTO verdicts (claim_id, nli_label, nli_confidence, nli_evidence_snippet,
                                           escalated, final_verdict, confidence, source_url,
                                           reasoning, source_directness, evidence_stance, source_tier,
                                           calibration_flags, human_reviewed, library_match)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (claim_id, None, None, f"({final.get('source_tier') or 'nutrition'})", 0,
                      final["final_verdict"], final["confidence"], final["source_url"],
                      final["reasoning"], final["source_directness"], final["evidence_stance"],
                      final["source_tier"], final.get("calibration_flags", ""),
                      0 if needs_human else 1, 0))
                conn.commit()
                ok += 1
                flag = "🔴 İNSAN ONAYI BEKLİYOR" if needs_human else "✓"
                print(f"  [{claim_id}] {final['final_verdict']} ({final.get('source_tier')}) {flag}")
                continue

        if not args.skip_nli:
            evidence = retrieve_pubmed_evidence(claim_text, search_query_en=search_query_en, category=category)
            if evidence:
                nli_slice = evidence[:FINAL_EVIDENCE_COUNT]
                evidence_text = " ".join(f"{e['title']} {e.get('abstract', '')}".strip() for e in nli_slice)
                nli_result = nli_check(claim_text, evidence_text)
                nli_label, nli_conf = nli_result["nli_label"], nli_result["nli_confidence"]
                nli_snippet = evidence_text[:500]
                do_escalate = should_escalate(nli_result, initial_risk)
            else:
                no_evidence_found = True
                do_escalate = True
                nli_snippet = "(hibrit retrieval: kanıt bulunamadı — otomatik escalate edildi)"
        else:
            evidence = retrieve_pubmed_evidence(claim_text, search_query_en=search_query_en, category=category)
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
                raw_result = escalate_factcheck(claim_text, evidence=evidence)
                parse_failed = bool(raw_result.get("parse_failed"))
                calibrated = (
                    calibrate_factcheck(raw_result, evidence=evidence)
                    if not parse_failed else raw_result
                )
                for k in final:
                    if k in calibrated:
                        final[k] = calibrated.get(k)
                _append_debug_log({
                    "claim_id": claim_id,
                    "claim_text": claim_text,
                    "cite_source": calibrated.get("cite_source"),
                    "package_urls": [e.get("url") for e in (evidence or [])],
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
                })
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
        needs_human = (category in HIGH_RISK_HUMAN_REVIEW_CATEGORIES) or (initial_risk == "high") \
            or is_drug_interaction_claim(claim_text) \
            or parse_failed or (final["final_verdict"] is None) \
            or (escalated_flag == 1 and bool(calibrated.get("needs_human")))

        _merge_library_review_flag(final, library_review_hit)
        conn.execute("""
            INSERT OR REPLACE INTO verdicts (claim_id, nli_label, nli_confidence, nli_evidence_snippet,
                                   escalated, final_verdict, confidence, source_url,
                                   reasoning, source_directness, evidence_stance, source_tier,
                                   calibration_flags, human_reviewed, library_match)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (claim_id, nli_label, nli_conf, nli_snippet, escalated_flag,
              final["final_verdict"], final["confidence"], final["source_url"],
              final["reasoning"], final["source_directness"], final["evidence_stance"],
              final["source_tier"], final["calibration_flags"],
              0 if needs_human else 1, library_match))
        conn.commit()
        ok += 1
        flag = "🔴 İNSAN ONAYI BEKLİYOR" if needs_human else "✓"
        conf_s = f"{final['confidence']:.2f}" if final["confidence"] is not None else "—"
        print(f"  [{claim_id}] {final['final_verdict']} conf={conf_s} "
              f"tier={final['source_tier'] or '—'} cite={calibrated.get('cite_source') or '—'} "
              f"direct={final['source_directness'] or '—'} "
              f"stance={final['evidence_stance'] or '—'} (esc={escalated_flag}) {flag}")
        if final["reasoning"]:
            print(f"           {final['reasoning'][:240]}")
        if final["calibration_flags"]:
            print(f"           kalibrasyon: {final['calibration_flags']}")

    print(f"\n[factcheck] {ok} iddia işlendi, {failed} iddia hata verdi (tekrar denenecek).")
    print(f"[factcheck] ham reasoning -> {DEBUG_LOG}")

    conn.close()
    print("[factcheck] tamamlandı. Yüksek riskli iddialar human_reviewed=0 ile işaretlendi — "
          "bunları onaylamadan şüpheli listesine 'kesin' olarak yazmayın (bkz. README).")


if __name__ == "__main__":
    main()
