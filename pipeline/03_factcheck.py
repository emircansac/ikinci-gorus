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

Kullanım:
    python pipeline/03_factcheck.py [--limit 100] [--skip-nli]

--skip-nli: HF modelini kurmadıysanız (torch/transformers ağır), doğrudan her
            iddiayı Claude+web_search'e gönderir. Daha pahalı ama kurulum gerektirmez.
"""
import argparse
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent))
from utils.db import get_conn
from utils.claude_client import escalate_factcheck

# "tanı" eklendi: yanlış teşhis iddiaları (ör. "sertleşme sorununuzun kaynağı X'tir")
# risk etiketi 'medium' bile olsa insan onayı gerektirmeli — bir önceki sürümde
# sadece initial_risk='high' ya da bu üç kategoriye bakılıyordu, 'tanı' unutulmuştu.
HIGH_RISK_HUMAN_REVIEW_CATEGORIES = {"tedavi", "doz", "mucize-ürün", "tanı"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=200)
    ap.add_argument("--skip-nli", action="store_true")
    args = ap.parse_args()

    conn = get_conn()
    rows = conn.execute("""
        SELECT c.claim_id, c.claim_text, c.search_query_en, c.category, c.initial_risk
        FROM claims c
        LEFT JOIN verdicts vr ON vr.claim_id = c.claim_id
        WHERE vr.claim_id IS NULL
        LIMIT ?
    """, (args.limit,)).fetchall()

    print(f"[factcheck] işlenecek iddia sayısı: {len(rows)}")

    nli_check = should_escalate = None
    if not args.skip_nli:
        from utils.nli import nli_check as _nli_check, should_escalate as _should_escalate
        from utils.evidence_retrieval import retrieve_pubmed_evidence
        nli_check, should_escalate = _nli_check, _should_escalate

    ok, failed = 0, 0
    for row in rows:
        claim_id, claim_text, search_query_en, category, initial_risk = (
            row["claim_id"], row["claim_text"], row["search_query_en"], row["category"], row["initial_risk"])
        nli_label, nli_conf, nli_snippet = None, None, None
        no_evidence_found = False
        do_escalate = True

        if not args.skip_nli:
            # search_query_en'i geçiyoruz — yoksa (eski kayıt) fonksiyon kendi uyarısını basar.
            evidence = retrieve_pubmed_evidence(claim_text, search_query_en=search_query_en)
            if evidence:
                # DÜZELTME: önceki sürüm sadece başlıkları birleştiriyordu (e["title"]).
                # Başlık, "destekliyor/çürütüyor" ayrımı için neredeyse hiç bilgi taşımaz —
                # şimdi EFetch ile çekilen GERÇEK ÖZET metni (abstract) de dahil ediliyor.
                evidence_text = " ".join(f"{e['title']} {e.get('abstract', '')}".strip() for e in evidence)
                nli_result = nli_check(claim_text, evidence_text)
                nli_label, nli_conf = nli_result["nli_label"], nli_result["nli_confidence"]
                nli_snippet = evidence_text[:500]
                do_escalate = should_escalate(nli_result, initial_risk)
            else:
                # DÜZELTME (önceki tur): önceki sürüm burada evidence_text'i claim_text'e düşürüyordu,
                # yani iddia kendisiyle karşılaştırılıp yanlışlıkla "destekliyor" çıkabiliyordu.
                # Kanıt bulunamadıysa NLI'ya hiç girmeden doğrudan Claude+web_search'e yollanır.
                no_evidence_found = True
                do_escalate = True
                nli_snippet = "(PubMed'de ilgili kanıt bulunamadı — otomatik escalate edildi)"

        final = {"final_verdict": None, "confidence": None, "source_url": None}
        escalated_flag = 0
        parse_failed = False
        try:
            if do_escalate:
                escalated_flag = 1
                result = escalate_factcheck(claim_text)
                final["final_verdict"] = result.get("final_verdict")
                final["confidence"] = result.get("confidence")
                final["source_url"] = result.get("source_url")
                parse_failed = bool(result.get("parse_failed"))
            else:
                # ucuz filtre yeterince güvenliydi, LLM'e gitmeden NLI etiketini kullan
                final["final_verdict"] = {"SUPPORTS": "doğrulanmış", "REFUTES": "yanlış"}.get(nli_label, "belirsiz")
                final["confidence"] = nli_conf
        except Exception as e:
            # Tek iddianın API hatası tüm batch'i durdurmasın; bu satır verdicts'e hiç
            # yazılmaz, bir sonraki çalıştırmada tekrar denenir (WHERE vr.claim_id IS NULL).
            print(f"  [{claim_id}] !! hata, atlandı (tekrar denenecek): {e}")
            failed += 1
            continue

        # parse_failed veya no_evidence_found ise insan onayı olmadan asla "temiz" sayılmaz.
        needs_human = (category in HIGH_RISK_HUMAN_REVIEW_CATEGORIES) or (initial_risk == "high") \
            or parse_failed or (final["final_verdict"] is None)

        conn.execute("""
            INSERT INTO verdicts (claim_id, nli_label, nli_confidence, nli_evidence_snippet,
                                   escalated, final_verdict, confidence, source_url, human_reviewed)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (claim_id, nli_label, nli_conf, nli_snippet, escalated_flag,
              final["final_verdict"], final["confidence"], final["source_url"],
              0 if needs_human else 1))
        conn.commit()
        ok += 1
        flag = "🔴 İNSAN ONAYI BEKLİYOR" if needs_human else "✓"
        print(f"  [{claim_id}] {final['final_verdict']} (esc={escalated_flag}, no_evidence={no_evidence_found}) {flag}")

    print(f"\n[factcheck] {ok} iddia işlendi, {failed} iddia hata verdi (tekrar denenecek).")

    conn.close()
    print("[factcheck] tamamlandı. Yüksek riskli iddialar human_reviewed=0 ile işaretlendi — "
          "bunları onaylamadan şüpheli listesine 'kesin' olarak yazmayın (bkz. README).")


if __name__ == "__main__":
    main()
