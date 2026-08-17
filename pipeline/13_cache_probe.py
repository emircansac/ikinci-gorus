"""
Prompt caching canlı kanıt: aynı cached system ile art arda 2 çağrı.

Kullanım:
    python pipeline/13_cache_probe.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent))

from utils.claude_client import (
    build_escalate_params,
    _call_with_retry,
    _usage_dict,
    MODEL,
)

OUT = Path(__file__).parent.parent / "data" / "cache_probe.json"


def main() -> None:
    params = build_escalate_params(
        "Ölçülü kahve Alzheimer riskini azaltır.",
        evidence=[{
            "title": "Coffee and neurodegeneration",
            "abstract": "Observational link between coffee and Parkinson/Alzheimer risk.",
            "url": "https://pubmed.ncbi.nlm.nih.gov/28507563/",
        }],
        force_package_only=False,
    )
    # Arama faturası olmasın; tool şeması cache prefix'ine girsin (1024 token eşiği).
    params["tool_choice"] = {"type": "none"}
    params["max_tokens"] = 128
    print(f"[cache-probe] model={MODEL}")
    usages = []
    for i in range(1, 3):
        print(f"[cache-probe] çağrı {i}/2")
        resp = _call_with_retry(**params)
        usage = _usage_dict(getattr(resp, "usage", None))
        usages.append(usage)
        print(
            f"  cache_creation_input_tokens={usage.get('cache_creation_input_tokens')} "
            f"cache_read_input_tokens={usage.get('cache_read_input_tokens')} "
            f"input={usage.get('input_tokens')} output={usage.get('output_tokens')}"
        )
    payload = {"model": MODEL, "usages": usages}
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[cache-probe] yazıldı: {OUT}")
    u1, u2 = usages[0], usages[1]
    if (u1.get("cache_creation_input_tokens") or 0) <= 0:
        print("[cache-probe] UYARI: ilk çağrıda cache yazılmadı (eşik/prefix?)")
    if (u2.get("cache_read_input_tokens") or 0) <= 0:
        print("[cache-probe] UYARI: ikinci çağrıda cache okunmadı")
    else:
        print("[cache-probe] cache hit doğrulandı")


if __name__ == "__main__":
    main()
