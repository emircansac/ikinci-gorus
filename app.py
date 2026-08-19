"""
Web sunucusu — Render'a deploy edildiğinde bir web adresi versin diye.

Yaptığı iş basit: pipeline'ın ürettiği CSV dosyalarını (data/suspects.csv,
data/claim_index.csv, data/claim_archive.csv, data/narrative_clusters.csv,
data/videos.csv) okuyup JSON olarak sunar, dashboard bu JSON'ı fetch() ile
çekip gösterir.

BİLİNÇLİ TASARIM: veritabanına (SQLite) doğrudan bağlanmak yerine SADECE
CSV çıktılarını okuyor. Neden: pipeline'ın "doğruluk kaynağı" zaten bu CSV'ler
(06_claim_index.py, 04_score_suspects.py tarafından üretiliyor) — web sunucusu
kendi sorgu mantığını (join, filtre) tekrar yazıp DB şemasıyla sıkı bağlı
olmaktansa, tek bir yerden (CSV) okuyor. Şema değişse bile bu dosya bozulmaz.

ÖNEMLİ AYRIM: bu dosya pipeline'ı ÇALIŞTIRMAZ (o hâlâ run_pipeline.py'nin işi,
ayrı zamanlanmış bir görev olarak — ör. Render'da ayrı bir "Cron Job" servisi).
Bu sadece SONUÇLARI göstermek için var. İkisini ayrı tutuyoruz çünkü pipeline
uzun sürebilir (dakikalar) ve bir web isteğinin süresini aşabilir — web
sunucusu sadece hazır veriyi okuyup göstermeli, ağır işi tetiklememeli.

Yerel çalıştırma:
    pip install flask pandas
    python app.py
    -> http://localhost:8000

Render'da:
    Start command: gunicorn app:app
"""
import os
import json
import math
import subprocess
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
    try:
        result = review_claim(claim_id, action, note=note)
        if not result.get("ok"):
            return jsonify(result), 400
        return jsonify(result)
    except subprocess.CalledProcessError as e:
        return jsonify({"ok": False, "error": f"export güncellenemedi: {e}"}), 500


@app.route("/healthz")
def healthz():
    """Render'ın servisin ayakta olup olmadığını kontrol etmesi için basit bir uç nokta."""
    return jsonify({"status": "ok", "suspects_csv_exists": (DATA_DIR / "suspects.csv").exists()})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8000)), debug=True)
