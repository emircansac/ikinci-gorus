import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parent.parent))

from utils.claude_client import (
    escalate_factcheck,
    build_batch_request,
    WEB_SEARCH_TOOL,
    SUPPORTIVE_PACKAGE_NOTE,
    NO_DIRECT_EVIDENCE_NOTE,
)

_FAKE_JSON = {
    "final_verdict": "tartışmalı",
    "confidence": 0.4,
    "reasoning": "Paket yeterli.",
    "source_url": "https://pubmed.ncbi.nlm.nih.gov/28507563/",
    "source_directness": "direct",
    "evidence_stance": "mixed",
    "source_tier": "systematic_review",
}


def _fake_resp():
    return SimpleNamespace(content=[SimpleNamespace(text=json.dumps(_FAKE_JSON))])


def test_escalate_omits_web_search_when_force_package_only(monkeypatch):
    captured = {}

    def fake_call(**kwargs):
        captured.update(kwargs)
        return _fake_resp()

    monkeypatch.setattr("utils.claude_client._call_with_retry", fake_call)
    out = escalate_factcheck(
        "Ölçülü kahve Alzheimer riskini azaltır.",
        evidence=[{
            "title": "Coffee and neurodegeneration",
            "abstract": "Coffee consumption and Parkinson/Alzheimer risk.",
            "url": "https://pubmed.ncbi.nlm.nih.gov/28507563/",
        }],
        force_package_only=True,
    )
    assert "tools" not in captured
    assert captured["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert captured["system"][0]["type"] == "text"
    assert out["source_url"] == "https://pubmed.ncbi.nlm.nih.gov/28507563/"
    assert "cite_source" not in out


def test_escalate_attaches_web_search_by_default(monkeypatch):
    captured = {}

    def fake_call(**kwargs):
        captured.update(kwargs)
        return _fake_resp()

    monkeypatch.setattr("utils.claude_client._call_with_retry", fake_call)
    escalate_factcheck("test claim", evidence=[])
    assert captured["tools"] == [WEB_SEARCH_TOOL]
    assert captured["system"][0]["cache_control"] == {"type": "ephemeral"}


def test_batch_request_schema_custom_id_and_params():
    req = build_batch_request(
        1284,
        "Ölçülü kahve Alzheimer riskini azaltır.",
        evidence=[{
            "title": "Coffee review",
            "abstract": "Coffee and Parkinson.",
            "url": "https://pubmed.ncbi.nlm.nih.gov/28507563/",
        }],
        force_package_only=False,
    )
    assert set(req.keys()) == {"custom_id", "params"}
    assert req["custom_id"] == "1284"
    params = req["params"]
    assert params["model"]
    assert params["max_tokens"] == 2000
    assert params["messages"][0]["role"] == "user"
    assert isinstance(params["messages"][0]["content"], str)
    assert params["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert params["tools"] == [WEB_SEARCH_TOOL]
    assert "thinking" in params


def test_batch_request_omits_web_search_when_force_package_only():
    req = build_batch_request(42, "test", evidence=[], force_package_only=True)
    assert "tools" not in req["params"]
    assert req["custom_id"] == "42"


def test_escalate_supportive_note_keeps_web_search(monkeypatch):
    captured = {}

    def fake_call(**kwargs):
        captured.update(kwargs)
        return _fake_resp()

    monkeypatch.setattr("utils.claude_client._call_with_retry", fake_call)
    escalate_factcheck(
        "Kivi C vitamini oksidatif stresi önler.",
        evidence=[{
            "title": "Vitamin C and oxidative stress",
            "abstract": "Vitamin C and endothelial function.",
            "url": "https://pubmed.ncbi.nlm.nih.gov/24130351/",
        }],
        force_package_only=False,
        specificity_tier="supportive",
    )
    assert captured["tools"] == [WEB_SEARCH_TOOL]
    content = captured["messages"][0]["content"]
    assert SUPPORTIVE_PACKAGE_NOTE in content
    assert NO_DIRECT_EVIDENCE_NOTE not in content


def test_escalate_epistemic_note_keeps_web_search(monkeypatch):
    captured = {}

    def fake_call(**kwargs):
        captured.update(kwargs)
        return _fake_resp()

    monkeypatch.setattr("utils.claude_client._call_with_retry", fake_call)
    escalate_factcheck(
        "Aç karnına kahve kas kaybını hızlandırır.",
        evidence=[{
            "title": "Coffee and body composition",
            "abstract": "Coffee quantity and inflammation.",
            "url": "https://pubmed.ncbi.nlm.nih.gov/41041133/",
        }],
        force_package_only=False,
        epistemic_class="no_direct_evidence_expected",
    )
    assert captured["tools"] == [WEB_SEARCH_TOOL]
    content = captured["messages"][0]["content"]
    assert NO_DIRECT_EVIDENCE_NOTE in content
    assert SUPPORTIVE_PACKAGE_NOTE not in content


def test_escalate_direct_force_package_only_still_omits_tools(monkeypatch):
    captured = {}

    def fake_call(**kwargs):
        captured.update(kwargs)
        return _fake_resp()

    monkeypatch.setattr("utils.claude_client._call_with_retry", fake_call)
    escalate_factcheck(
        "Ölçülü kahve Alzheimer riskini azaltır.",
        evidence=[{
            "title": "Coffee and neurodegeneration",
            "abstract": "Coffee consumption and Parkinson/Alzheimer risk.",
            "url": "https://pubmed.ncbi.nlm.nih.gov/28507563/",
        }],
        force_package_only=True,
        specificity_tier="direct",
    )
    assert "tools" not in captured
    content = captured["messages"][0]["content"]
    assert SUPPORTIVE_PACKAGE_NOTE not in content
    assert NO_DIRECT_EVIDENCE_NOTE not in content


def test_extract_claims_uses_cached_system(monkeypatch):
    captured = {}

    def fake_call(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(content=[SimpleNamespace(text='{"claims": []}')])

    monkeypatch.setattr("utils.claude_client._call_with_retry", fake_call)
    from utils.claude_client import _extract_claims_once
    _extract_claims_once("[0s] Kahve sağlıklıdır.")
    assert captured["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert captured["system"][0]["type"] == "text"


def test_summarize_cache_roles_write_read_both():
    from utils.claude_client import summarize_cache_roles
    out = summarize_cache_roles({
        "360": {"cache_creation_input_tokens": 4119, "cache_read_input_tokens": 0},
        "363": {"cache_creation_input_tokens": 0, "cache_read_input_tokens": 4119},
        "364": {"cache_creation_input_tokens": 20000, "cache_read_input_tokens": 18000},
        "368": {"cache_creation_input_tokens": 0, "cache_read_input_tokens": 0},
    })
    assert out["roles"]["360"] == "write"
    assert out["roles"]["363"] == "read"
    assert out["roles"]["364"] == "both"
    assert out["roles"]["368"] == "none"
    assert out["n_write_gt0"] == 2
    assert out["n_read_gt0"] == 2
    assert out["n_both"] == 1
    assert out["n_write_only"] == 1
    assert out["n_read_only"] == 1
    assert out["n_none"] == 1
