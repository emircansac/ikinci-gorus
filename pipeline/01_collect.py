"""
AŞAMA 1: YouTube'dan veri çekme.

Kullanım:
    python pipeline/01_collect.py --channels data/channels.csv --max-videos 15

channels.csv en az bir 'channel_id' (UCxxxx) sütunu içermeli.
Mevcut Excel veritabanınızdan (ID sütunu) doğrudan türetebilirsiniz:
    python -c "import pandas as pd; pd.read_excel('kanallar.xlsx')[['ID']].rename(columns={'ID':'channel_id'}).to_csv('data/channels.csv', index=False)"
"""
import argparse
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent))
import pandas as pd
from utils.db import get_conn, init_db
from utils.youtube import collect_channel, collect_watchlist_videos, QuotaError
from utils.watchlist import load_watchlist, get_watchlist_video_ids, sync_channels_csv


def upsert_channel(conn, stats: dict):
    conn.execute("""
        INSERT INTO channels (channel_id, name, description, subscribers, total_videos, total_views, last_checked_at)
        VALUES (:channel_id, :name, :description, :subscribers, :total_videos, :total_views, datetime('now'))
        ON CONFLICT(channel_id) DO UPDATE SET
            subscribers=excluded.subscribers,
            total_videos=excluded.total_videos,
            total_views=excluded.total_views,
            last_checked_at=datetime('now')
    """, stats)
    conn.execute("""
        INSERT INTO channel_snapshots (channel_id, subscribers, total_videos, total_views)
        VALUES (?, ?, ?, ?)
    """, (stats["channel_id"], stats["subscribers"], stats["total_videos"], stats["total_views"]))


def upsert_video(conn, channel_id: str, v: dict):
    """
    NOT: transcript None ise (zaten DB'de vardı ve tekrar indirilmedi) mevcut
    transkripti SIFIRLAMAMAK için COALESCE kullanıyoruz. Bunu unutursanız her
    çalıştırma bir öncekinin transkriptini NULL ile eziyor.
    """
    conn.execute("""
        INSERT INTO videos (video_id, channel_id, title, published_at, transcript, transcript_lang, watch_source)
        VALUES (:video_id, :channel_id, :title, :published_at, :transcript, :transcript_lang, :watch_source)
        ON CONFLICT(video_id) DO UPDATE SET
            transcript=COALESCE(excluded.transcript, videos.transcript),
            transcript_lang=COALESCE(excluded.transcript_lang, videos.transcript_lang),
            title=COALESCE(excluded.title, videos.title)
    """, {
        **v, "channel_id": channel_id,
        "transcript": v.get("transcript"), "transcript_lang": v.get("transcript_lang"),
        "watch_source": v.get("watch_source", "channel"),
    })


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--channels", default="data/channels.csv", help="channel_id sütunu içeren CSV")
    ap.add_argument("--watchlist", action="store_true", help="data/watchlist.json'dan kanal + tekil videoları topla")
    ap.add_argument("--max-videos", type=int, default=15)
    ap.add_argument("--no-transcripts", action="store_true", help="sadece istatistik çek, transkript atla (hızlı tarama)")
    args = ap.parse_args()

    init_db()
    conn = get_conn()

    # Zaten transkripti olan videoları bul — aynısını tekrar indirmemek için.
    existing_transcripts = {r["video_id"] for r in
                             conn.execute("SELECT video_id FROM videos WHERE transcript IS NOT NULL").fetchall()}

    channel_ids = []
    if args.watchlist:
        wl = load_watchlist()
        channel_ids = [c["channel_id"] for c in wl["channels"]]
        sync_channels_csv()
        print(f"[collect] izleme listesi: {len(channel_ids)} kanal, {len(wl['videos'])} tekil video")
    elif args.channels:
        df = pd.read_csv(args.channels)
        df = df.dropna(subset=["channel_id"])
        channel_ids = [str(row["channel_id"]).strip() for _, row in df.iterrows() if str(row["channel_id"]).strip()]

    for cid in channel_ids:
        print(f"[collect] {cid} ...")
        try:
            data = collect_channel(cid, max_videos=args.max_videos, fetch_transcripts=not args.no_transcripts,
                                    already_have_transcript=existing_transcripts)
        except QuotaError as e:
            print(f"  !! API hatası/kota: {e}")
            continue
        except Exception as e:
            # Tek bir kanalın çökmesi tüm taramayı durdurmasın (ağ hatası, silinmiş kanal vb.)
            print(f"  !! beklenmeyen hata, bu kanal atlanıyor: {e}")
            continue
        if not data:
            print("  !! kanal bulunamadı")
            continue

        upsert_channel(conn, {k: data[k] for k in ("channel_id", "name", "description", "subscribers", "total_videos", "total_views")})
        for v in data["videos"]:
            v.setdefault("watch_source", "channel")
            upsert_video(conn, cid, v)
        conn.commit()
        print(f"  ✓ {data['name']} — {len(data['videos'])} video, {data['subscribers']} abone")

    # Tekil video izleme listesi — sadece o video, kanal aboneliği açmadan
    direct_ids = get_watchlist_video_ids()
    if direct_ids:
        print(f"[collect] tekil videolar ({len(direct_ids)}) ...")
        for item in collect_watchlist_videos(direct_ids, fetch_transcripts=not args.no_transcripts,
                                            already_have_transcript=existing_transcripts):
            ch = item["channel"]
            if ch.get("channel_id"):
                upsert_channel(conn, {
                    "channel_id": ch["channel_id"],
                    "name": ch.get("name") or ch["channel_id"],
                    "description": ch.get("description") or "",
                    "subscribers": ch.get("subscribers") or 0,
                    "total_videos": ch.get("total_videos") or 0,
                    "total_views": ch.get("total_views") or 0,
                })
            v = item["video"]
            upsert_video(conn, v["channel_id"], v)
            conn.commit()
            print(f"  ✓ tekil video {v['video_id']} — {v.get('title', '')[:50]}")

    conn.close()
    print("[collect] tamamlandı.")


if __name__ == "__main__":
    main()
