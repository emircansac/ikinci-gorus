"""
Kanala abone ol — önce güncel video sayısını çek, maliyet tahmini göster, ONAY BEKLE.

Onay gelmeden collect / extract / fact-check başlamaz.
Tek onay üç adımı birden başlatır: collect (liste+transkript) + extract + fact-check.
Yeni video yoksa ve bekleyen iş varsa, onay metni bunu açıkça söyler.

Kullanım:
    python pipeline/20_subscribe_channel.py --channel-id UCXhDI7n_iC4J9jR3GYJKkcQ
    python pipeline/20_subscribe_channel.py --channel-url 'https://www.youtube.com/channel/UCXhDI7n_iC4J9jR3GYJKkcQ'
    python pipeline/20_subscribe_channel.py --channel-id UCXhDI7n_iC4J9jR3GYJKkcQ --dry-run
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.append(str(ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass

from utils.db import get_conn
from utils.watchlist import add_channel, load_watchlist, parse_channel_input
from utils.youtube import (
    API_BASE,
    QuotaError,
    get_channel_stats,
    get_transcript,
    resolve_channel_id,
)
from utils import youtube as youtube_mod

# Import anında okunan anahtar, dotenv'den sonra tazelensin.
youtube_mod.YOUTUBE_API_KEY = os.environ.get("YOUTUBE_API_KEY")

CHUNK_DIR = ROOT / "data" / "extraction_chunks"
OPS_DIR = ROOT / "data" / "ops_reports"
SMOKE_USAGE_GLOB = "smoke_*/usage.json"

# Extraction senkron Sonnet 5 (12_ops_report ile aynı taban).
PRICE_SYNC_IN = 2.0 / 1_000_000
PRICE_SYNC_OUT = 10.0 / 1_000_000

YES = {"evet", "e", "yes", "y"}
NO = {"hayır", "hayir", "h", "no", "n"}


def _load_collect_mod():
    path = ROOT / "pipeline" / "01_collect.py"
    spec = importlib.util.spec_from_file_location("pipeline_01_collect", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def parse_channel_arg(channel_id: str | None, channel_url: str | None) -> str:
    """--channel-id veya --channel-url → UC... (handle ise API ile çözülür)."""
    raw = (channel_id or channel_url or "").strip()
    if not raw:
        raise SystemExit("Hata: --channel-id veya --channel-url gerekli.")
    parsed = parse_channel_input(raw)
    if parsed is None and raw.startswith("UC") and len(raw) >= 20:
        parsed = raw
    if parsed is None:
        raise SystemExit(f"Hata: kanal ID/URL çözülemedi: {raw!r}")
    if parsed.startswith("UC"):
        return parsed
    cid = resolve_channel_id(parsed)
    if not cid:
        raise SystemExit(f"Hata: handle çözülemedi: {parsed!r}")
    return cid


def count_processed_videos(conn, channel_id: str) -> int:
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM videos WHERE channel_id = ?",
        (channel_id,),
    ).fetchone()
    return int(row["n"] if row else 0)


def avg_chunks_from_files(chunk_dir: Path) -> dict:
    """extraction_chunks/*.json içindeki gerçek chunk sayısı ortalaması."""
    per_video: dict[str, int] = {}
    for path in sorted(chunk_dir.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        n = len(data.get("chunks") or [])
        vid = data.get("video_id") or path.stem
        per_video[vid] = n
    if not per_video:
        raise RuntimeError(f"chunk dosyası yok: {chunk_dir}")
    total = sum(per_video.values())
    return {
        "avg": total / len(per_video),
        "n_videos": len(per_video),
        "total_chunks": total,
        "per_video": per_video,
    }


def _sync_usage_cost_usd(usage: dict | None) -> float | None:
    if not usage:
        return None
    inp = int(usage.get("input_tokens") or 0)
    out = int(usage.get("output_tokens") or 0)
    if inp == 0 and out == 0:
        return None
    return inp * PRICE_SYNC_IN + out * PRICE_SYNC_OUT


def avg_cost_per_chunk_from_usage(data_dir: Path) -> dict:
    """Ölçülmüş extraction maliyeti / o ölçümdeki chunk sayısı. Sabit uydurma yok."""
    samples = []
    for path in sorted(data_dir.glob(SMOKE_USAGE_GLOB)):
        data = json.loads(path.read_text(encoding="utf-8"))
        ext = data.get("extraction") or {}
        cost = _sync_usage_cost_usd(ext)
        chunks = ext.get("chunks") or []
        n_chunks = len(chunks) if chunks else int(ext.get("calls") or 0)
        if cost is None or n_chunks <= 0:
            continue
        samples.append({
            "path": str(path.relative_to(data_dir)),
            "cost_usd": cost,
            "n_chunks": n_chunks,
        })
    if not samples:
        raise RuntimeError("ölçülmüş extraction usage.json bulunamadı")
    total_cost = sum(s["cost_usd"] for s in samples)
    total_chunks = sum(s["n_chunks"] for s in samples)
    return {
        "avg": total_cost / total_chunks,
        "total_cost_usd": total_cost,
        "total_chunks": total_chunks,
        "samples": samples,
    }


def avg_cost_per_claim_from_ops(ops_dir: Path) -> dict:
    """Son ops JSON raporundaki gerçek $/claim ortalaması (all.cost_mean)."""
    candidates: list[tuple[float, Path, float]] = []
    for path in ops_dir.glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict):
            continue
        mean = None
        block = data.get("all")
        if isinstance(block, dict) and block.get("cost_mean") is not None:
            mean = float(block["cost_mean"])
        elif data.get("cost_mean") is not None:
            try:
                mean = float(data["cost_mean"])
            except (TypeError, ValueError):
                mean = None
        if mean is None:
            continue
        candidates.append((path.stat().st_mtime, path, mean))
    if not candidates:
        raise RuntimeError(f"ops raporunda cost_mean yok: {ops_dir}")
    candidates.sort(key=lambda x: x[0], reverse=True)
    _, path, mean = candidates[0]
    return {"avg": mean, "source": str(path.relative_to(ROOT))}


def avg_claims_per_video(conn, channel_id: str) -> dict:
    """Kanalın kendi aktif iddia ortalaması; yoksa genel ortalama."""
    row = conn.execute(
        """
        SELECT COUNT(*) AS claims, COUNT(DISTINCT video_id) AS videos
        FROM claims
        WHERE archived_at IS NULL AND channel_id = ?
        """,
        (channel_id,),
    ).fetchone()
    ch_claims = int(row["claims"] or 0)
    ch_videos = int(row["videos"] or 0)
    if ch_videos > 0:
        return {
            "avg": ch_claims / ch_videos,
            "source": "channel",
            "claims": ch_claims,
            "videos": ch_videos,
        }
    row = conn.execute(
        """
        SELECT COUNT(*) AS claims, COUNT(DISTINCT video_id) AS videos
        FROM claims
        WHERE archived_at IS NULL
        """
    ).fetchone()
    g_claims = int(row["claims"] or 0)
    g_videos = int(row["videos"] or 0)
    if g_videos <= 0:
        raise RuntimeError("DB'de ortalama iddia hesabı için video yok")
    return {
        "avg": g_claims / g_videos,
        "source": "global",
        "claims": g_claims,
        "videos": g_videos,
    }


def estimate_costs(n_new: int, chunks: dict, per_chunk: dict, claims: dict, per_claim: dict) -> dict:
    n = max(0, int(n_new))
    extraction = n * chunks["avg"] * per_chunk["avg"]
    factcheck = n * claims["avg"] * per_claim["avg"]
    return {
        "n_new": n,
        "extraction_usd": extraction,
        "factcheck_usd": factcheck,
        "total_usd": extraction + factcheck,
        "avg_chunks_per_video": chunks["avg"],
        "avg_cost_per_chunk": per_chunk["avg"],
        "avg_claims_per_video": claims["avg"],
        "avg_cost_per_claim": per_claim["avg"],
        "chunks_meta": chunks,
        "per_chunk_meta": per_chunk,
        "claims_meta": claims,
        "per_claim_meta": per_claim,
    }


def _fmt_money(x: float) -> str:
    return f"${x:.2f}"


def print_preview(stats: dict, processed: int, n_new: int, estimate: dict) -> None:
    print()
    print("=== Kanal bilgisi (YouTube Data API) ===")
    print(f"  Kanal adı: {stats.get('name')}")
    print(f"  channel_id: {stats.get('channel_id')}")
    print(f"  Toplam video sayısı: {stats.get('total_videos')}")
    print(f"  Zaten işlenmiş (DB'de varsa): {processed}")
    print(f"  Yeni işlenecek: {n_new}")
    print()
    print("=== Maliyet tahmini (geçmiş ölçüm) ===")
    ch = estimate["chunks_meta"]["per_video"]
    chunk_bits = ", ".join(f"{k}={v}" for k, v in ch.items())
    print(f"  avg_chunks_per_video = {estimate['avg_chunks_per_video']:.4f}"
          f"  ({chunk_bits}; n={estimate['chunks_meta']['n_videos']})")
    pc = estimate["per_chunk_meta"]
    sample_bits = "; ".join(
        f"{s['path']} ${s['cost_usd']:.5f}/{s['n_chunks']} chunk"
        for s in pc["samples"]
    )
    print(f"  avg_cost_per_chunk = ${estimate['avg_cost_per_chunk']:.4f}"
          f"  ({sample_bits})")
    cm = estimate["claims_meta"]
    src = "kanal geçmişi" if cm["source"] == "channel" else "genel ortalama"
    print(f"  avg_claims_per_video = {estimate['avg_claims_per_video']:.2f}"
          f"  ({src}: {cm['claims']}/{cm['videos']})")
    print(f"  avg_cost_per_claim = ${estimate['avg_cost_per_claim']:.4f}"
          f"  ({estimate['per_claim_meta']['source']})")
    print()
    n = estimate["n_new"]
    print(
        f"{n} yeni video için tahmini maliyet: {_fmt_money(estimate['total_usd'])} "
        f"(extraction: {_fmt_money(estimate['extraction_usd'])}, "
        f"fact-check: {_fmt_money(estimate['factcheck_usd'])}). "
        "Bu, geçmiş ortalamalara dayanan bir TAHMİN, kesin fatura değil."
    )
    print()


def count_pending_extract_videos(conn, channel_id: str) -> int:
    row = conn.execute(
        """
        SELECT COUNT(*) AS n FROM videos
        WHERE channel_id = ?
          AND transcript IS NOT NULL
          AND claims_extracted_at IS NULL
        """,
        (channel_id,),
    ).fetchone()
    return int(row["n"] or 0)


def count_pending_factcheck_claims(conn, channel_id: str) -> int:
    row = conn.execute(
        """
        SELECT COUNT(*) AS n
        FROM claims c
        LEFT JOIN verdicts vr ON vr.claim_id = c.claim_id
        WHERE c.channel_id = ?
          AND c.archived_at IS NULL
          AND vr.claim_id IS NULL
        """,
        (channel_id,),
    ).fetchone()
    return int(row["n"] or 0)


def pending_factcheck_video_ids(conn, channel_id: str) -> list[str]:
    rows = conn.execute(
        """
        SELECT DISTINCT c.video_id
        FROM claims c
        LEFT JOIN verdicts vr ON vr.claim_id = c.claim_id
        WHERE c.channel_id = ?
          AND c.archived_at IS NULL
          AND vr.claim_id IS NULL
        ORDER BY c.video_id
        """,
        (channel_id,),
    ).fetchall()
    return [r["video_id"] for r in rows]


def format_confirm_scope(
    *,
    n_new: int,
    estimate: dict,
    pending_extract: int,
    pending_claims: int,
    pending_extract_usd: float,
    pending_factcheck_usd: float,
) -> dict:
    """Onay gövdesi, prompt ve sorulup sorulmayacağı."""
    if n_new > 0:
        total = estimate["total_usd"]
        body = (
            f"{n_new} yeni video için kanalın tamamı işlenecek: "
            "collect (liste+transkript) + extract + fact-check.\n"
            f"Tahmini maliyet: {_fmt_money(total)} "
            f"(extraction: {_fmt_money(estimate['extraction_usd'])}, "
            f"fact-check: {_fmt_money(estimate['factcheck_usd'])}). "
            "Bu bir TAHMİN, kesin fatura değil.\n"
            "Onay = bu üç adımın hepsi başlar. İptal = hiçbiri başlamaz."
        )
        prompt = (
            f"Kanalın tamamı extract+fact-check edilecek "
            f"(tahmini {_fmt_money(total)}). Devam etmek istiyor musunuz? (evet/hayır): "
        )
        return {"body": body, "prompt": prompt, "should_ask": True}

    if pending_claims <= 0 and pending_extract <= 0:
        body = "Yeni video yok, bekleyen extract veya fact-check de yok."
        return {"body": body, "prompt": "", "should_ask": False}

    total = pending_extract_usd + pending_factcheck_usd
    lines = [
        f"Bu kanal için YENİ video yok, ama önceki bir çalıştırmadan kalma "
        f"{pending_claims} bekleyen iddia var "
        "(extract edilmiş ama fact-check edilmemiş). "
        "Bunları işlemek ister misiniz? "
        f"Tahmini maliyet: {_fmt_money(total)}."
    ]
    if pending_extract > 0:
        lines.append(
            f"Ayrıca {pending_extract} video extract bekliyor "
            f"(tahmini extraction {_fmt_money(pending_extract_usd)})."
        )
    if pending_claims <= 0 and pending_extract > 0:
        lines = [
            f"Bu kanal için YENİ video yok, ama önceki bir çalıştırmadan kalma "
            f"{pending_extract} video extract bekliyor. "
            "Bunları işlemek ister misiniz? "
            f"Tahmini maliyet: {_fmt_money(pending_extract_usd)}."
        ]
    body = "\n".join(lines)
    prompt = (
        f"Bekleyen işi işlemek istiyor musunuz? (tahmini {_fmt_money(total)}) "
        "(evet/hayır): "
    )
    return {"body": body, "prompt": prompt, "should_ask": True}


def ask_confirm(*, dry_run: bool, prompt: str) -> bool:
    if dry_run:
        print(prompt + "hayır")
        print("[dry-run] onay otomatik: hayır — collect/extract/fact-check başlatılmıyor")
        return False
    while True:
        raw = input(prompt).strip().lower()
        if raw in YES:
            return True
        if raw in NO:
            return False
        print("  Lütfen 'evet' veya 'hayır' yazın.")


def _load_factcheck03():
    path = ROOT / "pipeline" / "03_factcheck.py"
    spec = importlib.util.spec_from_file_location("pipeline_03_factcheck", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def run_extract_channel(channel_id: str, *, limit: int = 10000) -> int:
    script = ROOT / "pipeline" / "02_extract_claims.py"
    proc = subprocess.run(
        [sys.executable, str(script), "--channel-id", channel_id, "--limit", str(limit)],
        cwd=str(ROOT),
    )
    return proc.returncode


def run_factcheck_channel(video_ids: list[str], n_claims: int) -> tuple[int, dict | None]:
    if not video_ids:
        return 0, None
    argv = [
        "--auto-method",
        "--video-ids",
        ",".join(video_ids),
        "--limit",
        str(max(int(n_claims), 1)),
    ]
    print(f"  [factcheck] auto-method videos={len(video_ids)} claims={n_claims}")
    mod = _load_factcheck03()
    try:
        dispatch = mod.main(argv)
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else 1
        return (code or 0), None
    return 0, dispatch


def _list_upload_videos(uploads_playlist_id: str, max_results: int) -> list[dict]:
    """playlistItems sayfalı — 01_collect tek sayfa (≤50); abonelikte tüm yeni videolar gerekir."""
    import requests

    videos: list[dict] = []
    page_token = None
    key = youtube_mod.YOUTUBE_API_KEY
    while len(videos) < max_results:
        params = {
            "part": "snippet",
            "playlistId": uploads_playlist_id,
            "maxResults": min(50, max_results - len(videos)),
            "key": key,
        }
        if page_token:
            params["pageToken"] = page_token
        r = requests.get(f"{API_BASE}/playlistItems", params=params, timeout=15)
        data = r.json()
        if "error" in data:
            raise QuotaError(data["error"].get("message", str(data["error"])))
        for it in data.get("items") or []:
            sn = it["snippet"]
            videos.append({
                "video_id": sn["resourceId"]["videoId"],
                "title": sn["title"],
                "published_at": sn["publishedAt"],
            })
        page_token = data.get("nextPageToken")
        if not page_token:
            break
        time.sleep(0.2)
    return videos[:max_results]


def collect_channel_videos(
    conn,
    stats: dict,
    *,
    max_videos: int,
    fetch_transcripts: bool = True,
) -> dict:
    """01_collect upsert + transkript mantığı; extraction/fact-check yok."""
    collect_mod = _load_collect_mod()
    existing = {
        r["video_id"]
        for r in conn.execute(
            "SELECT video_id FROM videos WHERE transcript IS NOT NULL"
        ).fetchall()
    }
    uploads = stats.get("uploads_playlist")
    if not uploads:
        raise RuntimeError("kanal uploads playlist ID'si yok")
    videos = _list_upload_videos(uploads, max_videos)
    if fetch_transcripts:
        for v in videos:
            if v["video_id"] in existing:
                v["transcript"], v["transcript_lang"] = None, None
                continue
            text, lang = get_transcript(v["video_id"])
            v["transcript"] = text
            v["transcript_lang"] = lang
            time.sleep(0.3)
    else:
        for v in videos:
            v["transcript"] = None
            v["transcript_lang"] = None

    channel_row = {
        k: stats[k]
        for k in ("channel_id", "name", "description", "subscribers", "total_videos", "total_views")
    }
    collect_mod.upsert_channel(conn, channel_row)
    cid = stats["channel_id"]
    for v in videos:
        v.setdefault("watch_source", "channel")
        collect_mod.upsert_video(conn, cid, v)
    conn.commit()
    return {
        "name": stats.get("name"),
        "n_listed": len(videos),
        "n_new_transcripts": sum(1 for v in videos if v.get("transcript")),
    }


def snapshot_state(conn, channel_id: str) -> dict:
    n_videos = count_processed_videos(conn, channel_id)
    wl = load_watchlist()
    return {
        "db_videos": n_videos,
        "watchlist_channels": [c["channel_id"] for c in wl["channels"]],
        "watchlist_n_channels": len(wl["channels"]),
        "watchlist_n_videos": len(wl["videos"]),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Kanala abone ol: maliyeti göster, onayla, collect+extract+fact-check."
    )
    ap.add_argument("--channel-id", help="YouTube kanal ID (UC...)")
    ap.add_argument("--channel-url", help="Kanal URL'si (ID buradan çıkarılır)")
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="YouTube'dan sayıyı çek ve tahmini göster; onayı otomatik hayır say, işlem başlatma",
    )
    ap.add_argument(
        "--max-videos",
        type=int,
        default=None,
        help="Onay sonrası collect üst sınırı (varsayılan: kanaldaki toplam video sayısı)",
    )
    ap.add_argument(
        "--no-transcripts",
        action="store_true",
        help="Collect'te transkript atla (sadece video listesi). Extract transkript ister.",
    )
    args = ap.parse_args(argv)
    if not args.channel_id and not args.channel_url:
        ap.error("--channel-id veya --channel-url gerekli")

    channel_id = parse_channel_arg(args.channel_id, args.channel_url)
    print(f"[subscribe] channel_id={channel_id}")

    try:
        stats = get_channel_stats(channel_id)
    except QuotaError as e:
        print(f"!! YouTube API hatası/kota: {e}")
        return 1
    if not stats:
        print("!! kanal bulunamadı")
        return 1

    conn = get_conn()
    before = snapshot_state(conn, channel_id)
    processed = before["db_videos"]
    n_total = int(stats.get("total_videos") or 0)
    n_new = max(0, n_total - processed)

    chunks = avg_chunks_from_files(CHUNK_DIR)
    per_chunk = avg_cost_per_chunk_from_usage(ROOT / "data")
    claims = avg_claims_per_video(conn, channel_id)
    per_claim = avg_cost_per_claim_from_ops(OPS_DIR)
    estimate = estimate_costs(n_new, chunks, per_chunk, claims, per_claim)
    pending_extract = count_pending_extract_videos(conn, channel_id)
    pending_claims = count_pending_factcheck_claims(conn, channel_id)
    pending_extract_usd = pending_extract * chunks["avg"] * per_chunk["avg"]
    pending_factcheck_usd = pending_claims * per_claim["avg"]
    print_preview(stats, processed, n_new, estimate)
    scope = format_confirm_scope(
        n_new=n_new,
        estimate=estimate,
        pending_extract=pending_extract,
        pending_claims=pending_claims,
        pending_extract_usd=pending_extract_usd,
        pending_factcheck_usd=pending_factcheck_usd,
    )
    print(scope["body"])
    print()

    collect_started = False
    extract_started = False
    factcheck_started = False
    if not scope["should_ask"]:
        after = snapshot_state(conn, channel_id)
        conn.close()
        print("[subscribe] yapılacak iş yok — collect/extract/fact-check başlamadı.")
        print(
            f"collect_started={str(collect_started).lower()}  "
            f"extract_started={str(extract_started).lower()}  "
            f"factcheck_started={str(factcheck_started).lower()}  "
            f"db_videos_before={before['db_videos']} db_videos_after={after['db_videos']}  "
            f"watchlist_channels_before={before['watchlist_n_channels']} "
            f"watchlist_channels_after={after['watchlist_n_channels']}"
        )
        return 0

    confirmed = ask_confirm(dry_run=args.dry_run, prompt=scope["prompt"])
    if not confirmed:
        after = snapshot_state(conn, channel_id)
        conn.close()
        print("[subscribe] iptal — collect/extract/fact-check başlamadı.")
        print(
            f"collect_started={str(collect_started).lower()}  "
            f"extract_started={str(extract_started).lower()}  "
            f"factcheck_started={str(factcheck_started).lower()}  "
            f"db_videos_before={before['db_videos']} db_videos_after={after['db_videos']}  "
            f"watchlist_channels_before={before['watchlist_n_channels']} "
            f"watchlist_channels_after={after['watchlist_n_channels']}"
        )
        return 0

    print("[subscribe] onay alındı — collect + extract + fact-check başlıyor.")
    added = add_channel(channel_id, name=stats.get("name"))
    if not added.get("ok"):
        print(f"  [watchlist] {added.get('error', 'kanal eklenemedi')} — collect yine de çalışacak")
    else:
        print(f"  [watchlist] abone olundu: {channel_id}")

    max_videos = args.max_videos if args.max_videos is not None else n_total
    collect_started = True
    try:
        result = collect_channel_videos(
            conn,
            stats,
            max_videos=max_videos,
            fetch_transcripts=not args.no_transcripts,
        )
    except QuotaError as e:
        print(f"  !! API hatası/kota: {e}")
        conn.close()
        return 1
    print(
        f"  ✓ {result['name']} — listelenen {result['n_listed']} video, "
        f"{result['n_new_transcripts']} yeni transkript"
    )
    conn.close()

    extract_started = True
    print("[subscribe] extract başlıyor (yalnızca bu kanal).")
    ext_rc = run_extract_channel(channel_id)
    if ext_rc:
        print(f"[subscribe] extract hata kodu={ext_rc} — fact-check yine denenecek")

    conn = get_conn()
    fc_n = count_pending_factcheck_claims(conn, channel_id)
    video_ids = pending_factcheck_video_ids(conn, channel_id)
    conn.close()
    factcheck_started = True
    print("[subscribe] fact-check başlıyor (yöntem otomatik).")
    fc_rc, dispatch = run_factcheck_channel(video_ids, fc_n)
    if dispatch:
        print(f"[subscribe] factcheck method={dispatch.get('method')} "
              f"n_claims={dispatch.get('n_claims')} batch_id={dispatch.get('batch_id')}")
    if fc_rc:
        print(f"[subscribe] fact-check hata kodu={fc_rc}")

    print(
        f"collect_started={str(collect_started).lower()}  "
        f"extract_started={str(extract_started).lower()}  "
        f"factcheck_started={str(factcheck_started).lower()}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
