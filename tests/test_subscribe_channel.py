"""20_subscribe_channel — maliyet tahmini (veri-temelli) + dry-run collect yok."""
import hashlib
import importlib.util
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

ROOT = Path(__file__).parent.parent


def _load_mod():
    spec = importlib.util.spec_from_file_location(
        "subscribe20", ROOT / "pipeline" / "20_subscribe_channel.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_parse_channel_id_and_url():
    mod = _load_mod()
    cid = "UCXhDI7n_iC4J9jR3GYJKkcQ"
    assert mod.parse_channel_arg(cid, None) == cid
    assert mod.parse_channel_arg(
        None, f"https://www.youtube.com/channel/{cid}"
    ) == cid


def test_avg_chunks_matches_extraction_files():
    mod = _load_mod()
    out = mod.avg_chunks_from_files(ROOT / "data" / "extraction_chunks")
    assert out["per_video"]["odZgEDFDmbE"] == 4
    assert out["per_video"]["bZsorXWeLhM"] == 5
    assert out["per_video"]["jP5XF06OLbo"] == 5
    assert abs(out["avg"] - (4 + 5 + 5) / 3) < 1e-9


def test_avg_cost_per_chunk_from_jP5_usage():
    mod = _load_mod()
    out = mod.avg_cost_per_chunk_from_usage(ROOT / "data")
    assert out["total_chunks"] == 5
    # 32689 * $2/M + 10477 * $10/M = $0.170148
    assert abs(out["total_cost_usd"] - 0.170148) < 1e-6
    assert abs(out["avg"] - 0.170148 / 5) < 1e-9


def test_avg_cost_per_claim_from_554_close():
    mod = _load_mod()
    out = mod.avg_cost_per_claim_from_ops(ROOT / "data" / "ops_reports")
    assert "554-close" in out["source"]
    assert abs(out["avg"] - 0.03280885317604356) < 1e-9


def test_hasan_channel_claims_average():
    from utils.db import get_conn

    mod = _load_mod()
    conn = get_conn()
    try:
        out = mod.avg_claims_per_video(conn, "UC83SKJrkGxPAkhK1aPosw7A")
    finally:
        conn.close()
    assert out["source"] == "channel"
    assert out["claims"] == 718
    assert out["videos"] == 14
    assert abs(out["avg"] - 718 / 14) < 1e-9


def test_unknown_channel_falls_back_to_global():
    from utils.db import get_conn

    mod = _load_mod()
    conn = get_conn()
    try:
        out = mod.avg_claims_per_video(conn, "UCdoesnotexist00000000000")
        overall = conn.execute(
            """
            SELECT COUNT(*) AS claims, COUNT(DISTINCT video_id) AS videos
            FROM claims WHERE archived_at IS NULL
            """
        ).fetchone()
    finally:
        conn.close()
    assert out["source"] == "global"
    assert out["claims"] == int(overall["claims"])
    assert out["videos"] == int(overall["videos"])


def test_estimate_formula():
    mod = _load_mod()
    est = mod.estimate_costs(
        62,
        {"avg": 14 / 3, "n_videos": 3, "total_chunks": 14, "per_video": {}},
        {"avg": 0.034, "samples": []},
        {"avg": 41.0, "source": "channel", "claims": 41, "videos": 1},
        {"avg": 0.0328, "source": "x"},
    )
    assert abs(est["extraction_usd"] - 62 * (14 / 3) * 0.034) < 1e-9
    assert abs(est["factcheck_usd"] - 62 * 41.0 * 0.0328) < 1e-9
    assert abs(est["total_usd"] - (est["extraction_usd"] + est["factcheck_usd"])) < 1e-12


def test_dry_run_does_not_call_collect(monkeypatch, capsys):
    mod = _load_mod()
    cid = "UCXhDI7n_iC4J9jR3GYJKkcQ"
    wl_path = ROOT / "data" / "watchlist.json"
    before_hash = _file_hash(wl_path)

    def _boom(*_a, **_k):
        raise AssertionError("collect_channel_videos dry-run'da çağrılmamalı")

    monkeypatch.setattr(
        mod,
        "get_channel_stats",
        lambda _cid: {
            "channel_id": cid,
            "name": "Dr. Stefen Radoslaw ile Fizyoterapi",
            "description": "",
            "subscribers": 0,
            "total_videos": 63,
            "total_views": 0,
            "uploads_playlist": "UUXhDI7n_iC4J9jR3GYJKkcQ",
        },
    )
    monkeypatch.setattr(mod, "collect_channel_videos", _boom)
    monkeypatch.setattr(mod, "add_channel", _boom)
    monkeypatch.setattr(mod, "run_extract_channel", _boom)
    monkeypatch.setattr(mod, "run_factcheck_channel", _boom)
    monkeypatch.setattr(mod, "count_processed_videos", lambda *_a, **_k: 0)

    from utils.db import get_conn
    conn = get_conn()
    try:
        n_before = conn.execute(
            "SELECT COUNT(*) AS n FROM videos WHERE channel_id = ?", (cid,)
        ).fetchone()["n"]
    finally:
        conn.close()

    rc = mod.main(["--channel-id", cid, "--dry-run"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "collect_started=false" in out
    assert "hayır" in out
    assert "tahmini maliyet" in out
    assert "kanalın tamamı işlenecek" in out
    assert "extract + fact-check" in out
    assert "yalnızca collect" not in out

    conn = get_conn()
    try:
        n_after = conn.execute(
            "SELECT COUNT(*) AS n FROM videos WHERE channel_id = ?", (cid,)
        ).fetchone()["n"]
    finally:
        conn.close()
    assert n_after == n_before
    assert _file_hash(wl_path) == before_hash


def test_hayir_via_input_does_not_collect(monkeypatch):
    mod = _load_mod()
    called = {"collect": 0}

    monkeypatch.setattr(
        mod,
        "get_channel_stats",
        lambda _cid: {
            "channel_id": "UCXhDI7n_iC4J9jR3GYJKkcQ",
            "name": "Stefen",
            "description": "",
            "subscribers": 1,
            "total_videos": 63,
            "total_views": 1,
            "uploads_playlist": "UUXhDI7n_iC4J9jR3GYJKkcQ",
        },
    )
    monkeypatch.setattr("builtins.input", lambda *_a, **_k: "hayır")
    monkeypatch.setattr(
        mod,
        "collect_channel_videos",
        lambda *_a, **_k: called.__setitem__("collect", called["collect"] + 1),
    )
    monkeypatch.setattr(mod, "run_extract_channel", lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("extract")))
    monkeypatch.setattr(mod, "run_factcheck_channel", lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("fc")))
    monkeypatch.setattr(mod, "count_processed_videos", lambda *_a, **_k: 0)
    monkeypatch.setattr(mod, "add_channel", lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("watchlist")))
    rc = mod.main(["--channel-id", "UCXhDI7n_iC4J9jR3GYJKkcQ"])
    assert rc == 0
    assert called["collect"] == 0


def test_format_confirm_scope_new_videos():
    mod = _load_mod()
    est = {
        "total_usd": 12.34,
        "extraction_usd": 4.0,
        "factcheck_usd": 8.34,
    }
    out = mod.format_confirm_scope(
        n_new=62,
        estimate=est,
        pending_extract=0,
        pending_claims=0,
        pending_extract_usd=0.0,
        pending_factcheck_usd=0.0,
    )
    assert out["should_ask"] is True
    assert "62 yeni video için kanalın tamamı işlenecek" in out["body"]
    assert "extract + fact-check" in out["body"]
    assert "$12.34" in out["body"]
    assert "Kanalın tamamı extract+fact-check edilecek" in out["prompt"]


def test_format_confirm_scope_n_new_zero_pending_claims():
    mod = _load_mod()
    out = mod.format_confirm_scope(
        n_new=0,
        estimate={"total_usd": 0, "extraction_usd": 0, "factcheck_usd": 0},
        pending_extract=0,
        pending_claims=40,
        pending_extract_usd=0.0,
        pending_factcheck_usd=1.31,
    )
    assert out["should_ask"] is True
    assert "YENİ video yok" in out["body"]
    assert "40 bekleyen iddia" in out["body"]
    assert "extract edilmiş ama fact-check edilmemiş" in out["body"]
    assert "Tahmini maliyet: $1.31" in out["body"]


def test_format_confirm_scope_n_new_zero_nothing_pending():
    mod = _load_mod()
    out = mod.format_confirm_scope(
        n_new=0,
        estimate={"total_usd": 0, "extraction_usd": 0, "factcheck_usd": 0},
        pending_extract=0,
        pending_claims=0,
        pending_extract_usd=0.0,
        pending_factcheck_usd=0.0,
    )
    assert out["should_ask"] is False
    assert "bekleyen extract veya fact-check de yok" in out["body"]


def test_n_new_zero_pending_claims_dry_run_message(monkeypatch, capsys):
    mod = _load_mod()
    cid = "UCXhDI7n_iC4J9jR3GYJKkcQ"

    def _boom(*_a, **_k):
        raise AssertionError("dry-run'da collect/extract/fact-check çağrılmamalı")

    monkeypatch.setattr(
        mod,
        "get_channel_stats",
        lambda _cid: {
            "channel_id": cid,
            "name": "Stefen",
            "description": "",
            "subscribers": 0,
            "total_videos": 63,
            "total_views": 0,
            "uploads_playlist": "UUXhDI7n_iC4J9jR3GYJKkcQ",
        },
    )
    monkeypatch.setattr(mod, "count_processed_videos", lambda *_a, **_k: 63)
    monkeypatch.setattr(mod, "count_pending_extract_videos", lambda *_a, **_k: 0)
    monkeypatch.setattr(mod, "count_pending_factcheck_claims", lambda *_a, **_k: 40)
    monkeypatch.setattr(mod, "collect_channel_videos", _boom)
    monkeypatch.setattr(mod, "add_channel", _boom)
    monkeypatch.setattr(mod, "run_extract_channel", _boom)
    monkeypatch.setattr(mod, "run_factcheck_channel", _boom)

    rc = mod.main(["--channel-id", cid, "--dry-run"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "YENİ video yok" in out
    assert "40 bekleyen iddia" in out
    assert "Tahmini maliyet:" in out
    assert "collect_started=false" in out


def test_avg_chunks_from_tmp(tmp_path):
    mod = _load_mod()
    (tmp_path / "a.json").write_text(json.dumps({"video_id": "a", "chunks": [{}, {}, {}, {}]}), encoding="utf-8")
    (tmp_path / "b.json").write_text(json.dumps({"video_id": "b", "chunks": [{}, {}, {}, {}, {}]}), encoding="utf-8")
    out = mod.avg_chunks_from_files(tmp_path)
    assert abs(out["avg"] - 4.5) < 1e-9
    assert out["n_videos"] == 2
