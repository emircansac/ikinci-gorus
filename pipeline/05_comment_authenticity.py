"""
AŞAMA 5 (opsiyonel): Yorum özgünlük / bot analizi.

Kullanım:
    python pipeline/05_comment_authenticity.py [--max-comments-per-video 100]

Sadece videos tablosundaki kanalların videolarını tarar, yorumları çeker,
utils/bot_detection.py ile puanlar, comments tablosuna yazar. Ardından
channel_risk_scores.bot_comment_ratio'yu günceller (04_score_suspects.py'nin
skoruna dahil edebilmesi için).

NOT: Bu aşama YouTube kotasını en çok tüketen aşamadır (her video için 1 unit
+ her benzersiz yorumcu için toplu channels.list). Günlük değil, haftalık
çalıştırmak yeterli olabilir.
"""
import argparse
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent))
from utils.db import get_conn
from utils.youtube import get_comments, get_channel_creation_dates, QuotaError
from utils.bot_detection import score_comments

BOT_SCORE_THRESHOLD = 50  # bu ve üzeri "şüpheli yorum" sayılır (bot_comment_ratio hesabında)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-comments-per-video", type=int, default=100)
    args = ap.parse_args()

    conn = get_conn()
    videos = conn.execute("SELECT video_id, channel_id FROM videos").fetchall()
    print(f"[comments] taranacak video sayısı: {len(videos)}")

    channel_comments = {}  # channel_id -> [comment dict, ...] (tüm videoları birleştirilmiş)

    for v in videos:
        try:
            comments = get_comments(v["video_id"], max_results=args.max_comments_per_video)
        except QuotaError as e:
            print(f"  !! {v['video_id']}: {e}")
            continue
        for c in comments:
            c["video_id"] = v["video_id"]
        channel_comments.setdefault(v["channel_id"], []).extend(comments)
        print(f"  {v['video_id']}: {len(comments)} yorum")

    for channel_id, comments in channel_comments.items():
        if not comments:
            continue

        # Yorumcu profillerini toplu çek (yeni-hesap sinyali için)
        author_ids = list({c["author_channel_id"] for c in comments if c.get("author_channel_id")})
        try:
            profiles = get_channel_creation_dates(author_ids)
        except QuotaError as e:
            print(f"  !! yorumcu profilleri çekilemedi: {e}")
            profiles = {}

        scored = score_comments(comments, profiles)

        for c in scored:
            conn.execute("""
                INSERT OR REPLACE INTO comments
                    (comment_id, video_id, channel_id, author_channel_id, author_name,
                     text, published_at, like_count, bot_score, bot_flags)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (c["comment_id"], c["video_id"], channel_id, c.get("author_channel_id"),
                  c.get("author_name"), c.get("text"), c.get("published_at"),
                  c.get("like_count", 0), c["bot_score"], c["bot_flags"]))

        suspicious = sum(1 for c in scored if c["bot_score"] >= BOT_SCORE_THRESHOLD)
        ratio = suspicious / len(scored) if scored else 0
        conn.execute("""
            UPDATE channel_risk_scores SET bot_comment_ratio = ?
            WHERE channel_id = ?
        """, (round(ratio, 3), channel_id))
        # Kanal henüz channel_risk_scores'da yoksa (04. aşama hiç çalışmadıysa) sessizce geç —
        # 04_score_suspects.py bir sonraki çalıştırmada bu veriyi zaten okuyacak.

        conn.commit()
        flag = "🔴" if ratio > 0.3 else ("🟡" if ratio > 0.1 else "🟢")
        print(f"  [{channel_id}] {len(scored)} yorum, %{ratio*100:.0f} şüpheli {flag}")

    conn.close()
    print("[comments] tamamlandı. Şüpheli oranı %30+ olan kanallardaki yorumları "
          "comments tablosundan bot_flags='duplicate,burst' filtresiyle inceleyin.")


if __name__ == "__main__":
    main()
