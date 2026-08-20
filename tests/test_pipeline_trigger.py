"""Manuel pipeline tetikleme — yereldeki run_pipeline --watchlist karşılığı."""
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


def _wait_pipeline_idle(appmod, timeout=2.0):
    deadline = time.time() + timeout
    while appmod._scheduler_state.get("running") and time.time() < deadline:
        time.sleep(0.05)
    appmod._scheduler_state["running"] = False
    appmod._scheduler_state["last_error"] = None


def test_pipeline_status_endpoint(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    import app as appmod
    monkeypatch.setattr(appmod, "DATA_DIR", tmp_path)

    client = appmod.app.test_client()
    rv = client.get("/api/pipeline/status")
    assert rv.status_code == 200
    body = rv.get_json()
    assert "running" in body
    assert "ticks" in body


def test_pipeline_run_queues_with_watchlist(monkeypatch, tmp_path):
    monkeypatch.delenv("PIPELINE_DRY_RUN", raising=False)
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    import app as appmod
    monkeypatch.setattr(appmod, "DATA_DIR", tmp_path)

    seen = {}

    def capture(**kwargs):
        seen.update(kwargs)

    monkeypatch.setattr("run_pipeline.run_pipeline", capture)

    client = appmod.app.test_client()
    rv = client.post("/api/pipeline/run")
    assert rv.status_code == 200
    data = rv.get_json()
    assert data["ok"] is True
    assert data["queued"] is True

    deadline = time.time() + 3
    while time.time() < deadline and not seen:
        time.sleep(0.05)
    assert seen.get("watchlist") is True


def test_pipeline_run_already_running(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    import app as appmod
    monkeypatch.setattr(appmod, "DATA_DIR", tmp_path)
    appmod._scheduler_state["running"] = True

    client = appmod.app.test_client()
    rv = client.post("/api/pipeline/run")
    assert rv.status_code == 200
    data = rv.get_json()
    assert data["queued"] is False
    assert data["reason"] == "already_running"

    appmod._scheduler_state["running"] = False


def test_enqueue_pipeline_run_second_call_while_running(monkeypatch, tmp_path):
    monkeypatch.delenv("PIPELINE_DRY_RUN", raising=False)
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    import app as appmod
    monkeypatch.setattr(appmod, "DATA_DIR", tmp_path)

    started = threading.Event()

    def slow(**kwargs):
        started.set()
        time.sleep(0.3)

    monkeypatch.setattr("run_pipeline.run_pipeline", slow)

    first = appmod.enqueue_pipeline_run(force_watchlist=True)
    assert first["queued"] is True
    assert started.wait(1)

    second = appmod.enqueue_pipeline_run(force_watchlist=True)
    assert second["queued"] is False
    assert second["reason"] == "already_running"

    _wait_pipeline_idle(appmod)
