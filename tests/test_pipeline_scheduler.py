"""Arka plan pipeline tetikleyicisi: adım sırası, hata yalıtımı, Flask bloklamama."""
import json
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from run_pipeline import build_step_plan


def _wait_pipeline_idle(appmod, timeout=2.0):
    deadline = time.time() + timeout
    while appmod._scheduler_state.get("running") and time.time() < deadline:
        time.sleep(0.05)
    appmod._scheduler_state["running"] = False
    appmod._scheduler_state["last_error"] = None


def test_step_plan_is_retrieve_collect_extract_automethod_then_score_index():
    plan = build_step_plan()
    scripts = [name for name, _ in plan]
    templates = [args for _, args in plan]
    assert scripts == [
        "03_factcheck.py",
        "01_collect.py",
        "02_extract_claims.py",
        "03_factcheck.py",
        "04_score_suspects.py",
        "06_claim_index.py",
    ]
    assert templates[0] == ["--batch-retrieve"]
    assert "--auto-method" in templates[3]
    assert "20_subscribe_channel.py" not in scripts
    assert "21_pre_research_channel.py" not in scripts


def test_skip_collect_drops_01_keeps_retrieve_extract_automethod():
    scripts = [name for name, _ in build_step_plan(skip_collect=True)]
    assert scripts[0] == "03_factcheck.py"
    assert "01_collect.py" not in scripts
    assert scripts[1:3] == ["02_extract_claims.py", "03_factcheck.py"]


def test_background_job_error_does_not_propagate(monkeypatch, tmp_path):
    monkeypatch.setenv("PIPELINE_DRY_RUN", "0")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    import app as appmod
    monkeypatch.setattr(appmod, "DATA_DIR", tmp_path)
    _wait_pipeline_idle(appmod)

    def boom(**kwargs):
        raise RuntimeError("simulated pipeline crash")

    monkeypatch.setattr("run_pipeline.run_pipeline", boom)
    appmod.run_background_pipeline()  # must not raise
    assert appmod._scheduler_state["last_error"]
    assert "simulated pipeline crash" in appmod._scheduler_state["last_error"]
    assert appmod._scheduler_state["running"] is False
    assert appmod._scheduler_state["ticks"] >= 1


def test_flask_serves_healthz_while_pipeline_thread_sleeps(monkeypatch, tmp_path):
    monkeypatch.setenv("PIPELINE_DRY_RUN", "1")
    monkeypatch.setenv("PIPELINE_DRY_RUN_SLEEP", "2")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    import app as appmod
    monkeypatch.setattr(appmod, "DATA_DIR", tmp_path)
    _wait_pipeline_idle(appmod)

    started = threading.Event()
    orig = appmod.run_background_pipeline

    def wrapped():
        started.set()
        orig()

    t = threading.Thread(target=wrapped, daemon=True)
    t.start()
    assert started.wait(1)
    # job is sleeping ~2s; Flask test client must still answer quickly
    client = appmod.app.test_client()
    t0 = time.perf_counter()
    rv = client.get("/healthz")
    elapsed = time.perf_counter() - t0
    assert rv.status_code == 200
    body = rv.get_json()
    assert body["status"] == "ok"
    assert elapsed < 0.75, f"healthz blocked for {elapsed:.2f}s"
    t.join(timeout=5)
    assert not t.is_alive()
    log_path = tmp_path / "pipeline_scheduler.log"
    assert log_path.exists()
    events = [json.loads(line)["event"] for line in log_path.read_text().splitlines() if line.strip()]
    assert "start" in events
    assert "dry_run_ok" in events
    start_line = next(
        json.loads(line) for line in log_path.read_text().splitlines()
        if json.loads(line).get("event") == "start"
    )
    assert start_line["steps"][:4] == [
        "03_factcheck.py",
        "01_collect.py",
        "02_extract_claims.py",
        "03_factcheck.py",
    ]
