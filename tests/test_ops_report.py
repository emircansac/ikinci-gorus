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
    assert len(verdicted) == 244, f"expected 244 verdicted, got {len(verdicted)}"


def test_percentile_helpers():
    mod = _load_ops_report()
    xs = [1.0, 2.0, 3.0, 4.0, 5.0]
    assert mod._percentile(xs, 0.50) == 3.0
    assert mod._percentile(xs, 1.0) == 5.0
    assert mod._percentile([], 0.50) is None
    assert mod._percentile([7.0], 0.90) == 7.0
