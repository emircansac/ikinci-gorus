"""12_ops_report — SQL alias + all_verdicted satır sayısı + percentile."""
import importlib.util
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

ROOT = Path(__file__).parent.parent


def _load_ops_report():
    spec = importlib.util.spec_from_file_location(
        "ops_report12", ROOT / "pipeline" / "12_ops_report.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_all_verdicted_sql_uses_vr_alias():
    src = (ROOT / "pipeline" / "12_ops_report.py").read_text(encoding="utf-8")
    assert "vr.claim_id IS NOT NULL" in src
    assert "clauses.append(\"v.claim_id IS NOT NULL\")" not in src


def test_all_verdicted_returns_244_rows():
    from utils.db import get_conn

    mod = _load_ops_report()
    conn = get_conn()
    try:
        rows = mod._fetch_claim_rows(
            conn, video_ids=[], claim_ids=[], since=None, until=None,
        )
    finally:
        conn.close()
    verdicted = [r for r in rows if r.get("verified_at")]
    assert len(verdicted) == 344, f"expected 344 verdicted, got {len(verdicted)}"


def test_percentile_helpers():
    mod = _load_ops_report()
    xs = [1.0, 2.0, 3.0, 4.0, 5.0]
    assert mod._percentile(xs, 0.50) == 3.0
    assert mod._percentile(xs, 1.0) == 5.0
    assert mod._percentile([], 0.50) is None
    assert mod._percentile([7.0], 0.90) == 7.0


def test_parse_cost_spread_from_baseline():
    mod = _load_ops_report()
    prev = mod._metrics_from_report_file(
        ROOT / "data" / "ops_reports" / "2026-08-18-all_verdicted-baseline.md"
    )
    assert prev["$/claim p95"] == 0.0643
    assert prev["$/claim max"] == 0.0903


def test_tail_cost_max_warning_triggers_on_slice100():
    mod = _load_ops_report()
    prev = mod._metrics_from_report_file(
        ROOT / "data" / "ops_reports" / "2026-08-18-all_verdicted-baseline.md"
    )
    metrics = {
        "cost_p95": 0.0679,
        "cost_max": 0.6371,
        "avg_cost_usd": 0.0423,
        "escalation_rate": 1.0,
    }
    warnings = mod._build_warnings(metrics, {}, prev, has_previous_report=True)
    assert any("$/claim max" in w for w in warnings)
    assert not any("$/claim p95" in w for w in warnings)


def test_claim_ids_scope_label_custom_not_measurement():
    mod = _load_ops_report()
    lines = mod._claim_ids_scope_lines("slice100", [649, 807, 624])
    assert any("--claim-ids" in ln for ln in lines)
    assert not any("measurement_50" in ln for ln in lines)
    test_lines = mod._claim_ids_scope_lines("test (measurement_50 + ...)", [1, 2])
    assert any("measurement_50" in ln for ln in test_lines)


def _minimal_metrics(**overrides):
    base = {
        "n_claims": 10,
        "n_verdicts": 10,
        "n_videos": 0,
        "avg_claims_per_video": 0.0,
        "dedup_merged": 0,
        "dedup_raw": 0,
        "dedup_ratio": None,
        "dedup_video_n": 0,
        "escalation_rate": 1.0,
        "web_search_rate": 0.5,
        "retrieval_cited_rate": 0.3,
        "cache_hit_rate": None,
        "specificity_tier": {"direct": 10},
        "parse_fail_n": 0,
        "parse_retry_ok": 0,
        "parse_retry_n": 0,
        "needs_human_rate": 0.9,
        "avg_cost_usd": None,
        "n_cost_samples": 0,
        "cost_sources": {},
        "escalated_0_n": 0,
        "would_auto_accept_v1": {"true": 0, "false": 10},
        "source_tier": {"other": 10},
        "claims_by_video": {},
    }
    base.update(overrides)
    return base


def _render_notes_body(mod, metrics):
    from datetime import date

    return mod._render_report(
        metrics=metrics,
        scope_label="unit",
        video_ids=[],
        claim_ids=[],
        since=None,
        until=None,
        report_date=date(2026, 8, 18),
        prev={},
        warnings=None,
        is_first_report=True,
    )


def test_specificity_missing_note_two_way():
    """(yok) notu yalnızca N>0 iken; N=0 satırı tamamen yok."""
    mod = _load_ops_report()
    marker = "specificity_tier=(yok)"
    body0 = _render_notes_body(
        mod,
        _minimal_metrics(specificity_tier={"direct": 10}, n_verdicts=10),
    )
    assert marker not in body0
    body_n = _render_notes_body(
        mod,
        _minimal_metrics(
            specificity_tier={"direct": 1, "(yok)": 99},
            n_verdicts=100,
        ),
    )
    assert "- **specificity_tier=(yok) 99 iddia**" in body_n
    assert "bu mekanizma eklenmeden önce fact-check edilmiş" in body_n
