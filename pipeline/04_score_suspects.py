"""
AŞAMA 4: Kanal bazlı risk skoru + şüpheli listesi.

Video/iddia bazlı değil, KANAL bazlı puanlama yapılır çünkü tespit ettiğimiz gibi
aynı kanal hem sorumlu hem agresif yanıltıcı içerik üretebiliyor — kanalın genel
paternine bakmak gerekiyor.

Skor bileşenleri (0-100, toplam):
  - kontrol edilmiş iddiaların ort. şüphe skoru     (ağırlık: 40)
  - kontrol edilmişlerde yüksek şüphe (≥75) oranı   (ağırlık: 10)
  - henüz kontrol edilmemiş high initial_risk        (ağırlık: 15)
  - genel yüksek risk yoğunluğu                       (ağırlık: 10)
  - satış hunisi / AI-persona / büyüme / bot yorum  (ağırlık: 15+5+10+15)
  Fact-check kapsamı <%20 ise kademe en fazla 'incele' olur.

Kullanım:
    python pipeline/04_score_suspects.py --export data/suspects.csv
"""
import argparse
import sys
import re
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent))
import pandas as pd
from utils.db import get_conn
from utils.watchlist import MIN_VIDEOS_FOR_CHANNEL_SCORE
from utils.suspicion import compute_channel_risk
from utils.extraction_store import ACTIVE_CLAIM_WHERE

FUNNEL_PATTERNS = [
    r"ücretsiz rehber", r"sabit yorum", r"linke tıkla", r"profil.*link",
    r"kanal.*link", r"özel program", r"detaylı bilgi için", r"whatsapp",
    r"bio.*link", r"hemen tıkla", r"kaçırma", r"sınırlı say", r"indirim kodu",
    r"dm.*at", r"özel mesaj at",
]
AI_PERSONA_PATTERNS = [
    r"yapay zeka.*sağlık eğitimcisi", r"ai.*sağlık eğitimcisi", r"dijital.*karakter",
    r"teknolojiyle desteklenen.*karakter", r"yapay zeka.*karakter",
]


def _turkish_lower(text: str) -> str:
    """
    Python'un varsayılan str.lower() Türkçe büyük İ harfini doğru çevirmez:
    'İ'.lower() -> 'i̇' (i + combining dot, 2 karakter), düz 'i' değil. Bu da
    regex eşleşmesini sessizce kırar (ör. büyük harfle başlayan cümlelerde
    "İlaç", "İçindeki" gibi kelimeler anahtar kelime taramasından kaçabilir).
    Türkçe'ye özgü İ/I çiftlerini elle çeviriyoruz.
    """
    return text.replace("İ", "i").replace("I", "ı").lower()


def keyword_flag(text: str, patterns: list[str]) -> bool:
    if not text:
        return False
    text_low = _turkish_lower(text)
    return any(re.search(p, text_low) for p in patterns)


def compute_growth_anomaly(conn, channel_id: str, threshold_pct: float = 20.0) -> bool:
    """Ardışık iki snapshot arasında abone sayısı %threshold'dan fazla arttıysa flagle."""
    rows = conn.execute("""
        SELECT subscribers, checked_at FROM channel_snapshots
        WHERE channel_id = ? ORDER BY checked_at ASC
    """, (channel_id,)).fetchall()
    for i in range(1, len(rows)):
        prev, curr = rows[i - 1]["subscribers"], rows[i]["subscribers"]
        if prev and prev > 0 and (curr - prev) / prev * 100 > threshold_pct:
            return True
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--export", default="data/suspects.csv")
    args = ap.parse_args()

    conn = get_conn()
    channels = conn.execute("SELECT * FROM channels").fetchall()

    results = []
    for ch in channels:
        cid = ch["channel_id"]
        claim_rows = conn.execute(f"""
            SELECT c.claim_id, c.category, c.initial_risk, vr.final_verdict, vr.human_reviewed
            FROM claims c LEFT JOIN verdicts vr ON vr.claim_id = c.claim_id
            WHERE c.channel_id = ? AND c.{ACTIVE_CLAIM_WHERE}
        """, (cid,)).fetchall()

        total = len(claim_rows)
        false_or_disputed = sum(1 for r in claim_rows if r["final_verdict"] in ("yanlış", "tartışmalı"))
        high_risk = sum(1 for r in claim_rows if r["initial_risk"] == "high")
        pending_human = sum(1 for r in claim_rows if r["human_reviewed"] == 0 and r["final_verdict"] is not None)

        transcripts = conn.execute("SELECT transcript FROM videos WHERE channel_id=?", (cid,)).fetchall()
        full_text = " ".join((t["transcript"] or "") for t in transcripts) + " " + (ch["description"] or "")

        analyzed_videos = conn.execute("""
            SELECT COUNT(*) AS n FROM videos
            WHERE channel_id = ? AND claims_extracted_at IS NOT NULL
        """, (cid,)).fetchone()["n"]

        funnel = keyword_flag(full_text, FUNNEL_PATTERNS)
        ai_persona = keyword_flag(ch["description"] or "", AI_PERSONA_PATTERNS)
        growth_anomaly = compute_growth_anomaly(conn, cid)

        existing = conn.execute("SELECT bot_comment_ratio FROM channel_risk_scores WHERE channel_id=?", (cid,)).fetchone()
        bot_ratio = existing["bot_comment_ratio"] if existing and existing["bot_comment_ratio"] is not None else 0.0

        if analyzed_videos < MIN_VIDEOS_FOR_CHANNEL_SCORE:
            tier = "yetersiz_veri"
            score = None
            scored_claims = 0
            fact_check_coverage = 0.0
            avg_suspicion = None
        else:
            rows_as_dicts = [dict(r) for r in claim_rows]
            score, tier, risk_meta = compute_channel_risk(
                rows_as_dicts,
                funnel_flag=funnel,
                ai_persona_flag=ai_persona,
                growth_anomaly_flag=growth_anomaly,
                bot_comment_ratio=bot_ratio,
            )
            scored_claims = risk_meta["scored_claims"]
            fact_check_coverage = risk_meta["fact_check_coverage"]
            avg_suspicion = risk_meta["avg_suspicion"]

        conn.execute("""
            INSERT INTO channel_risk_scores
                (channel_id, total_claims, false_claims, high_risk_claims,
                 funnel_flag, ai_persona_flag, growth_anomaly_flag, bot_comment_ratio, risk_score, risk_tier)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(channel_id) DO UPDATE SET
                total_claims=excluded.total_claims, false_claims=excluded.false_claims,
                high_risk_claims=excluded.high_risk_claims, funnel_flag=excluded.funnel_flag,
                ai_persona_flag=excluded.ai_persona_flag, growth_anomaly_flag=excluded.growth_anomaly_flag,
                bot_comment_ratio=excluded.bot_comment_ratio,
                risk_score=excluded.risk_score, risk_tier=excluded.risk_tier,
                computed_at=datetime('now')
        """, (cid, total, false_or_disputed, high_risk, int(funnel), int(ai_persona), int(growth_anomaly),
              bot_ratio, round(score, 1) if score is not None else None, tier))

        results.append({
            "channel_id": cid, "name": ch["name"],
            "risk_score": round(score, 1) if score is not None else None,
            "risk_tier": tier,
            "analyzed_videos": analyzed_videos,
            "min_videos_for_score": MIN_VIDEOS_FOR_CHANNEL_SCORE,
            "total_claims": total, "false_or_disputed": false_or_disputed, "high_risk_claims": high_risk,
            "scored_claims": scored_claims if analyzed_videos >= MIN_VIDEOS_FOR_CHANNEL_SCORE else 0,
            "fact_check_coverage": fact_check_coverage if analyzed_videos >= MIN_VIDEOS_FOR_CHANNEL_SCORE else 0.0,
            "avg_suspicion": avg_suspicion,
            "funnel_flag": funnel, "ai_persona_flag": ai_persona, "growth_anomaly_flag": growth_anomaly,
            "bot_comment_ratio": bot_ratio, "pending_human_review": pending_human,
        })

    conn.commit()
    conn.close()

    df = pd.DataFrame(results)
    if "risk_score" in df.columns:
        df = df.sort_values("risk_score", ascending=False, na_position="last")
    Path(args.export).parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(args.export, index=False)
    print(df.to_string(index=False))
    print(f"\n[score] Şüpheli listesi kaydedildi -> {args.export}")
    print(f"[score] Kanal puanı için en az {MIN_VIDEOS_FOR_CHANNEL_SCORE} analiz edilmiş video gerekir (yetersiz_veri = henüz erken).")
    print("[score] NOT: pending_human_review > 0 olan kanallardaki 'acil' etiketi "
          "insan onayından geçmeden nihai kabul edilmemeli.")


if __name__ == "__main__":
    main()
