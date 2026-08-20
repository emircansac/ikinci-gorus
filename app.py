"""
Yerel web sunucusu — pipeline CSV çıktılarını JSON olarak sunar.

Okuduğu dosyalar: data/suspects.csv, data/claim_index.csv,
data/claim_archive.csv, data/narrative_clusters.csv, data/videos.csv.
Dashboard bunları fetch() ile çeker.

BİLİNÇLİ TASARIM: veritabanına (SQLite) doğrudan bağlanmak yerine SADECE
CSV çıktılarını okuyor. Neden: pipeline'ın "doğruluk kaynağı" zaten bu CSV'ler
(06_claim_index.py, 04_score_suspects.py tarafından üretiliyor) — web sunucusu
kendi sorgu mantığını (join, filtre) tekrar yazıp DB şemasıyla sıkı bağlı
olmaktansa, tek bir yerden (CSV) okuyor. Şema değişse bile bu dosya bozulmaz.

Dashboard'daki Analiz et, run_pipeline.py --watchlist karşılığını arka plan
thread'inde çalıştırır; Flask istek sunumunu bloklamaz. 20/21 interaktif
script'ler burada yok (terminalden çalıştırın).

    python app.py
    -> http://localhost:8000
"""
import json
import logging
import math
import os
import sys
import threading
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from flask import Flask, jsonify, render_template, request

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env")
except ImportError:
    pass

app = Flask(__name__)
ROOT = Path(__file__).resolve().parent
_raw_data = os.environ.get("DATA_DIR")
DATA_DIR = Path(_raw_data) if _raw_data else (ROOT / "data")

log = logging.getLogger("pipeline_scheduler")
log.setLevel(logging.INFO)
log.propagate = False
if not log.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("%(asctime)s %(name)s %(levelname)s %(message)s"))
    log.addHandler(_handler)

_job_lock = threading.Lock()
_scheduler_state = {
    "last_started_at": None,
    "last_finished_at": None,
    "last_error": None,
    "running": False,
    "ticks": 0,
}

_export_lock = threading.Lock()
_claim_patches_lock = threading.Lock()
_claim_patches = {}
_export_stop = threading.Event()
_export_thread = None
_export_state = {
    "enabled": False,
    "interval_seconds": None,
    "stale": False,
    "running": False,
    "last_started_at": None,
    "last_finished_at": None,
    "last_error": None,
    "ticks": 0,
}


def _csv_mtime_iso(filename: str):
    path = DATA_DIR / filename
    if not path.exists():
        return None
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat()


if _export_state["last_finished_at"] is None:
    _export_state["last_finished_at"] = _csv_mtime_iso("claim_index.csv") or _csv_mtime_iso("suspects.csv")


def read_csv_as_json(filename: str):
    path = DATA_DIR / filename
    if not path.exists():
        return None
    df = pd.read_csv(path)
    # to_json NaN -> null yapar; df.where(..., None) float sütunlarda NaN bırakabiliyor
    records = json.loads(df.to_json(orient="records"))
    for rec in records:
        for k, v in list(rec.items()):
            if isinstance(v, float) and math.isnan(v):
                rec[k] = None
    return records


@app.route("/")
def dashboard():
    return render_template("dashboard.html")


@app.route("/api/channels")
def api_channels():
    data = read_csv_as_json("suspects.csv")
    if data is None:
        return jsonify({"error": "henüz veri yok — pipeline/04_score_suspects.py hiç çalışmamış olabilir"}), 404
    return jsonify(data)


@app.route("/api/claims")
def api_claims():
    active, _archived = claims_with_patches()
    return jsonify(active)


@app.route("/api/claims/archived")
def api_claims_archived():
    _active, archived = claims_with_patches()
    return jsonify(archived)


@app.route("/api/watchlist")
def api_watchlist_get():
    from utils.watchlist import load_watchlist, MIN_VIDEOS_FOR_CHANNEL_SCORE
    wl = load_watchlist()
    wl["min_videos_for_score"] = MIN_VIDEOS_FOR_CHANNEL_SCORE
    return jsonify(wl)


@app.route("/api/watchlist/channel", methods=["POST"])
def api_watchlist_add_channel():
    from utils.watchlist import parse_channel_input, add_channel
    from utils.youtube import resolve_channel_id, get_channel_stats, QuotaError
    body = request.get_json(silent=True) or {}
    raw = (body.get("input") or body.get("channel_id") or "").strip()
    if not raw:
        return jsonify({"ok": False, "error": "kanal URL veya ID gerekli"}), 400
    parsed = parse_channel_input(raw) or raw
    try:
        cid = resolve_channel_id(parsed) if not parsed.startswith("UC") else parsed
        if not cid:
            return jsonify({"ok": False, "error": "kanal bulunamadı — UC... ID veya @handle deneyin"}), 400
        name = None
        try:
            stats = get_channel_stats(cid)
            name = stats.get("name")
        except (QuotaError, RuntimeError):
            pass
        result = add_channel(cid, name=name)
        if not result.get("ok"):
            return jsonify(result), 409
        return jsonify({**result, "name": name, "channel_id": cid})
    except QuotaError as e:
        return jsonify({"ok": False, "error": str(e)}), 502
    except RuntimeError as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/watchlist/video", methods=["POST"])
def api_watchlist_add_video():
    from utils.watchlist import parse_video_input, add_video
    from utils.youtube import get_video_metadata, QuotaError
    body = request.get_json(silent=True) or {}
    raw = (body.get("input") or body.get("video_id") or "").strip()
    if not raw:
        return jsonify({"ok": False, "error": "video URL veya ID gerekli"}), 400
    vid = parse_video_input(raw)
    if not vid:
        return jsonify({"ok": False, "error": "geçersiz video URL/ID"}), 400
    try:
        meta = get_video_metadata(vid)
        if not meta:
            return jsonify({"ok": False, "error": "video bulunamadı"}), 404
        result = add_video(vid, channel_id=meta["channel_id"], title=meta.get("title"))
        if not result.get("ok"):
            return jsonify(result), 409
        return jsonify({**result, "title": meta.get("title"), "channel_id": meta["channel_id"]})
    except QuotaError as e:
        return jsonify({"ok": False, "error": str(e)}), 502
    except RuntimeError as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/watchlist/channel/<channel_id>", methods=["DELETE"])
def api_watchlist_remove_channel(channel_id):
    from utils.watchlist import remove_channel
    result = remove_channel(channel_id)
    return jsonify(result), 200 if result.get("ok") else 404


@app.route("/api/watchlist/video/<video_id>", methods=["DELETE"])
def api_watchlist_remove_video(video_id):
    from utils.watchlist import remove_video
    result = remove_video(video_id)
    return jsonify(result), 200 if result.get("ok") else 404


@app.route("/api/videos")
def api_videos():
    data = read_csv_as_json("videos.csv")
    return jsonify(data or [])


@app.route("/api/clusters")
def api_clusters():
    data = read_csv_as_json("narrative_clusters.csv")
    return jsonify(data or [])


@app.route("/api/ops/summary")
def api_ops_summary():
    from utils.ops_summary import load_ops_summary
    ops_dir = DATA_DIR / "ops_reports"
    summary = load_ops_summary(ops_dir)
    if summary is None:
        return jsonify({"error": "henüz ops raporu yok — pipeline/12_ops_report.py çalıştırın"}), 404
    return jsonify(summary)


@app.route("/api/claims/<int:claim_id>/review", methods=["POST"])
def api_claim_review(claim_id):
    from utils.review import review_claim
    body = request.get_json(silent=True) or {}
    action = (body.get("action") or "").strip().lower()
    note = (body.get("note") or "").strip() or None
    verdict = (body.get("verdict") or "").strip() or None
    result = review_claim(claim_id, action, note=note, verdict=verdict)
    if not result.get("ok"):
        return jsonify(result), 400
    remember_review_patch(result)
    mark_exports_stale()
    return jsonify(result)


@app.route("/healthz")
def healthz():
    """Yerel süreç ayakta mı — CSV varlığı + son pipeline / export turu."""
    return jsonify({
        "status": "ok",
        "suspects_csv_exists": (DATA_DIR / "suspects.csv").exists(),
        "pipeline": pipeline_status(),
        "export": export_status(),
    })


@app.route("/api/export/status")
def api_export_status():
    return jsonify(export_status())


@app.route("/api/pipeline/status")
def api_pipeline_status():
    return jsonify(pipeline_status())


@app.route("/api/pipeline/run", methods=["POST"])
def api_pipeline_run():
    """Yereldeki `run_pipeline.py --watchlist` karşılığı — arka planda kuyruğa al."""
    result = enqueue_pipeline_run(force_watchlist=True)
    return jsonify(result), 200


def pipeline_status():
    return dict(_scheduler_state)


def export_status():
    return dict(_export_state)


def _claim_id_int(value):
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def remember_review_patch(result: dict):
    """CSV yenilenene kadar /api/claims yanıtına DB sonucunu bindir."""
    cid = _claim_id_int(result.get("claim_id"))
    if cid is None:
        return
    patch = {
        "human_reviewed": 1,
        "auto_accepted": 0,
        "reviewer_note": result.get("reviewer_note"),
        "final_verdict": result.get("final_verdict") or result.get("verdict"),
    }
    if result.get("archived"):
        patch["archived_at"] = datetime.now(timezone.utc).isoformat()
        patch["archive_reason"] = result.get("archive_reason")
    with _claim_patches_lock:
        _claim_patches[cid] = patch


def mark_exports_stale():
    _export_state["stale"] = True


def claims_with_patches():
    active = read_csv_as_json("claim_index.csv") or []
    archived = read_csv_as_json("claim_archive.csv") or []
    with _claim_patches_lock:
        patches = dict(_claim_patches)

    def merge(rec):
        cid = _claim_id_int(rec.get("claim_id"))
        if cid is None:
            return rec
        extra = patches.get(cid)
        return {**rec, **extra} if extra else rec

    active_m = [merge(r) for r in active]
    archived_m = [merge(r) for r in archived]
    still_active = []
    for rec in active_m:
        if rec.get("archived_at"):
            archived_m.append(rec)
        else:
            still_active.append(rec)
    return still_active, archived_m


def clear_claim_patches():
    with _claim_patches_lock:
        _claim_patches.clear()


def enqueue_pipeline_run(force_watchlist=False):
    """Pipeline turunu arka plan thread'inde başlat; Flask isteğini bloklamaz."""
    if _scheduler_state.get("running"):
        return {"ok": True, "queued": False, "reason": "already_running"}
    threading.Thread(
        target=run_background_pipeline,
        kwargs={"force_watchlist": force_watchlist},
        daemon=True,
    ).start()
    return {"ok": True, "queued": True}


def _env_flag(name, default=True):
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() not in ("0", "false", "no", "off")


def _env_int(name, default):
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    return int(raw)


def _append_pipeline_log(event, **extra):
    """Arka plan turlarını data/pipeline_scheduler.log'a yazar (yerel kanıt + ops)."""
    payload = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "event": event,
        **extra,
    }
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        with open(DATA_DIR / "pipeline_scheduler.log", "a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except OSError:
        log.exception("pipeline log yazılamadı")


def run_background_pipeline(force_watchlist=False):
    """retrieve→collect→extract→auto-method zincirini arka planda çalıştır.

    Hata Flask sürecini düşürmez: loglanır, sonraki tur normal devam eder.
    PIPELINE_DRY_RUN=1 iken gerçek pipeline çağrılmaz (yerel tetikleyici testi).
    force_watchlist=True (dashboard Analiz et) veya PIPELINE_WATCHLIST=1 iken
    izleme listesindeki kanallar + tekil videolar toplanır.
    """
    if not _job_lock.acquire(blocking=False):
        log.warning("pipeline zaten çalışıyor, bu tur atlandı")
        _append_pipeline_log("skip_already_running")
        return
    _scheduler_state["running"] = True
    _scheduler_state["last_started_at"] = datetime.now(timezone.utc).isoformat()
    _scheduler_state["last_error"] = None
    try:
        from run_pipeline import build_step_plan, run_pipeline

        skip_collect = _env_flag("PIPELINE_SKIP_COLLECT", default=False)
        with_comments = _env_flag("PIPELINE_WITH_COMMENTS", default=False)
        plan = build_step_plan(skip_collect=skip_collect, with_comments=with_comments)
        step_names = [script for script, _ in plan]
        log.info("pipeline turu başlıyor: %s", " → ".join(step_names))
        _append_pipeline_log("start", steps=step_names)

        if _env_flag("PIPELINE_DRY_RUN", default=False):
            sleep_s = max(0, _env_int("PIPELINE_DRY_RUN_SLEEP", 0))
            if sleep_s:
                time.sleep(sleep_s)
            log.info("pipeline dry-run bitti (adımlar çalıştırılmadı)")
            _append_pipeline_log("dry_run_ok", steps=step_names, sleep_s=sleep_s)
            return

        use_watchlist = force_watchlist or _env_flag("PIPELINE_WATCHLIST", default=False)
        run_pipeline(
            channels=os.environ.get("PIPELINE_CHANNELS", "data/channels.csv"),
            max_videos=os.environ.get("PIPELINE_MAX_VIDEOS", "15"),
            skip_collect=skip_collect,
            skip_nli=_env_flag("PIPELINE_SKIP_NLI", default=False),
            with_comments=with_comments,
            watchlist=use_watchlist,
        )
        _append_pipeline_log("ok", steps=step_names)
    except Exception:
        err = traceback.format_exc()
        log.exception("arka plan pipeline turu hata verdi; Flask ayakta kalıyor")
        _scheduler_state["last_error"] = err[-2000:]
        _append_pipeline_log("error", error=_scheduler_state["last_error"])
    finally:
        _scheduler_state["running"] = False
        _scheduler_state["ticks"] = int(_scheduler_state["ticks"] or 0) + 1
        _scheduler_state["last_finished_at"] = datetime.now(timezone.utc).isoformat()
        _job_lock.release()


def run_export_refresh():
    """04 + 06'yı arka planda çalıştır; Flask isteğini bloklamaz."""
    if _scheduler_state.get("running"):
        log.info("pipeline çalışıyor, CSV yenileme bu tur atlandı")
        return
    if not _export_lock.acquire(blocking=False):
        log.info("CSV yenileme zaten çalışıyor, atlandı")
        return
    _export_state["running"] = True
    _export_state["last_started_at"] = datetime.now(timezone.utc).isoformat()
    _export_state["last_error"] = None
    try:
        from utils.review import refresh_dashboard_exports
        refresh_dashboard_exports()
        _export_state["stale"] = False
        _export_state["last_finished_at"] = datetime.now(timezone.utc).isoformat()
        clear_claim_patches()
        log.info("dashboard CSV yenilendi (04 + 06)")
    except Exception:
        err = traceback.format_exc()
        log.exception("CSV yenileme hata verdi; Flask ayakta kalıyor")
        _export_state["last_error"] = err[-2000:]
    finally:
        _export_state["running"] = False
        _export_state["ticks"] = int(_export_state["ticks"] or 0) + 1
        _export_lock.release()


def _export_loop():
    interval = max(1, _env_int("EXPORT_INTERVAL_SECONDS", 180))
    _export_state["interval_seconds"] = interval
    while not _export_stop.wait(interval):
        if _export_state.get("stale"):
            run_export_refresh()


def start_export_refresh():
    """Review sonrası bayraklanan CSV'leri her N saniyede bir yenile."""
    global _export_thread
    if _export_thread is not None and _export_thread.is_alive():
        return _export_thread
    if "pytest" in sys.modules and not _env_flag("EXPORT_REFRESH_FORCE", default=False):
        return None
    if not _env_flag("EXPORT_REFRESH_ENABLED", default=True):
        log.info("CSV yenileme kapalı (EXPORT_REFRESH_ENABLED=0)")
        return None
    interval = max(1, _env_int("EXPORT_INTERVAL_SECONDS", 180))
    _export_state["enabled"] = True
    _export_state["interval_seconds"] = interval
    _export_stop.clear()
    thread = threading.Thread(target=_export_loop, name="export_refresh", daemon=True)
    thread.start()
    _export_thread = thread
    log.info("CSV yenileme başladı: stale ise her %ss", interval)
    return thread


start_export_refresh()


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    debug = _env_flag("FLASK_DEBUG", default=True)
    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 8000)),
        debug=debug,
        use_reloader=False,
    )
