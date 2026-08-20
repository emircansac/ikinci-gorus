"""21_pre_research_channel — örneklem, ilk kapı abort, maliyet formülü, 04 yazılmaz."""
import hashlib
import importlib.util
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

ROOT = Path(__file__).parent.parent
CID = "UCXhDI7n_iC4J9jR3GYJKkcQ"


def _load_mod():
    spec = importlib.util.spec_from_file_location(
        "pre_research21", ROOT / "pipeline" / "21_pre_research_channel.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _fake_catalog(n=8):
    return [
        {"video_id": f"vid{i:02d}xxxxx", "title": f"Video {i}", "published_at": "2026-01-01"}
        for i in range(n)
    ]


def test_pick_sample_returns_three():
    mod = _load_mod()
    catalog = _fake_catalog(10)
    sample = mod.pick_sample(catalog, 3)
    assert len(sample) == 3
    ids = {v["video_id"] for v in sample}
    assert ids <= {v["video_id"] for v in catalog}


def test_pick_sample_smaller_catalog():
    mod = _load_mod()
    catalog = _fake_catalog(2)
    sample = mod.pick_sample(catalog, 3)
    assert len(sample) == 2


def test_estimate_costs_times_three():
    mod = _load_mod()
    est = mod.sub20.estimate_costs(
        3,
        {"avg": 14 / 3, "n_videos": 3, "total_chunks": 14, "per_video": {}},
        {"avg": 0.034, "samples": []},
        {"avg": 0.0, "source": "n/a", "claims": 0, "videos": 0},
        {"avg": 0.0328, "source": "x"},
    )
    assert abs(est["extraction_usd"] - 3 * (14 / 3) * 0.034) < 1e-9
    assert est["n_new"] == 3


def test_extraction_band_from_chunk_variance_not_safety_pad():
    mod = _load_mod()
    est = mod.extraction_estimate_from_chunk_variance(
        3,
        {"avg": 14 / 3, "per_video": {"odZg": 4, "bZsor": 5, "jP5": 5}},
        {"avg": 0.034},
    )
    assert est["is_range"] is True
    assert est["min_chunks"] == 4
    assert est["max_chunks"] == 5
    assert abs(est["cost_low"] - 3 * 4 * 0.034) < 1e-9
    assert abs(est["cost_high"] - 3 * 5 * 0.034) < 1e-9
    line = mod.format_gate1_extraction_line(est)
    assert "$0.50" not in line
    assert "güvenlik payı değil" in line
    assert "$0.41" in line and "$0.51" in line


def test_extraction_band_collapses_when_no_chunk_variance():
    mod = _load_mod()
    est = mod.extraction_estimate_from_chunk_variance(
        3,
        {"avg": 5, "per_video": {"a": 5, "b": 5}},
        {"avg": 0.034},
    )
    assert est["is_range"] is False
    line = mod.format_gate1_extraction_line(est)
    assert "–" not in line
    assert "varyansı yok" in line
    assert abs(est["cost_low"] - est["cost_high"]) < 1e-12


def test_informational_risk_does_not_write_channel_risk_scores():
    from utils.db import get_conn

    mod = _load_mod()
    conn = get_conn()
    try:
        before = conn.execute("SELECT COUNT(*) AS n FROM channel_risk_scores").fetchone()["n"]
        stats = {
            "channel_id": CID,
            "description": "Sağlık eğitim videosu",
            "name": "Stefen",
        }
        risk = mod.compute_informational_risk(conn, stats, ["P4m9F9mykQ8"])
        after = conn.execute("SELECT COUNT(*) AS n FROM channel_risk_scores").fetchone()["n"]
    finally:
        conn.close()
    assert after == before
    assert "score" in risk
    assert "funnel_flag" in risk
    assert "ai_persona_flag" in risk


def test_dry_run_first_gate_does_not_collect_or_extract(monkeypatch, capsys):
    mod = _load_mod()
    wl_path = ROOT / "data" / "watchlist.json"
    before_hash = _file_hash(wl_path)
    catalog = _fake_catalog(6)

    def _boom(*_a, **_k):
        raise AssertionError("dry-run'da collect/extract çağrılmamalı")

    monkeypatch.setattr(
        mod.sub20,
        "get_channel_stats",
        lambda _cid: {
            "channel_id": CID,
            "name": "Dr. Stefen Radoslaw ile Fizyoterapi",
            "description": "",
            "subscribers": 0,
            "total_videos": 64,
            "total_views": 0,
            "uploads_playlist": "UUXhDI7n_iC4J9jR3GYJKkcQ",
        },
    )
    monkeypatch.setattr(mod.sub20, "_list_upload_videos", lambda *_a, **_k: catalog)
    monkeypatch.setattr(mod, "collect_selected_videos", _boom)
    monkeypatch.setattr(mod, "extract_selected_videos", _boom)
    monkeypatch.setattr(mod, "run_factcheck_videos", _boom)

    from utils.db import get_conn
    conn = get_conn()
    try:
        n_vid = conn.execute(
            "SELECT COUNT(*) AS n FROM videos WHERE channel_id = ?", (CID,)
        ).fetchone()["n"]
        n_claims = conn.execute("SELECT COUNT(*) AS n FROM claims").fetchone()["n"]
    finally:
        conn.close()

    rc = mod.main(["--channel-id", CID, "--dry-run"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "collect_started=false" in out
    assert "extract_started=false" in out
    assert "factcheck_started=false" in out
    assert "kaba extraction tahmini" in out
    assert "hayır" in out
    assert "~$0.50" not in out
    assert "güvenlik payı değil" in out
    assert "$0.41–$0.51" in out or "$0.41-$0.51" in out

    conn = get_conn()
    try:
        n_vid2 = conn.execute(
            "SELECT COUNT(*) AS n FROM videos WHERE channel_id = ?", (CID,)
        ).fetchone()["n"]
        n_claims2 = conn.execute("SELECT COUNT(*) AS n FROM claims").fetchone()["n"]
    finally:
        conn.close()
    assert n_vid2 == n_vid
    assert n_claims2 == n_claims
    assert _file_hash(wl_path) == before_hash


def test_sample_cost_inputs_uses_three_video_claims_not_channel_wide():
    """Kalan tahmin avg_claims örneklem videolarından gelir (kanal geneli değil)."""
    from utils.db import get_conn

    mod = _load_mod()
    conn = get_conn()
    try:
        _chunks, _per_chunk, claims, per_claim = mod.sample_cost_inputs(
            conn,
            ["P4m9F9mykQ8"],
            extraction_usd=0.0,
            extraction_calls=0,
            factcheck_usd=None,
            factcheck_n=0,
            historical_chunks={"avg": 4.67, "n_videos": 3, "total_chunks": 14, "per_video": {}},
            historical_per_chunk={"avg": 0.034, "samples": []},
            historical_per_claim={"avg": 0.0328, "source": "ops"},
        )
    finally:
        conn.close()
    assert claims["source"] == "sample3"
    assert claims["videos"] == 1
    assert claims["claims"] == 41
    assert abs(claims["avg"] - 41.0) < 1e-9
    assert per_claim["avg"] == 0.0328
