import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parent.parent))

from utils.claude_client import (
    escalate_factcheck,
    build_batch_request,
    web_search_tool,
    WEB_SEARCH_TOOL,
    resolve_max_search_calls,
    SUPPORTIVE_PACKAGE_NOTE,
    NO_DIRECT_EVIDENCE_NOTE,
    _format_evidence_package,
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


def test_classify_parse_failure_invalid_json_unescaped_quotes():
    from utils.claude_client import classify_parse_failure
    bad = '{"final_verdict": "doğrulanmış", "reasoning": "foo "bar" baz"}'
    cat, err = classify_parse_failure(bad, "end_turn", None)
    assert cat == "invalid_json"
    assert err


def test_classify_parse_failure_prose_no_json():
    from utils.claude_client import classify_parse_failure
    cat, err = classify_parse_failure("Bu iddia spesifik bir vaka hakkında.", "end_turn", None)
    assert cat == "invalid_json"
    assert "no JSON" in err


def test_classify_parse_failure_truncated_stop_reason():
    from utils.claude_client import classify_parse_failure
    cat, err = classify_parse_failure('{"final_verdict": "belirsiz",', "max_tokens", None)
    assert cat == "truncated"


def test_classify_parse_failure_wrong_enum():
    from utils.claude_client import classify_parse_failure
    parsed = {
        "final_verdict": "maybe",
        "confidence": 0.5,
        "reasoning": "x",
        "source_url": "https://example.com",
        "source_directness": "direct",
        "evidence_stance": "supports",
        "source_tier": "primary_study",
    }
    cat, err = classify_parse_failure("{}", "end_turn", parsed)
    assert cat == "wrong_enum"


def test_escalate_parse_retry_on_invalid_json(monkeypatch):
    calls = []

    def fake_call(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            return SimpleNamespace(
                content=[SimpleNamespace(text='not json at all')],
                stop_reason="end_turn",
                usage=None,
            )
        return _fake_resp()

    monkeypatch.setattr("utils.claude_client._call_with_retry", fake_call)
    out = escalate_factcheck("test claim", evidence=[], force_package_only=True)
    assert len(calls) == 2
    assert "temperature" not in calls[1]
    assert "[JSON RETRY]" in calls[1]["messages"][0]["content"]
    assert out["final_verdict"] == "tartışmalı"
    assert out.get("parse_retry") is True
    assert out.get("parse_retry_succeeded") is True


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
    assert params["max_tokens"] == 2000  # ESCALATE_MAX_TOKENS
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


def test_format_package_includes_component_map():
    cmap = {
        "components": [
            {"text": "A bileşeni glisemi yavaş yükselir", "tier": "direct", "kept": 4},
            {"text": "B bileşeni insülin duyarlılığı artar", "tier": "background", "kept": 5},
        ]
    }
    out = _format_evidence_package(
        "bileşik iddia",
        [{
            "title": "Protein study",
            "url": "https://pubmed.ncbi.nlm.nih.gov/1/",
            "abstract": "Glycemic response.",
        }],
        cmap,
    )
    assert "Bileşen kanıt haritası" in out
    assert "tier=direct" in out
    assert "tier=background" in out
    assert "[A]" in out and "[B]" in out


def test_escalate_passes_component_map_into_user_message(monkeypatch):
    captured = {}

    def fake_call(**kwargs):
        captured.update(kwargs)
        return _fake_resp()

    monkeypatch.setattr("utils.claude_client._call_with_retry", fake_call)
    escalate_factcheck(
        "test claim",
        evidence=[{
            "title": "T",
            "abstract": "x",
            "url": "https://pubmed.ncbi.nlm.nih.gov/1/",
        }],
        force_package_only=True,
        component_evidence_map={
            "components": [
                {"text": "alt A uzun metin burada yeter", "tier": "supportive", "kept": 3},
                {"text": "alt B uzun metin burada yeter", "tier": "none", "kept": 0},
            ]
        },
    )
    content = captured["messages"][0]["content"]
    assert "Bileşen kanıt haritası" in content
    assert "tier=supportive" in content
    assert "tier=none" in content


def test_resolve_max_search_calls_defaults():
    assert resolve_max_search_calls(initial_risk="medium", nli_label="SUPPORTS") == 1
    assert resolve_max_search_calls(initial_risk="high") == 3
    assert resolve_max_search_calls(initial_risk="low", nli_label="REFUTES") == 2


def test_escalate_passes_max_uses_on_web_search(monkeypatch):
    captured = {}

    def fake_call(**kwargs):
        captured.update(kwargs)
        return _fake_resp()

    monkeypatch.setattr("utils.claude_client._call_with_retry", fake_call)
    escalate_factcheck("test claim", evidence=[], max_search_calls=2)
    assert captured["tools"] == [web_search_tool(2)]


def test_batch_request_includes_max_uses():
    req = build_batch_request(99, "test", evidence=[], max_search_calls=1)
    assert req["params"]["tools"] == [web_search_tool(1)]


def test_count_web_search_calls_success_only_not_server_tool_use():
    from utils.claude_client import count_web_search_calls

    msg = SimpleNamespace(content=[
        SimpleNamespace(type="text", text="intro"),
        SimpleNamespace(type="server_tool_use", name="web_search"),
        SimpleNamespace(type="server_tool_use", name="web_search"),
        SimpleNamespace(type="web_search_tool_result", content="WebSearchResultBlock(...)"),
        SimpleNamespace(
            type="web_search_tool_result",
            content="WebSearchToolResultError(error_code='max_uses_exceeded', type='web_search_tool_result_error')",
        ),
        SimpleNamespace(type="server_tool_use", name="web_search"),
        SimpleNamespace(
            type="web_search_tool_result",
            content="WebSearchToolResultError(error_code='max_uses_exceeded', type='web_search_tool_result_error')",
        ),
    ])
    assert count_web_search_calls(msg) == 1


def test_count_web_search_calls_legacy_server_tool_use_fallback():
    from utils.claude_client import count_web_search_calls

    msg = SimpleNamespace(content=[
        SimpleNamespace(type="server_tool_use", name="web_search"),
        SimpleNamespace(type="server_tool_use", name="web_search"),
    ])
    assert count_web_search_calls(msg) == 2


def test_official_web_search_requests_from_usage_dict():
    from utils.claude_client import official_web_search_requests

    assert official_web_search_requests(None) is None
    assert official_web_search_requests({"input_tokens": 10}) is None
    assert official_web_search_requests({
        "server_tool_use": {"web_search_requests": 1, "web_fetch_requests": 0},
    }) == 1
    assert official_web_search_requests({
        "server_tool_use": {"web_search_requests": 0},
    }) == 0


def test_official_web_search_requests_from_usage_object():
    from utils.claude_client import official_web_search_requests

    usage = SimpleNamespace(
        server_tool_use=SimpleNamespace(web_search_requests=3, web_fetch_requests=0),
    )
    assert official_web_search_requests(usage) == 3


def test_count_web_search_calls_unchanged_when_official_differs():
    """Üretim sayacı usage alanını okumaz — 3 deneme / 1 başarı senaryosu aynı kalır."""
    from utils.claude_client import count_web_search_calls, official_web_search_requests

    msg = SimpleNamespace(
        content=[
            SimpleNamespace(type="server_tool_use", name="web_search"),
            SimpleNamespace(type="web_search_tool_result", content="ok"),
            SimpleNamespace(type="server_tool_use", name="web_search"),
            SimpleNamespace(
                type="web_search_tool_result",
                content="WebSearchToolResultError(error_code='max_uses_exceeded')",
            ),
        ],
        usage=SimpleNamespace(
            server_tool_use=SimpleNamespace(web_search_requests=1),
        ),
    )
    assert count_web_search_calls(msg) == 1
    assert official_web_search_requests(msg.usage) == 1
