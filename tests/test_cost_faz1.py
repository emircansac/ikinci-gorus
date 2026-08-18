"""18_cost_faz1_test — cache token'lı sync maliyet formülü."""
import importlib.util
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
ROOT = Path(__file__).parent.parent


def _load():
    spec = importlib.util.spec_from_file_location(
        "cost_faz1", ROOT / "pipeline" / "18_cost_faz1_test.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_estimate_sync_cost_includes_cache_tokens():
    mod = _load()
    usage = {
        "input_tokens": 1019,
        "output_tokens": 705,
        "cache_creation_input_tokens": 10549,
        "cache_read_input_tokens": 8352,
    }
    legacy = mod._estimate_sync_cost_legacy(usage)
    full = mod._estimate_sync_cost(usage)
    assert legacy is not None and full is not None
    assert full > legacy
    expected = (
        1019 * 2.0 / 1_000_000
        + 705 * 10.0 / 1_000_000
        + 10549 * 2.5 / 1_000_000
        + 8352 * 0.20 / 1_000_000
    )
    assert abs(full - expected) < 1e-12


def test_estimate_sync_cost_cache_only_not_none():
    mod = _load()
    usage = {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_creation_input_tokens": 1000,
        "cache_read_input_tokens": 0,
    }
    assert mod._estimate_sync_cost_legacy(usage) is None
    assert abs(mod._estimate_sync_cost(usage) - 1000 * 2.5 / 1_000_000) < 1e-12
