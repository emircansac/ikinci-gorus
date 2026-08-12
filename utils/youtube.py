"""
AŞAMA 1: YouTube'dan veri çekme.

Üç kaynak kullanılır:
  1. YouTube Data API v3   -> kanal istatistikleri, video listesi (kota harcar, güvenilir)
  2. RSS feed              -> yeni video tespiti (kota harcamaz, ~gerçek zamanlı)
  3. youtube-transcript-api-> video transkriptleri (Türkçe altyazı varsa çeker)

Gerekli ortam değişkeni: YOUTUBE_API_KEY
(https://console.cloud.google.com/apis/credentials -> YouTube Data API v3'ü etkinleştirin)
"""
import os
import time
import requests
import xml.etree.ElementTree as ET

YOUTUBE_API_KEY = os.environ.get("YOUTUBE_API_KEY")
API_BASE = "https://www.googleapis.com/youtube/v3"


class QuotaError(Exception):
    pass


def resolve_channel_id(channel_id_or_handle: str) -> str | None:
    """UC... ID veya @handle → channel_id."""
    if not YOUTUBE_API_KEY:
        raise RuntimeError("YOUTUBE_API_KEY tanımlı değil")
    if channel_id_or_handle.startswith("UC") and len(channel_id_or_handle) >= 20:
        return channel_id_or_handle
    if channel_id_or_handle.startswith("@"):
        handle = channel_id_or_handle[1:]
        params = {"part": "id", "forHandle": handle, "key": YOUTUBE_API_KEY}
        r = requests.get(f"{API_BASE}/channels", params=params, timeout=15)
        data = r.json()
        if data.get("items"):
            return data["items"][0]["id"]
    return None


def get_channel_stats(channel_id: str) -> dict:
    """channels.list ile güncel istatistikleri çeker. 1 unit harcar."""
    if not YOUTUBE_API_KEY:
        raise RuntimeError("YOUTUBE_API_KEY tanımlı değil (.env dosyasına ekleyin)")
    params = {
        "part": "snippet,statistics,contentDetails",
        "id": channel_id,
        "key": YOUTUBE_API_KEY,
    }
    r = requests.get(f"{API_BASE}/channels", params=params, timeout=15)
    data = r.json()
    if "error" in data:
        raise QuotaError(data["error"].get("message", str(data["error"])))
    items = data.get("items", [])
    if not items:
        return {}
    it = items[0]
    stats = it["statistics"]
    return {
        "channel_id": channel_id,
        "name": it["snippet"]["title"],
        "description": it["snippet"].get("description", ""),
        "subscribers": int(stats.get("subscriberCount", 0)),
        "total_videos": int(stats.get("videoCount", 0)),
        "total_views": int(stats.get("viewCount", 0)),
        "uploads_playlist": it["contentDetails"]["relatedPlaylists"]["uploads"],
    }


def get_recent_videos_via_api(uploads_playlist_id: str, max_results: int = 25) -> list[dict]:
    """playlistItems.list ile yükleme listesinden son videoları çeker."""
    videos = []
    params = {
        "part": "snippet",
        "playlistId": uploads_playlist_id,
        "maxResults": min(max_results, 50),
        "key": YOUTUBE_API_KEY,
    }
    r = requests.get(f"{API_BASE}/playlistItems", params=params, timeout=15)
    data = r.json()
    if "error" in data:
        raise QuotaError(data["error"].get("message", str(data["error"])))
    for it in data.get("items", []):
        sn = it["snippet"]
        videos.append({
            "video_id": sn["resourceId"]["videoId"],
            "title": sn["title"],
            "published_at": sn["publishedAt"],
        })
    return videos


def get_recent_videos_via_rss(channel_id: str) -> list[dict]:
    """
    Kota harcamadan yeni video tespiti. API'den daha az detaylı (~son 15 video)
    ama günlük/saatlik hızlı kontrol için ideal — 26 kanalı dakikada tarayabilirsiniz.
    """
    url = f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
    r = requests.get(url, timeout=15)
    r.raise_for_status()
    ns = {
        "atom": "http://www.w3.org/2005/Atom",
        "yt": "http://www.youtube.com/xml/schemas/2015",
    }
    root = ET.fromstring(r.text)
    videos = []
    for entry in root.findall("atom:entry", ns):
        videos.append({
            "video_id": entry.find("yt:videoId", ns).text,
            "title": entry.find("atom:title", ns).text,
            "published_at": entry.find("atom:published", ns).text,
        })
    return videos


def _format_with_timestamps(fetched) -> str:
    """
    Ham chunk listesini ('text','start','duration' sözlükleri) [Ns] etiketli tek metne çevirir.
    Bu KRİTİK: zaman damgasını burada atarsak, Claude'a düz metin gider ve
    claim_extraction'daki 'timestamp_sec' alanı Claude'un UYDURDUĞU bir sayı olur.
    youtube-transcript-api sürümüne göre chunk bir dict ya da FetchedTranscriptSnippet
    nesnesi olabilir; ikisini de destekliyoruz.
    """
    parts = []
    for chunk in fetched:
        if isinstance(chunk, dict):
            start, text = chunk.get("start", 0), chunk.get("text", "")
        else:  # newer versions return objects with attributes
            start, text = getattr(chunk, "start", 0), getattr(chunk, "text", "")
        parts.append(f"[{int(start)}s] {text}")
    return " ".join(parts)


def get_transcript(video_id: str, languages=("tr", "en")) -> tuple[str | None, str | None]:
    """
    youtube-transcript-api ile transkript çeker. Zaman damgalarını [Ns] formatında
    metne gömer (yoksa Claude timestamp_sec'i uydurmak zorunda kalır).

    NOT (sürüm uyumluluğu): youtube-transcript-api 1.0+ sürümünde API instance-based
    hale geldi (YouTubeTranscriptApi().fetch(...) / .list(...)). Bu fonksiyon önce eski
    (0.6.x, sınıf metodu) API'yi dener, başarısız olursa yeni API'ye düşer. Kurulu
    sürümünüzü `pip show youtube-transcript-api` ile kontrol edin.
    """
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
    except ImportError:
        raise RuntimeError("pip install youtube-transcript-api gerekli")

    # --- Eski API (<=0.6.x): sınıf metodu, list_transcripts ---------------
    if hasattr(YouTubeTranscriptApi, "list_transcripts"):
        try:
            transcript_list = YouTubeTranscriptApi.list_transcripts(video_id)
            for lang in languages:
                try:
                    t = transcript_list.find_transcript([lang])
                    return _format_with_timestamps(t.fetch()), lang
                except Exception:
                    continue
            for t in transcript_list:
                return _format_with_timestamps(t.fetch()), t.language_code
            return None, None
        except Exception as e:
            print(f"  [transcript] {video_id}: eski API başarısız ({e}), yeni API deneniyor")

    # --- Yeni API (1.0+): instance-based -----------------------------------
    try:
        api = YouTubeTranscriptApi()
        for lang in languages:
            try:
                fetched = api.fetch(video_id, languages=[lang])
                return _format_with_timestamps(fetched), lang
            except Exception:
                continue
        fetched = api.fetch(video_id)
        return _format_with_timestamps(fetched), None
    except Exception as e:
        print(f"  [transcript] {video_id}: alınamadı ({e})")
        return None, None


def get_comments(video_id: str, max_results: int = 100) -> list[dict]:
    """
    commentThreads.list ile üst düzey yorumları çeker (yanıtlar hariç, bot analizi
    için yeterli). 1 sayfa = 1 unit kota. max_results 100'ü aşarsa sayfalama gerekir
    (bu prototipte tek sayfa ile sınırlı tutuldu — genişletmek isterseniz
    'nextPageToken' ile döngü ekleyin).
    """
    params = {
        "part": "snippet",
        "videoId": video_id,
        "maxResults": min(max_results, 100),
        "order": "time",
        "textFormat": "plainText",
        "key": YOUTUBE_API_KEY,
    }
    r = requests.get(f"{API_BASE}/commentThreads", params=params, timeout=15)
    data = r.json()
    if "error" in data:
        # Yorumlar kapalıysa (commentsDisabled) burada hata döner — bu normaldir, kanalın
        # "botlu görünme" riskiyle ilgisi yok, sadece o video için veri yok demektir.
        msg = data["error"].get("message", str(data["error"]))
        if "disabled" in msg.lower():
            return []
        raise QuotaError(msg)

    comments = []
    for item in data.get("items", []):
        top = item["snippet"]["topLevelComment"]["snippet"]
        comments.append({
            "comment_id": item["snippet"]["topLevelComment"]["id"],
            "author_channel_id": top.get("authorChannelId", {}).get("value"),
            "author_name": top.get("authorDisplayName"),
            "text": top.get("textDisplay", ""),
            "published_at": top.get("publishedAt"),
            "like_count": top.get("likeCount", 0),
        })
    return comments


def get_channel_creation_dates(channel_ids: list[str]) -> dict:
    """
    Yorumcuların kanal oluşturma tarihini toplu çeker (bot tespiti için: çok yeni
    açılmış hesaplar şüpheli). channels.list bir çağrıda 50 ID'ye kadar kabul eder.
    """
    out = {}
    for i in range(0, len(channel_ids), 50):
        batch = channel_ids[i:i + 50]
        params = {"part": "snippet,statistics", "id": ",".join(batch), "key": YOUTUBE_API_KEY}
        r = requests.get(f"{API_BASE}/channels", params=params, timeout=15)
        data = r.json()
        if "error" in data:
            continue
        for it in data.get("items", []):
            out[it["id"]] = {
                "channel_created_at": it["snippet"].get("publishedAt"),
                "public_video_count": int(it.get("statistics", {}).get("videoCount", 0)),
            }
        time.sleep(0.2)
    return out


def collect_channel(channel_id: str, max_videos: int = 25, fetch_transcripts: bool = True,
                     already_have_transcript: set | None = None) -> dict:
    """
    Tek bir kanal için tam toplama: istatistik + video listesi + transkriptler.

    already_have_transcript: DB'de zaten transkripti kayıtlı video_id'lerin kümesi.
    Bu geçilmezse her çalıştırmada AYNI videonun transkripti tekrar tekrar indirilir
    (gereksiz zaman + rate-limit riski) — pipeline/01_collect.py bu kümeyi DB'den
    doldurup buraya geçirir.
    """
    stats = get_channel_stats(channel_id)
    if not stats:
        return {}
    time.sleep(0.2)  # kota dostu tempo
    videos = get_recent_videos_via_api(stats["uploads_playlist"], max_results=max_videos)
    already_have_transcript = already_have_transcript or set()

    if fetch_transcripts:
        for v in videos:
            if v["video_id"] in already_have_transcript:
                v["transcript"], v["transcript_lang"] = None, None  # 01_collect.py bunu UPDATE etmeyecek
                continue
            text, lang = get_transcript(v["video_id"])
            v["transcript"] = text
            v["transcript_lang"] = lang
            time.sleep(0.3)

    stats["videos"] = videos
    return stats


def get_video_metadata(video_id: str) -> dict | None:
    """Tek video metadata (title, channel_id, published_at). videos.list — 1 unit."""
    if not YOUTUBE_API_KEY:
        raise RuntimeError("YOUTUBE_API_KEY tanımlı değil")
    params = {"part": "snippet", "id": video_id, "key": YOUTUBE_API_KEY}
    r = requests.get(f"{API_BASE}/videos", params=params, timeout=15)
    data = r.json()
    if "error" in data:
        raise QuotaError(data["error"].get("message", str(data["error"])))
    items = data.get("items", [])
    if not items:
        return None
    sn = items[0]["snippet"]
    return {
        "video_id": video_id,
        "channel_id": sn["channelId"],
        "channel_title": sn.get("channelTitle", ""),
        "title": sn["title"],
        "published_at": sn["publishedAt"],
    }


def collect_single_video(video_id: str, fetch_transcript: bool = True) -> dict | None:
    """
    İzleme listesine tekil eklenen video — sadece bu video çekilir, kanalın diğer
    videoları taranmaz (kanal aboneliği açılmaz).
    """
    meta = get_video_metadata(video_id)
    if not meta:
        return None
    time.sleep(0.2)
    ch_stats = get_channel_stats(meta["channel_id"])
    if not ch_stats:
        ch_stats = {
            "channel_id": meta["channel_id"],
            "name": meta.get("channel_title") or meta["channel_id"],
            "description": "", "subscribers": 0, "total_videos": 0, "total_views": 0,
        }
    v = dict(meta)
    if fetch_transcript:
        text, lang = get_transcript(video_id)
        v["transcript"] = text
        v["transcript_lang"] = lang
        time.sleep(0.3)
    else:
        v["transcript"] = None
        v["transcript_lang"] = None
    v["watch_source"] = "direct"
    return {"channel": {k: ch_stats[k] for k in ("channel_id", "name", "description", "subscribers", "total_videos", "total_views")}, "video": v}


def collect_watchlist_videos(video_ids: list[str], fetch_transcripts: bool = True,
                              already_have_transcript: set | None = None) -> list[dict]:
    """İzleme listesindeki tekil videoları toplar."""
    already_have_transcript = already_have_transcript or set()
    out = []
    for vid in video_ids:
        if vid in already_have_transcript:
            meta = get_video_metadata(vid)
            if meta:
                meta["transcript"] = None
                meta["transcript_lang"] = None
                meta["watch_source"] = "direct"
                ch = get_channel_stats(meta["channel_id"]) or {}
                out.append({"channel": ch, "video": meta})
            continue
        item = collect_single_video(vid, fetch_transcript=fetch_transcripts)
        if item:
            out.append(item)
    return out
