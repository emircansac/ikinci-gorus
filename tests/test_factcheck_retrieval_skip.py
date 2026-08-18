"""retrieve_hybrid_evidence 3. çağrıda patlarsa o claim atlanır, kalanlar job'a girer."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))


def _load_factcheck():
    spec = importlib.util.spec_from_file_location(
        "factcheck03", ROOT / "pipeline" / "03_factcheck.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_retrieve_failure_skips_third_claim_rest_enter_job(monkeypatch, tmp_path, capsys):
    from utils.db import get_conn

    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT claim_id FROM claims WHERE archived_at IS NULL ORDER BY claim_id LIMIT 3"
        ).fetchall()
    finally:
        conn.close()
    if len(rows) < 3:
        pytest.skip("need at least 3 active claims")
    ids = [int(r["claim_id"]) for r in rows]

    mod = _load_factcheck()
    calls = {"n": 0}

    def fake_retrieve(*_a, **_kw):
        calls["n"] += 1
        if calls["n"] == 3:
            raise ConnectionError("simulated network error")
        return (
            [{"title": "t", "abstract": "a", "url": "http://x", "source_tier": "primary_study"}],
            "pubmed",
            {},
        )

    monkeypatch.setattr(mod, "retrieve_hybrid_evidence", fake_retrieve)
    monkeypatch.setattr(mod, "lookup_library", lambda *_a, **_kw: None)
    monkeypatch.setattr(mod, "ensure_library_table", lambda *_a, **_kw: None)
    monkeypatch.setattr(mod, "assess_evidence_sufficiency", lambda *_a, **_kw: None)
    monkeypatch.setattr(mod, "collect_specificity_nli_scores", lambda *_a, **_kw: {})
    monkeypatch.setattr(mod, "classify_evidence_expectation", lambda *_a, **_kw: None)
    monkeypatch.setattr(mod, "score_component_evidence", lambda *_a, **_kw: None)
    monkeypatch.setattr(mod, "DEBUG_LOG", tmp_path / "debug.jsonl")

    payload = tmp_path / "payload.json"
    monkeypatch.setattr(sys, "argv", [
        "03_factcheck.py",
        "--batch-submit",
        "--skip-nli",
        "--recheck-ids",
        ",".join(str(i) for i in ids),
        "--dump-payload",
        str(payload),
    ])
    mod.main()
    out = capsys.readouterr().out

    assert payload.is_file(), out
    data = json.loads(payload.read_text(encoding="utf-8"))
    custom_ids = [str(r["custom_id"]) for r in data["requests"]]
    skipped = str(ids[2])
    assert skipped not in custom_ids
    assert str(ids[0]) in custom_ids
    assert str(ids[1]) in custom_ids
    assert "1 iddia retrieval hatasıyla atlandı" in out
    assert skipped in out

    debug_text = (tmp_path / "debug.jsonl").read_text(encoding="utf-8")
    assert "retrieval_failed" in debug_text
    assert skipped in debug_text
