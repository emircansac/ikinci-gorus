"""
Kanalı ön araştır — abone olmadan önce rastgele 3 video, üç kapılı onay.

Onay 1 gelmeden collect/extract/fact-check başlamaz.
Onay 2 gelmeden fact-check başlamaz (iddialar DB'de kalabilir).
Onay 3 gelmeden kalan videolara collect uygulanmaz.

Kullanım:
    python pipeline/21_pre_research_channel.py --channel-id UCXhDI7n_iC4J9jR3GYJKkcQ
    python pipeline/21_pre_research_channel.py --channel-id UCXhDI7n_iC4J9jR3GYJKkcQ --dry-run
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import random
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.append(str(ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass

from utils.db import get_conn
from utils.extraction_store import ACTIVE_CLAIM_WHERE, DEFAULT_EXTRACTION_VERSION, insert_claims_batch
from utils.suspicion import compute_channel_risk, compute_suspicion
from utils.watchlist import add_channel
from utils.youtube import QuotaError, get_transcript
from utils import claude_client

CHUNK_DIR = ROOT / "data" / "extraction_chunks"
OPS_DIR = ROOT / "data" / "ops_reports"
DEBUG_LOG = ROOT / "data" / "factcheck_debug.jsonl"
WATCHLIST_PATH = ROOT / "data" / "watchlist.json"
SAMPLE_N = 3

# 12_ops_report ile aynı fact-check fiyatı (senkron + cache).
PRICE_SYNC_IN = 2.0 / 1_000_000
PRICE_SYNC_OUT = 10.0 / 1_000_000
PRICE_BATCH_IN = PRICE_SYNC_IN * 0.5
PRICE_BATCH_OUT = PRICE_SYNC_OUT * 0.5
PRICE_CACHE_WRITE = 2.5 / 1_000_000
PRICE_CACHE_READ = 0.20 / 1_000_000

YES = {"evet", "e", "yes", "y"}
NO = {"hayır", "hayir", "h", "no", "n"}


def _load_sub20():
    spec = importlib.util.spec_from_file_location(
        "subscribe20", ROOT / "pipeline" / "20_subscribe_channel.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load_score04():
    spec = importlib.util.spec_from_file_location(
        "score04", ROOT / "pipeline" / "04_score_suspects.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


sub20 = _load_sub20()


def pick_sample(videos: list[dict], n: int = SAMPLE_N) -> list[dict]:
    """Seed yok — her çalıştırmada farklı örneklem."""
    if not videos:
        return []
    if len(videos) <= n:
        return list(videos)
    return random.sample(videos, n)


def ask_gate(prompt: str, *, auto_no: bool, auto_no_note: str) -> bool:
    if auto_no:
        print(prompt + "hayır")
        print(auto_no_note)
        return False
    while True:
        raw = input(prompt).strip().lower()
        if raw in YES:
            return True
        if raw in NO:
            return False
        print("  Lütfen 'evet' veya 'hayır' yazın.")


def _watchlist_sha256() -> str:
    if not WATCHLIST_PATH.is_file():
        return "(yok)"
    return hashlib.sha256(WATCHLIST_PATH.read_bytes()).hexdigest()


def snapshot_state(conn, channel_id: str) -> dict:
    base = sub20.snapshot_state(conn, channel_id)
    base["db_claims"] = int(
        conn.execute("SELECT COUNT(*) AS n FROM claims").fetchone()["n"]
    )
    base["channel_claims"] = int(
        conn.execute(
            "SELECT COUNT(*) AS n FROM claims WHERE channel_id = ?",
            (channel_id,),
        ).fetchone()["n"]
    )
    base["risk_rows"] = int(
        conn.execute("SELECT COUNT(*) AS n FROM channel_risk_scores").fetchone()["n"]
    )
    base["watchlist_sha256"] = _watchlist_sha256()
    return base


def print_abort_proof(before: dict, after: dict, *, collect_started: bool,
                      extract_started: bool, factcheck_started: bool, gate: int) -> None:
    print(f"[pre-research] iptal (onay {gate}) — collect/extract/fact-check tetiklenmedi."
          if gate == 1 else
          f"[pre-research] iptal (onay {gate}).")
    print(
        f"collect_started={str(collect_started).lower()} "
        f"extract_started={str(extract_started).lower()} "
        f"factcheck_started={str(factcheck_started).lower()}"
    )
    print(
        f"db_videos_before={before['db_videos']} db_videos_after={after['db_videos']}  "
        f"db_claims_before={before['db_claims']} db_claims_after={after['db_claims']}"
    )
    print(
        f"watchlist_channels_before={before['watchlist_n_channels']} "
        f"watchlist_channels_after={after['watchlist_n_channels']}"
    )
    print(
        f"watchlist_sha256_before={before['watchlist_sha256']} "
        f"watchlist_sha256_after={after['watchlist_sha256']}"
    )


def _fmt_money(x: float) -> str:
    return f"${x:.2f}"


def extraction_estimate_from_chunk_variance(
    n_videos: int, chunks: dict, per_chunk: dict
) -> dict:
    """Kaba extraction $: geçmiş chunk sayısı min–max × $/chunk × n video.

    Aralık güvenlik payı değil; ölçülmüş videolardaki chunk varyansı.
    Tüm videolarda aynı chunk sayısı varsa tek noktaya iner.
    """
    n = max(0, int(n_videos))
    per_video = chunks.get("per_video") or {}
    counts = [int(v) for v in per_video.values() if v is not None]
    if counts:
        lo_c, hi_c = min(counts), max(counts)
    else:
        avg_c = float(chunks["avg"])
        lo_c = hi_c = avg_c
    unit = float(per_chunk["avg"])
    low = n * lo_c * unit
    high = n * hi_c * unit
    return {
        "n_videos": n,
        "min_chunks": lo_c,
        "max_chunks": hi_c,
        "cost_low": low,
        "cost_high": high,
        "avg_cost_per_chunk": unit,
        "is_range": lo_c != hi_c,
        "chunk_counts": counts,
    }


def format_gate1_extraction_line(est: dict) -> str:
    n = est["n_videos"]
    unit = est["avg_cost_per_chunk"]
    if est["is_range"]:
        return (
            f"  {n} video için kaba extraction tahmini: "
            f"{_fmt_money(est['cost_low'])}–{_fmt_money(est['cost_high'])} "
            f"(geçmiş videolarda {est['min_chunks']}–{est['max_chunks']} chunk × "
            f"${unit:.4f}/chunk × {n}; aralık chunk sayısı varyansı, güvenlik payı değil)."
        )
    return (
        f"  {n} video için kaba extraction tahmini: {_fmt_money(est['cost_low'])} "
        f"(her videoda {est['min_chunks']} chunk × ${unit:.4f}/chunk × {n}; "
        f"geçmişte chunk varyansı yok)."
    )


def factcheck_usage_cost_usd(usage: dict | None) -> float | None:
    """12_ops_report._estimate_cost_usd ile aynı formül."""
    if not usage:
        return None
    inp = int(usage.get("input_tokens") or 0)
    out = int(usage.get("output_tokens") or 0)
    cw = int(usage.get("cache_creation_input_tokens") or 0)
    cr = int(usage.get("cache_read_input_tokens") or 0)
    if inp == 0 and out == 0 and cw == 0 and cr == 0:
        return None
    if cw or cr:
        return (
            inp * PRICE_BATCH_IN
            + out * PRICE_BATCH_OUT
            + cw * PRICE_CACHE_WRITE * 0.5
            + cr * PRICE_CACHE_READ
        )
    return inp * PRICE_SYNC_IN + out * PRICE_SYNC_OUT


def collect_selected_videos(conn, stats: dict, videos: list[dict]) -> dict:
    """Yalnızca seçilen videolar — 01_collect upsert + transkript."""
    collect_mod = sub20._load_collect_mod()
    existing = {
        r["video_id"]
        for r in conn.execute(
            "SELECT video_id FROM videos WHERE transcript IS NOT NULL"
        ).fetchall()
    }
    channel_row = {
        k: stats[k]
        for k in ("channel_id", "name", "description", "subscribers", "total_videos", "total_views")
    }
    collect_mod.upsert_channel(conn, channel_row)
    cid = stats["channel_id"]
    n_new = 0
    for v in videos:
        row = dict(v)
        if row["video_id"] in existing:
            row["transcript"], row["transcript_lang"] = None, None
        else:
            text, lang = get_transcript(row["video_id"])
            row["transcript"] = text
            row["transcript_lang"] = lang
            if text:
                n_new += 1
            time.sleep(0.3)
        row.setdefault("watch_source", "channel")
        collect_mod.upsert_video(conn, cid, row)
    conn.commit()
    return {"n_listed": len(videos), "n_new_transcripts": n_new}


def _capture_extraction_usage(fn):
    records: list[dict] = []
    orig = claude_client._log_usage

    def _wrapped(usage):
        d = orig(usage)
        if d:
            records.append(d)
        return d

    claude_client._log_usage = _wrapped  # type: ignore[method-assign]
    try:
        result = fn()
    finally:
        claude_client._log_usage = orig  # type: ignore[method-assign]
    return result, records


def extract_selected_videos(conn, videos: list[dict]) -> dict:
    """Yalnızca seçilen videolar — 02_extract_claims.main() çağrılmaz."""
    os.environ["SAVE_EXTRACTION_CHUNKS"] = "1"
    claude_client.SAVE_EXTRACTION_CHUNKS = True

    def _run():
        extracted = 0
        skipped = 0
        failed = 0
        n_claims = 0
        for v in videos:
            vid = v["video_id"]
            row = conn.execute(
                """
                SELECT video_id, channel_id, transcript, claims_extracted_at
                FROM videos WHERE video_id = ?
                """,
                (vid,),
            ).fetchone()
            if not row or not row["transcript"]:
                print(f"  [extract] {vid}: transkript yok, atlandı")
                failed += 1
                continue
            if row["claims_extracted_at"]:
                print(f"  [extract] {vid}: zaten çıkarılmış, atlandı")
                skipped += 1
                continue
            print(f"  [extract] {vid} ...")
            claims, success = claude_client.extract_claims(row["transcript"], video_id=vid)
            if not success:
                print(f"     !! JSON parse başarısız")
                failed += 1
                continue
            insert_claims_batch(
                conn, vid, row["channel_id"], claims, DEFAULT_EXTRACTION_VERSION
            )
            print(f"     {len(claims)} iddia")
            extracted += 1
            n_claims += len(claims)
        return {
            "extracted": extracted,
            "skipped": skipped,
            "failed": failed,
            "new_claims": n_claims,
        }

    result, usage_records = _capture_extraction_usage(_run)
    ext_cost = 0.0
    for rec in usage_records:
        c = sub20._sync_usage_cost_usd(rec)
        if c:
            ext_cost += c
    result["extraction_usd"] = ext_cost
    result["usage_n"] = len(usage_records)
    return result


def count_sample_claims(conn, video_ids: list[str]) -> int:
    if not video_ids:
        return 0
    placeholders = ",".join("?" * len(video_ids))
    row = conn.execute(
        f"""
        SELECT COUNT(*) AS n FROM claims
        WHERE video_id IN ({placeholders}) AND {ACTIVE_CLAIM_WHERE}
        """,
        video_ids,
    ).fetchone()
    return int(row["n"] or 0)


def _load_factcheck03():
    spec = importlib.util.spec_from_file_location(
        "pipeline_03_factcheck", ROOT / "pipeline" / "03_factcheck.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def run_factcheck_videos(video_ids: list[str], n_claims: int | None = None) -> tuple[int, dict | None]:
    """03_factcheck --auto-method; tek çağrı, video listesi scoped."""
    if not video_ids:
        return 0, None
    limit = max(int(n_claims or 0), 1)
    argv = [
        "--auto-method",
        "--video-ids",
        ",".join(video_ids),
        "--limit",
        str(limit),
    ]
    print(f"  [factcheck] auto-method videos={len(video_ids)} limit={limit}")
    mod = _load_factcheck03()
    try:
        dispatch = mod.main(argv)
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else 1
        return (code or 0), None
    return 0, dispatch


def _latest_debug_usage_by_claim() -> dict[int, dict]:
    out: dict[int, dict] = {}
    if not DEBUG_LOG.is_file():
        return out
    with DEBUG_LOG.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            cid = rec.get("claim_id")
            if cid is None:
                continue
            usage = rec.get("usage")
            if usage:
                out[int(cid)] = usage
    return out


def factcheck_cost_for_claims(conn, video_ids: list[str]) -> tuple[float, int]:
    if not video_ids:
        return 0.0, 0
    placeholders = ",".join("?" * len(video_ids))
    rows = conn.execute(
        f"""
        SELECT claim_id FROM claims
        WHERE video_id IN ({placeholders}) AND {ACTIVE_CLAIM_WHERE}
        """,
        video_ids,
    ).fetchall()
    ids = {int(r["claim_id"]) for r in rows}
    usage_by = _latest_debug_usage_by_claim()
    total = 0.0
    n = 0
    for cid in ids:
        cost = factcheck_usage_cost_usd(usage_by.get(cid))
        if cost is not None:
            total += cost
            n += 1
    return total, n


def compute_informational_risk(conn, stats: dict, video_ids: list[str]) -> dict:
    """04 heuristikleri; min_videos gate yok; channel_risk_scores YAZILMAZ."""
    score_mod = _load_score04()
    if not video_ids:
        return {"score": None, "tier": "yetersiz_veri", "funnel_flag": False,
                "ai_persona_flag": False, "meta": {}}
    placeholders = ",".join("?" * len(video_ids))
    transcripts = conn.execute(
        f"SELECT transcript FROM videos WHERE video_id IN ({placeholders})",
        video_ids,
    ).fetchall()
    full_text = " ".join((t["transcript"] or "") for t in transcripts)
    desc = stats.get("description") or ""
    funnel = score_mod.keyword_flag(full_text + " " + desc, score_mod.FUNNEL_PATTERNS)
    ai_persona = score_mod.keyword_flag(desc, score_mod.AI_PERSONA_PATTERNS)
    claim_rows = conn.execute(
        f"""
        SELECT c.claim_id, c.claim_text, c.category, c.initial_risk,
               vr.final_verdict, vr.confidence, vr.human_reviewed
        FROM claims c
        LEFT JOIN verdicts vr ON vr.claim_id = c.claim_id
        WHERE c.video_id IN ({placeholders}) AND c.{ACTIVE_CLAIM_WHERE}
        """,
        video_ids,
    ).fetchall()
    rows_as_dicts = [dict(r) for r in claim_rows]
    score, tier, meta = compute_channel_risk(
        rows_as_dicts,
        funnel_flag=funnel,
        ai_persona_flag=ai_persona,
        growth_anomaly_flag=False,
        bot_comment_ratio=0.0,
    )
    return {
        "score": score,
        "tier": tier,
        "funnel_flag": funnel,
        "ai_persona_flag": ai_persona,
        "meta": meta,
        "n_claims": len(rows_as_dicts),
    }


def print_sample_report(conn, videos: list[dict], risk: dict,
                        extraction_usd: float, factcheck_usd: float) -> None:
    video_ids = [v["video_id"] for v in videos]
    print()
    print("=== Ön araştırma raporu (3 video, bilgilendirme) ===")
    print("  Not: min_videos_for_score üretim kapısı bu rapor için yok sayıldı; "
          "channel_risk_scores tablosuna yazılmadı.")
    for v in videos:
        vid = v["video_id"]
        rows = conn.execute(
            f"""
            SELECT initial_risk FROM claims
            WHERE video_id = ? AND {ACTIVE_CLAIM_WHERE}
            """,
            (vid,),
        ).fetchall()
        dist = Counter((r["initial_risk"] or "?") for r in rows)
        dist_s = ", ".join(f"{k}={n}" for k, n in sorted(dist.items())) or "(iddia yok)"
        print(f"  {vid}  {v.get('title', '')[:70]}")
        print(f"    iddia: {len(rows)}  initial_risk: {dist_s}")

    verdicts = conn.execute(
        f"""
        SELECT vr.final_verdict FROM claims c
        JOIN verdicts vr ON vr.claim_id = c.claim_id
        WHERE c.video_id IN ({",".join("?" * len(video_ids))}) AND c.{ACTIVE_CLAIM_WHERE}
        """,
        video_ids,
    ).fetchall() if video_ids else []
    vdist = Counter((r["final_verdict"] or "?") for r in verdicts)
    print("  Verdict dağılımı: " + (
        ", ".join(f"{k}={n}" for k, n in sorted(vdist.items())) or "(henüz yok)"
    ))

    scored_rows = conn.execute(
        f"""
        SELECT c.claim_id, c.claim_text, vr.final_verdict, vr.confidence, vr.reasoning
        FROM claims c
        JOIN verdicts vr ON vr.claim_id = c.claim_id
        WHERE c.video_id IN ({",".join("?" * len(video_ids))}) AND c.{ACTIVE_CLAIM_WHERE}
        """,
        video_ids,
    ).fetchall() if video_ids else []
    ranked = []
    for r in scored_rows:
        parse_failed = "parse edilemedi" in ((r["reasoning"] or "").lower())
        score, _note = compute_suspicion(r["final_verdict"], r["confidence"], parse_failed)
        if score is not None:
            ranked.append((score, r))
    ranked.sort(key=lambda x: x[0], reverse=True)
    print("  En yüksek şüphe (en fazla 5):")
    if not ranked:
        print("    (skorlanabilir verdict yok)")
    for score, r in ranked[:5]:
        text = (r["claim_text"] or "").replace("\n", " ")
        if len(text) > 160:
            text = text[:160] + "…"
        print(f"    #{r['claim_id']}  suspicion={score:.1f}  {text}")

    print(f"  funnel_flag={risk['funnel_flag']}  ai_persona_flag={risk['ai_persona_flag']}")
    score_s = "—" if risk["score"] is None else f"{risk['score']:.1f}"
    print(f"  örneklem risk_score={score_s}  tier={risk['tier']}  "
          f"(üretim min_videos kapısı uygulanmadı)")
    total = extraction_usd + factcheck_usd
    print(
        f"  Gerçek toplam maliyet (bu 3 video): {_fmt_money(total)} "
        f"(extraction: {_fmt_money(extraction_usd)}, fact-check: {_fmt_money(factcheck_usd)})"
    )
    print()


def sample_cost_inputs(conn, video_ids: list[str], extraction_usd: float,
                       extraction_calls: int, factcheck_usd: float | None,
                       factcheck_n: int, historical_chunks: dict,
                       historical_per_chunk: dict, historical_per_claim: dict) -> tuple:
    """Kalan video tahmini: bu 3 videonun ortalaması (tek-video kanal geçmişi değil)."""
    per_video: dict[str, int] = {}
    for vid in video_ids:
        path = CHUNK_DIR / f"{vid}.json"
        if not path.is_file():
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        per_video[vid] = len(data.get("chunks") or [])
    if per_video:
        total = sum(per_video.values())
        chunks = {
            "avg": total / len(per_video),
            "n_videos": len(per_video),
            "total_chunks": total,
            "per_video": per_video,
        }
    else:
        chunks = historical_chunks

    n_chunks = sum(per_video.values()) if per_video else extraction_calls
    if extraction_usd > 0 and n_chunks > 0:
        per_chunk = {
            "avg": extraction_usd / n_chunks,
            "samples": [{
                "path": "pre_research_this_run",
                "cost_usd": extraction_usd,
                "n_chunks": n_chunks,
            }],
        }
    else:
        per_chunk = historical_per_chunk

    n_claims = count_sample_claims(conn, video_ids)
    n_vids = max(len(video_ids), 1)
    claims = {
        "avg": n_claims / n_vids,
        "source": "sample3",
        "claims": n_claims,
        "videos": n_vids,
    }

    if factcheck_usd is not None and factcheck_n > 0:
        per_claim = {
            "avg": factcheck_usd / factcheck_n,
            "source": "pre_research_this_run",
        }
    else:
        per_claim = historical_per_claim
    return chunks, per_chunk, claims, per_claim


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Kanalı ön araştır: rastgele 3 video, üç kapılı onay."
    )
    ap.add_argument("--channel-id", help="YouTube kanal ID (UC...)")
    ap.add_argument("--channel-url", help="Kanal URL'si")
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="Katalog + 3 başlık + kaba tahmini göster; onay 1 otomatik hayır",
    )
    args = ap.parse_args(argv)
    if not args.channel_id and not args.channel_url:
        ap.error("--channel-id veya --channel-url gerekli")

    channel_id = sub20.parse_channel_arg(args.channel_id, args.channel_url)
    print(f"[pre-research] channel_id={channel_id}")

    try:
        stats = sub20.get_channel_stats(channel_id)
    except QuotaError as e:
        print(f"!! YouTube API hatası/kota: {e}")
        return 1
    if not stats:
        print("!! kanal bulunamadı")
        return 1

    uploads = stats.get("uploads_playlist")
    if not uploads:
        print("!! uploads playlist yok")
        return 1
    n_total = int(stats.get("total_videos") or 0)
    try:
        catalog = sub20._list_upload_videos(uploads, max(n_total, 10_000))
    except QuotaError as e:
        print(f"!! YouTube API hatası/kota: {e}")
        return 1
    print(f"[pre-research] katalog: {len(catalog)} video (API videoCount={n_total})")

    sample = pick_sample(catalog, SAMPLE_N)
    if not sample:
        print("!! kanalda video yok")
        return 1
    if len(catalog) < SAMPLE_N:
        print(f"  uyarı: kanalda {len(catalog)} video var, hepsi seçildi")
    print()
    print("=== Rastgele örneklem ===")
    for i, v in enumerate(sample, 1):
        print(f"  {i}. {v['video_id']}  {v.get('title', '')}")
    print()

    historical_chunks = sub20.avg_chunks_from_files(CHUNK_DIR)
    historical_per_chunk = sub20.avg_cost_per_chunk_from_usage(ROOT / "data")
    n_sample = len(sample)
    gate1_est = extraction_estimate_from_chunk_variance(
        n_sample, historical_chunks, historical_per_chunk
    )
    print("=== Onay 1 — kaba extraction tahmini ===")
    counts = historical_chunks.get("per_video") or {}
    count_bits = ", ".join(f"{k}={v}" for k, v in counts.items())
    print(
        f"  geçmiş chunk sayıları: {count_bits or '(yok)'}  "
        f"avg_cost_per_chunk=${historical_per_chunk['avg']:.4f}"
    )
    print(format_gate1_extraction_line(gate1_est))
    print("  fact-check maliyeti extraction bitene kadar bilinmiyor, ikinci bir onay o zaman gelecek.")
    print()

    conn = get_conn()
    before = snapshot_state(conn, channel_id)
    collect_started = False
    extract_started = False
    factcheck_started = False

    ok1 = ask_gate(
        "Devam etmek istiyor musunuz? (evet/hayır): ",
        auto_no=args.dry_run,
        auto_no_note="[dry-run] onay 1 otomatik: hayır — collect/extract başlatılmıyor",
    )
    if not ok1:
        after = snapshot_state(conn, channel_id)
        conn.close()
        print_abort_proof(
            before, after,
            collect_started=collect_started,
            extract_started=extract_started,
            factcheck_started=factcheck_started,
            gate=1,
        )
        return 0

    print("[pre-research] onay 1 — 3 video collect + extract")
    collect_started = True
    try:
        col = collect_selected_videos(conn, stats, sample)
    except QuotaError as e:
        print(f"  !! API hatası/kota: {e}")
        conn.close()
        return 1
    print(f"  collect: {col['n_listed']} video, {col['n_new_transcripts']} yeni transkript")

    extract_started = True
    ext = extract_selected_videos(conn, sample)
    print(
        f"  extract: yeni={ext['extracted']} atlanan={ext['skipped']} "
        f"hata={ext['failed']} extraction=${ext['extraction_usd']:.4f}"
    )

    video_ids = [v["video_id"] for v in sample]
    n_claims = count_sample_claims(conn, video_ids)
    per_claim = sub20.avg_cost_per_claim_from_ops(OPS_DIR)
    fc_est = n_claims * per_claim["avg"]
    print()
    print("=== Onay 2 — fact-check tahmini (gerçek iddia sayısı) ===")
    print(f"  çıkan iddia: {n_claims}")
    print(f"  avg_cost_per_claim=${per_claim['avg']:.4f} ({per_claim['source']})")
    print(
        f"  tahmini fact-check: {_fmt_money(fc_est)}. "
        "Onay yoksa iddialar DB'de kalır, fact-check edilmez."
    )
    print()

    ok2 = ask_gate(
        "Fact-check'e devam etmek istiyor musunuz? (evet/hayır): ",
        auto_no=False,
        auto_no_note="",
    )
    extraction_usd = float(ext.get("extraction_usd") or 0.0)
    fc_usd = 0.0
    fc_n = 0
    if not ok2:
        after = snapshot_state(conn, channel_id)
        conn.close()
        print("[pre-research] fact-check atlandı — iddialar DB'de kaldı.")
        print_abort_proof(
            before, after,
            collect_started=collect_started,
            extract_started=extract_started,
            factcheck_started=False,
            gate=2,
        )
        return 0

    print("[pre-research] onay 2 — fact-check (yöntem otomatik)")
    factcheck_started = True
    conn.close()
    run_factcheck_videos(video_ids, n_claims)
    conn = get_conn()
    fc_usd, fc_n = factcheck_cost_for_claims(conn, video_ids)
    risk = compute_informational_risk(conn, stats, video_ids)
    print_sample_report(conn, sample, risk, extraction_usd, fc_usd)

    processed = sub20.count_processed_videos(conn, channel_id)
    n_remaining = max(0, n_total - processed)
    chunks_s, per_chunk_s, claims_s, per_claim_s = sample_cost_inputs(
        conn, video_ids, extraction_usd, int(ext.get("usage_n") or 0),
        fc_usd, fc_n, historical_chunks, historical_per_chunk, per_claim,
    )
    rem_est = sub20.estimate_costs(n_remaining, chunks_s, per_chunk_s, claims_s, per_claim_s)
    tier = risk.get("tier") or "yetersiz_veri"
    score = risk.get("score")
    score_s = "—" if score is None else f"{score:.1f}"
    print("=== Onay 3 — kalan videolara abone ol ===")
    print(
        f"Bu {n_sample} video örneğine göre kanalın riski: tier={tier}, "
        f"score={score_s}, funnel={risk['funnel_flag']}, ai_persona={risk['ai_persona_flag']}."
    )
    print(
        f"Kalan {n_remaining} videoya da abone olmak ister misiniz? "
        f"Tahmini maliyet: {_fmt_money(rem_est['total_usd'])} "
        f"(extraction: {_fmt_money(rem_est['extraction_usd'])}, "
        f"fact-check: {_fmt_money(rem_est['factcheck_usd'])}) "
        f"— 20_subscribe_channel formülü, bu kanalın {n_sample}-video ortalaması "
        f"(avg_claims={claims_s['avg']:.2f} = {claims_s['claims']}/{claims_s['videos']}; "
        f"tek-video örneklemi değil)."
    )
    print()

    ok3 = ask_gate(
        "Kalan videolara abone olmak istiyor musunuz? (evet/hayır): ",
        auto_no=False,
        auto_no_note="",
    )
    if not ok3:
        after = snapshot_state(conn, channel_id)
        conn.close()
        print("[pre-research] abonelik yok — kalan videolara collect yok.")
        print_abort_proof(
            before, after,
            collect_started=collect_started,
            extract_started=extract_started,
            factcheck_started=factcheck_started,
            gate=3,
        )
        return 0

    print("[pre-research] onay 3 — 20 collect akışı (extract/fact-check yok)")
    added = add_channel(channel_id, name=stats.get("name"))
    if not added.get("ok"):
        print(f"  [watchlist] {added.get('error', 'kanal eklenemedi')} — collect yine de çalışacak")
    else:
        print(f"  [watchlist] abone olundu: {channel_id}")
    try:
        result = sub20.collect_channel_videos(
            conn, stats, max_videos=n_total, fetch_transcripts=True,
        )
    except QuotaError as e:
        print(f"  !! API hatası/kota: {e}")
        conn.close()
        return 1
    conn.close()
    print(
        f"  ✓ {result['name']} — listelenen {result['n_listed']} video, "
        f"{result['n_new_transcripts']} yeni transkript"
    )
    print("[pre-research] kalan collect tamam. Extraction/fact-check elle, dilim disipliniyle.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
