"""
Web sunucusu — Render'a deploy edildiğinde bir web adresi versin diye.

Yaptığı iş: pipeline'ın ürettiği CSV dosyalarını (data/suspects.csv,
data/claim_index.csv, data/claim_archive.csv, data/narrative_clusters.csv,
data/videos.csv) okuyup JSON olarak sunar, dashboard bu JSON'ı fetch() ile
çekip gösterir.

BİLİNÇLİ TASARIM: veritabanına (SQLite) doğrudan bağlanmak yerine SADECE
CSV çıktılarını okuyor. Neden: pipeline'ın "doğruluk kaynağı" zaten bu CSV'ler
(06_claim_index.py, 04_score_suspects.py tarafından üretiliyor) — web sunucusu
kendi sorgu mantığını (join, filtre) tekrar yazıp DB şemasıyla sıkı bağlı
olmaktansa, tek bir yerden (CSV) okuyor. Şema değişse bile bu dosya bozulmaz.

ZAMANLAYICI: Render cron job'una disk bağlanamadığı için pipeline bu süreç
içinde, APScheduler arka plan thread'inde çalışır. Flask istek sunumunu
bloklamaz; bir tur hata verse bile süreç ayakta kalır. 20/21 interaktif
script'ler burada yok (yalnız yerel/Cursor).

Yerel çalıştırma:
    pip install flask pandas APScheduler
    python app.py
    -> http://localhost:8000

Render'da:
    Start command: gunicorn --workers 1 app:app
"""
import atexit
import json
import logging
import math
import os
import subprocess
import sys
import threading
import time
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
from flask import Flask, jsonify, render_template, request

try:
    import fcntl
except ImportError:  # Windows; Render/macOS'ta fcntl var
    fcntl = None

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

_scheduler = None
_scheduler_lock_fh = None
_job_lock = threading.Lock()
_scheduler_state = {
    "enabled": False,
    "interval_seconds": None,
    "last_started_at": None,
    "last_finished_at": None,
    "last_error": None,
    "running": False,
    "ticks": 0,
}


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
    data = read_csv_as_json("claim_index.csv")
    if data is None:
        return jsonify([])
    return jsonify(data)


@app.route("/api/claims/archived")
def api_claims_archived():
    data = read_csv_as_json("claim_archive.csv")
    return jsonify(data or [])


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
    try:
        result = review_claim(claim_id, action, note=note, verdict=verdict)
        if not result.get("ok"):
            return jsonify(result), 400
        return jsonify(result)
    except subprocess.CalledProcessError as e:
        return jsonify({"ok": False, "error": f"export güncellenemedi: {e}"}), 500


@app.route("/healthz")
def healthz():
    """Render'ın servisin ayakta olup olmadığını kontrol etmesi için basit bir uç nokta."""
    return jsonify({
        "status": "ok",
        "suspects_csv_exists": (DATA_DIR / "suspects.csv").exists(),
        "scheduler": dict(_scheduler_state),
    })


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


def _pipeline_interval_seconds():
    if os.environ.get("PIPELINE_INTERVAL_SECONDS", "").strip():
        return max(1, _env_int("PIPELINE_INTERVAL_SECONDS", 86400))
    hours = max(1, _env_int("PIPELINE_INTERVAL_HOURS", 24))
    return hours * 3600


def _append_scheduler_log(event, **extra):
    """Zamanlayıcı turlarını data/pipeline_scheduler.log'a yazar (yerel kanıt + ops)."""
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
        log.exception("scheduler log yazılamadı")


def run_scheduled_pipeline():
    """retrieve→collect→extract→auto-method zincirini arka planda çalıştır.

    Hata Flask sürecini düşürmez: loglanır, sonraki tur normal devam eder.
    PIPELINE_DRY_RUN=1 iken gerçek pipeline çağrılmaz (yerel zamanlayıcı testi).
    """
    if not _job_lock.acquire(blocking=False):
        log.warning("pipeline zaten çalışıyor, bu tur atlandı")
        _append_scheduler_log("skip_already_running")
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
        _append_scheduler_log("start", steps=step_names)

        if _env_flag("PIPELINE_DRY_RUN", default=False):
            sleep_s = max(0, _env_int("PIPELINE_DRY_RUN_SLEEP", 0))
            if sleep_s:
                time.sleep(sleep_s)
            log.info("pipeline dry-run bitti (adımlar çalıştırılmadı)")
            _append_scheduler_log("dry_run_ok", steps=step_names, sleep_s=sleep_s)
            return

        run_pipeline(
            channels=os.environ.get("PIPELINE_CHANNELS", "data/channels.csv"),
            max_videos=os.environ.get("PIPELINE_MAX_VIDEOS", "15"),
            skip_collect=skip_collect,
            skip_nli=_env_flag("PIPELINE_SKIP_NLI", default=False),
            with_comments=with_comments,
            watchlist=_env_flag("PIPELINE_WATCHLIST", default=False),
        )
        _append_scheduler_log("ok", steps=step_names)
    except Exception:
        err = traceback.format_exc()
        log.exception("zamanlanmış pipeline turu hata verdi; Flask ayakta kalıyor")
        _scheduler_state["last_error"] = err[-2000:]
        _append_scheduler_log("error", error=_scheduler_state["last_error"])
    finally:
        _scheduler_state["running"] = False
        _scheduler_state["ticks"] = int(_scheduler_state["ticks"] or 0) + 1
        _scheduler_state["last_finished_at"] = datetime.now(timezone.utc).isoformat()
        _job_lock.release()


def _acquire_scheduler_lock():
    """gunicorn worker>1 olursa yalnızca bir süreç zamanlayıcıyı tutar."""
    global _scheduler_lock_fh
    if fcntl is None:
        return True
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    lock_path = DATA_DIR / ".scheduler.lock"
    fh = open(lock_path, "a+")
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fh.close()
        log.info("zamanlayıcı kilidi başka süreçte; bu worker atlıyor")
        return False
    _scheduler_lock_fh = fh
    return True


def start_pipeline_scheduler():
    """Flask/gunicorn süreci ayağa kalkınca arka plan zamanlayıcıyı başlat."""
    global _scheduler
    if _scheduler is not None:
        return _scheduler
    if "pytest" in sys.modules and not _env_flag("PIPELINE_SCHEDULER_FORCE", default=False):
        return None
    if not _env_flag("PIPELINE_SCHEDULER_ENABLED", default=bool(os.environ.get("RENDER"))):
        log.info("zamanlayıcı kapalı (yerel varsayılan; Render'da veya PIPELINE_SCHEDULER_ENABLED=1 ile açılır)")
        return None
    # Flask debug reloader ebeveyn süreçte çift zamanlayıcı açmasın
    if os.environ.get("WERKZEUG_RUN_MAIN") == "false":
        return None
    if not _acquire_scheduler_lock():
        return None

    try:
        from apscheduler.schedulers.background import BackgroundScheduler
    except ImportError:
        log.error("APScheduler kurulu değil; zamanlayıcı başlatılamadı")
        return None

    interval = _pipeline_interval_seconds()
    delay = _env_int("PIPELINE_INITIAL_DELAY_SECONDS", 60)
    _scheduler_state["enabled"] = True
    _scheduler_state["interval_seconds"] = interval

    scheduler = BackgroundScheduler(daemon=True)
    scheduler.add_job(
        run_scheduled_pipeline,
        "interval",
        seconds=interval,
        id="health_pipeline",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        next_run_time=datetime.now() + timedelta(seconds=max(0, delay)),
    )
    scheduler.start()
    _scheduler = scheduler
    atexit.register(lambda: scheduler.shutdown(wait=False))
    log.info(
        "pipeline zamanlayıcı başladı: ilk tur %ss sonra, sonra her %ss",
        delay, interval,
    )
    _append_scheduler_log("scheduler_started", delay_s=delay, interval_s=interval)
    return scheduler


start_pipeline_scheduler()


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    debug = _env_flag("FLASK_DEBUG", default=True)
    # Reloader ikinci süreç açar ve zamanlayıcıyı çiftler; kapalı tut.
    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 8000)),
        debug=debug,
        use_reloader=False,
    )
