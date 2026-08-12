"""
AŞAMA 2: Transkriptlerden atomik iddiaları çıkarma (Claude API).

Kullanım:
    python pipeline/02_extract_claims.py [--limit 50]

Sadece henüz iddia çıkarılmamış (claims tablosunda karşılığı olmayan) videoları işler,
bu yüzden script'i tekrar tekrar çalıştırmak güvenlidir (idempotent).
"""
import argparse
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent))
from utils.db import get_conn
from utils.claude_client import extract_claims


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=100)
    args = ap.parse_args()

    conn = get_conn()
    # claims_extracted_at IS NULL kullanıyoruz (claim sayısına değil) — bir video
    # gerçekten 0 iddia içerse bile burası set edilir, yoksa o video her
    # çalıştırmada tekrar tekrar (ve tekrar ücretli) işlenir.
    rows = conn.execute("""
        SELECT video_id, channel_id, transcript
        FROM videos
        WHERE transcript IS NOT NULL
          AND claims_extracted_at IS NULL
        LIMIT ?
    """, (args.limit,)).fetchall()

    print(f"[claims] işlenecek video sayısı: {len(rows)}")
    ok, failed = 0, 0
    for row in rows:
        print(f"  -> {row['video_id']}")
        try:
            claims = extract_claims(row["transcript"])
        except Exception as e:
            # Tek videonun API hatası (rate limit, ağ) tüm batch'i durdurmasın.
            # claims_extracted_at İŞARETLENMEZ, bir sonraki çalıştırmada tekrar denenir.
            print(f"     !! hata, bu video atlandı (bir sonraki çalıştırmada tekrar denenecek): {e}")
            failed += 1
            continue

        for c in claims:
            conn.execute("""
                INSERT INTO claims (video_id, channel_id, timestamp_sec, claim_text, search_query_en, category, initial_risk)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (row["video_id"], row["channel_id"], c.get("timestamp_sec"),
                  c["claim_text"], c.get("search_query_en"), c.get("category", "diğer"), c.get("initial_risk", "medium")))
        conn.execute("UPDATE videos SET claims_extracted_at = datetime('now') WHERE video_id = ?", (row["video_id"],))
        conn.commit()
        print(f"     {len(claims)} iddia çıkarıldı")
        ok += 1

    conn.close()
    print(f"[claims] tamamlandı. {ok} video işlendi, {failed} video hata verdi (tekrar denenecek).")


if __name__ == "__main__":
    main()
