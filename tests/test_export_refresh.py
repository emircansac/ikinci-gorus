"""Review sonrası CSV yenileme: periyodik, tıklamada değil."""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


def _stop_export(appmod):
    appmod._export_stop.set()
    t = appmod._export_thread
    if t is not None:
        t.join(timeout=2)
    appmod._export_thread = None
    appmod._export_stop.clear()
    appmod._export_state["enabled"] = False
    appmod._export_state["stale"] = False
    appmod._export_state["running"] = False
    appmod.clear_claim_patches()


def test_review_endpoint_does_not_call_export(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    import app as appmod
    monkeypatch.setattr(appmod, "DATA_DIR", tmp_path)

    called = {"n": 0}

    def boom():
        called["n"] += 1
        raise RuntimeError("export must not run in review request")

    monkeypatch.setattr("utils.review.refresh_dashboard_exports", boom)
    monkeypatch.setattr("utils.review.review_claim", lambda *a, **k: {
        "ok": True, "claim_id": 42, "action": "approve", "archived": False,
        "archive_reason": None, "human_reviewed": 1, "final_verdict": "yanlış",
        "reviewer_note": "onaylandı",
    })

    client = appmod.app.test_client()
    t0 = time.perf_counter()
    rv = client.post("/api/claims/42/review", json={"action": "approve"})
    elapsed = time.perf_counter() - t0
    assert rv.status_code == 200
    assert rv.get_json()["ok"] is True
    assert called["n"] == 0
    assert elapsed < 0.5
    assert appmod._export_state["stale"] is True


def test_claims_api_applies_review_patch_before_csv_refresh(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    import pandas as pd
    import app as appmod
    monkeypatch.setattr(appmod, "DATA_DIR", tmp_path)
    tmp_path.mkdir(parents=True, exist_ok=True)
    pd.DataFrame([{
        "claim_id": 42, "claim_text": "x", "human_reviewed": 0, "auto_accepted": 0,
        "final_verdict": "yanlış", "archived_at": None,
    }]).to_csv(tmp_path / "claim_index.csv", index=False)

    appmod.clear_claim_patches()
    appmod.remember_review_patch({
        "claim_id": 42, "archived": False, "human_reviewed": 1,
        "final_verdict": "yanlış", "reviewer_note": "onaylandı",
    })
    client = appmod.app.test_client()
    rows = client.get("/api/claims").get_json()
    assert len(rows) == 1
    assert rows[0]["human_reviewed"] == 1
    assert rows[0]["reviewer_note"] == "onaylandı"
    appmod.clear_claim_patches()


def test_export_loop_runs_when_stale(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("EXPORT_INTERVAL_SECONDS", "1")
    monkeypatch.setenv("EXPORT_REFRESH_FORCE", "1")
    import app as appmod
    monkeypatch.setattr(appmod, "DATA_DIR", tmp_path)
    _stop_export(appmod)
    appmod._scheduler_state["running"] = False

    ran = {"n": 0}

    def fake_export():
        ran["n"] += 1

    monkeypatch.setattr("utils.review.refresh_dashboard_exports", fake_export)
    appmod._export_state["ticks"] = 0
    appmod.mark_exports_stale()
    appmod.start_export_refresh()
    deadline = time.time() + 3.5
    while time.time() < deadline and ran["n"] < 1:
        time.sleep(0.05)
    ticks = appmod._export_state["ticks"]
    finished = appmod._export_state["last_finished_at"]
    stale = appmod._export_state["stale"]
    _stop_export(appmod)
    assert ran["n"] >= 1
    assert ticks >= 1
    assert finished
    assert stale is False


def test_healthz_includes_export_status(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    import app as appmod
    monkeypatch.setattr(appmod, "DATA_DIR", tmp_path)
    rv = appmod.app.test_client().get("/healthz")
    body = rv.get_json()
    assert body["status"] == "ok"
    assert "export" in body
    assert "stale" in body["export"]
    assert "last_finished_at" in body["export"]
