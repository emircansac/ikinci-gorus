"""
İzleme listesi — dashboard'dan eklenen kanallar ve tekil videolar.

data/watchlist.json:
  channels: abone olunan kanallar (pipeline periyodik tarama)
  videos:   tek tek eklenen videolar (sadece o video analiz edilir, kanal aboneliği açılmaz)

Kanal risk skoru için en az MIN_VIDEOS_FOR_CHANNEL_SCORE (5) video işlenmelidir —
bkz. pipeline/04_score_suspects.py
"""
import json
import re
from datetime import datetime, timezone
from pathlib import Path

WATCHLIST_PATH = Path(__file__).parent.parent / "data" / "watchlist.json"
MIN_VIDEOS_FOR_CHANNEL_SCORE = 5

_CHANNEL_PATTERNS = [
    re.compile(r"youtube\.com/channel/(UC[\w-]{20,})", re.I),
    re.compile(r"youtube\.com/@([\w.-]+)", re.I),
]
_VIDEO_PATTERNS = [
    re.compile(r"(?:youtube\.com/watch\?.*v=|youtu\.be/)([\w-]{11})", re.I),
    re.compile(r"youtube\.com/shorts/([\w-]{11})", re.I),
]


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _empty() -> dict:
    return {"channels": [], "videos": []}


def load_watchlist() -> dict:
    if not WATCHLIST_PATH.exists():
        return _empty()
    with open(WATCHLIST_PATH, encoding="utf-8") as f:
        data = json.load(f)
    data.setdefault("channels", [])
    data.setdefault("videos", [])
    return data


def save_watchlist(data: dict) -> None:
    WATCHLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(WATCHLIST_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def parse_channel_input(raw: str) -> str | None:
    """UC... ID, @handle veya kanal URL'si → channel_id (handle için None, API gerekir)."""
    s = raw.strip()
    if s.startswith("UC") and len(s) >= 20:
        return s
    for pat in _CHANNEL_PATTERNS:
        m = pat.search(s)
        if m:
            val = m.group(1)
            return val if val.startswith("UC") else f"@{val}"
    return None


def parse_video_input(raw: str) -> str | None:
    s = raw.strip()
    if re.fullmatch(r"[\w-]{11}", s):
        return s
    for pat in _VIDEO_PATTERNS:
        m = pat.search(s)
        if m:
            return m.group(1)
    return None


def add_channel(channel_id: str, name: str | None = None) -> dict:
    wl = load_watchlist()
    if any(c["channel_id"] == channel_id for c in wl["channels"]):
        return {"ok": False, "error": "bu kanala zaten abone olunmuş"}
    wl["channels"].append({
        "channel_id": channel_id,
        "name": name,
        "added_at": _now_iso(),
    })
    save_watchlist(wl)
    sync_channels_csv()
    return {"ok": True, "channel_id": channel_id}


def add_video(video_id: str, channel_id: str | None = None, title: str | None = None) -> dict:
    wl = load_watchlist()
    if any(v["video_id"] == video_id for v in wl["videos"]):
        return {"ok": False, "error": "bu video zaten izleme listesinde"}
    wl["videos"].append({
        "video_id": video_id,
        "channel_id": channel_id,
        "title": title,
        "added_at": _now_iso(),
        "source": "direct",
    })
    save_watchlist(wl)
    return {"ok": True, "video_id": video_id}


def remove_channel(channel_id: str) -> dict:
    wl = load_watchlist()
    before = len(wl["channels"])
    wl["channels"] = [c for c in wl["channels"] if c["channel_id"] != channel_id]
    if len(wl["channels"]) == before:
        return {"ok": False, "error": "kanal bulunamadı"}
    save_watchlist(wl)
    sync_channels_csv()
    return {"ok": True}


def remove_video(video_id: str) -> dict:
    wl = load_watchlist()
    before = len(wl["videos"])
    wl["videos"] = [v for v in wl["videos"] if v["video_id"] != video_id]
    if len(wl["videos"]) == before:
        return {"ok": False, "error": "video bulunamadı"}
    save_watchlist(wl)
    return {"ok": True}


def sync_channels_csv(path: Path | None = None) -> None:
    """Pipeline uyumluluğu: izleme listesindeki kanalları channels.csv'ye yazar."""
    import pandas as pd
    out = path or WATCHLIST_PATH.parent / "channels.csv"
    wl = load_watchlist()
    ids = [c["channel_id"] for c in wl["channels"]]
    pd.DataFrame({"channel_id": ids}).to_csv(out, index=False)


def get_watchlist_video_ids() -> list[str]:
    return [v["video_id"] for v in load_watchlist()["videos"]]
