"""
İnsan incelemecisi için kural/şablon tabanlı özet — ek LLM çağrısı yok.

needs_human kararını değiştirmez; yalnızca hangi TEK noktaya bakılacağını kısaltır.
"""
from __future__ import annotations

import re

from utils.factcheck_review import (
    HIGH_RISK_HUMAN_REVIEW_CATEGORIES,
    VERDICT_REASONING_MISMATCH_FLAG,
    PACKAGE_ONLY_FORCED_FLAG,
    is_drug_interaction_claim,
)
from utils.reasoning_patterns import PARTIAL_REASONING_RE

HIGH_RISK_CATEGORIES = HIGH_RISK_HUMAN_REVIEW_CATEGORIES
CITE_FLAG_TO_SOURCE = {
    "retrieval_cited": "Sağlanan kanıt paketinden",
    "web_search_override": "Claude'un kendi aramasından (pakette değildi)",
    "web_search_only": "Claude'un kendi aramasından (pakette kanıt yoktu)",
}

_COMPOUND_CLAUSE_MIN = 25
_COMPOUND_MAX_PARTS = 3
_HEM_PAIR_RE = re.compile(
    r"\bhem\s+(.+?)\s+hem(?:\s+de)?\s+(.+)",
    re.IGNORECASE | re.DOTALL,
)
# Fiil eki: aorist ır/ir/ar/er listeleri (pancar) değil, 25-char taban korur.
_VERBISH_RE = re.compile(
    r"(?:yor|mekte|makta|mış|miş|muş|müş|acak|ecek|"
    r"[ıiuü]yor|[aeiıoöuü]r)\b",
    re.IGNORECASE,
)
_TOKEN_RE = re.compile(r"[A-Za-zçğıöşüÇĞİÖŞÜ0-9''-]+")


def _parse_flags(flags_str: str | None) -> set[str]:
    return {f.strip() for f in (flags_str or "").split(",") if f.strip()}


def _flag_prefix(flags: set[str], prefix: str) -> str | None:
    for flag in flags:
        if flag.startswith(f"{prefix}:"):
            return flag.split(":", 1)[1]
    return None


def _cite_source_from_row(claim_row: dict, flags: set[str]) -> str | None:
    explicit = (claim_row.get("cite_source") or "").strip()
    if explicit:
        return explicit
    for cite_flag in ("web_search_override", "retrieval_cited", "web_search_only"):
        if cite_flag in flags:
            return cite_flag
    return None


def _specificity_tier(claim_row: dict, flags: set[str]) -> str | None:
    tier = (claim_row.get("specificity_tier") or "").strip()
    if tier:
        return tier
    return _flag_prefix(flags, "specificity_tier")


def _strip_clause(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip(" \t\n\r,;.")).strip()


def _looks_verbish(clause: str) -> bool:
    return bool(_VERBISH_RE.search(clause or ""))


def _edge_token(text: str, *, from_end: bool) -> str:
    tokens = _TOKEN_RE.findall(text or "")
    if not tokens:
        return ""
    return tokens[-1] if from_end else tokens[0]


def _token_is_clause_verb(token: str) -> bool:
    """
    Yan cümle yüklemi. «karar/pancar» gibi kısa -ar adlarını fiil sayma.
    """
    t = (token or "").lower()
    if not t:
        return False
    if re.search(
        r"(?:yor|mekte|makta|mış|miş|muş|müş|acak|ecek|"
        r"dı|di|du|dü|tı|ti|tu|tü)$",
        t,
    ):
        return True
    if len(t) >= 8 and _looks_verbish(t):
        return True
    if len(t) >= 7 and t.endswith(("ilir", "ılır", "ulur", "ülür", "amaz", "emez")):
        return True
    return False


def _ve_is_phrase_coordination(left: str, right: str) -> bool:
    """
    Cümle-ortası NP/AP «ve»: bölünme|keratin, görülür|ölçülebilir, hafıza|karar.
    Yan cümle «yükselir ve yürüyüş sonrası…» False kalır.
    """
    a = _edge_token(left, from_end=True)
    b = _edge_token(right, from_end=False)
    if not a or not b:
        return False
    a_v, b_v = _token_is_clause_verb(a), _token_is_clause_verb(b)
    if a_v and not b_v:
        return False
    if not a_v and b_v and len(b) >= 8:
        return False
    return True


def _merge_short_ve_clauses(parts: list[str]) -> list[str]:
    """Kısa «ve» parçalarını komşuya yapıştır (ör. 'Protein ve yağ …')."""
    merged: list[str] = []
    for raw in parts:
        part = _strip_clause(raw)
        if not part:
            continue
        if not merged:
            merged.append(part)
            continue
        if len(part) < _COMPOUND_CLAUSE_MIN or len(merged[-1]) < _COMPOUND_CLAUSE_MIN:
            merged[-1] = f"{merged[-1]} ve {part}"
        else:
            merged.append(part)
    if len(merged) >= 2 and len(merged[-1]) < _COMPOUND_CLAUSE_MIN:
        tail = merged.pop()
        merged[-1] = f"{merged[-1]} ve {tail}"
    return merged


def _merge_phrase_ve_clauses(parts: list[str]) -> list[str]:
    """NP/AP koordinasyonundaki «ve» kesimini geri birleştir."""
    if len(parts) < 2:
        return parts
    out = [parts[0]]
    for part in parts[1:]:
        if _ve_is_phrase_coordination(out[-1], part):
            out[-1] = f"{out[-1]} ve {part}"
        else:
            out.append(part)
    return out


def _split_on_ve(text: str) -> list[str]:
    parts = [p.strip() for p in re.split(r"\s+ve\s+", text, flags=re.IGNORECASE)]
    if len(parts) < 2:
        return []
    merged = _merge_phrase_ve_clauses(_merge_short_ve_clauses(parts))
    substantial = [p for p in merged if len(p) >= _COMPOUND_CLAUSE_MIN]
    if len(substantial) < 2:
        return []
    return substantial[:_COMPOUND_MAX_PARTS]


def _split_on_hem(text: str) -> list[str]:
    m = _HEM_PAIR_RE.search(text)
    if not m:
        return []
    left, right = _strip_clause(m.group(1)), _strip_clause(m.group(2))
    if len(left) >= _COMPOUND_CLAUSE_MIN and len(right) >= _COMPOUND_CLAUSE_MIN:
        return [left, right][:_COMPOUND_MAX_PARTS]
    return []


def _split_on_comma_verbs(text: str) -> list[str]:
    """Virgülle ayrılmış fiil öbekleri; 'ıspanak, domates ve pancar' listesini bölmez."""
    parts = [_strip_clause(p) for p in re.split(r",\s+", text)]
    parts = [p for p in parts if p]
    if len(parts) < 2:
        return []
    if any(len(p) < _COMPOUND_CLAUSE_MIN or not _looks_verbish(p) for p in parts):
        return []
    return parts[:_COMPOUND_MAX_PARTS]


def decompose_claim_for_retrieval(claim_text: str) -> list[str]:
    """
    Saf metin: bileşik iddiayı 2–3 alt-iddiaya böl. LLM yok.
    Başarısız/tek parça → [orijinal metin].
    """
    text = _strip_clause(claim_text or "")
    if not text:
        return [""]
    for splitter in (_split_on_ve, _split_on_hem, _split_on_comma_verbs):
        parts = splitter(text)
        if len(parts) >= 2:
            return parts[:_COMPOUND_MAX_PARTS]
    return [text]


def is_compound_claim(claim_text: str, reasoning: str | None = None) -> bool:
    """Bileşik iddia: decompose ≥2 parça, veya gerekçede bileşen ipucu."""
    if len(decompose_claim_for_retrieval(claim_text or "")) >= 2:
        return True
    reasoning_text = reasoning or ""
    if re.search(r"\bbileşik\b|\bbileşen\b|\bkısmı\b|\bparçası\b", reasoning_text, re.IGNORECASE):
        return True
    return False


def _extract_mismatch_snippet(reasoning: str) -> str:
    """verdict_reasoning_mismatch için gerekçeden ilgili cümleyi çek."""
    text = (reasoning or "").strip()
    if not text:
        return "gerekçede işaretlenen kısım"
    for sent in re.split(r"(?<=[.!?])\s+", text):
        sent = sent.strip()
        if sent and PARTIAL_REASONING_RE.search(sent):
            return sent[:140] + ("…" if len(sent) > 140 else "")
    m = re.search(r"(?:ancak|fakat|oysa)\s+(.{15,140}?)(?:[.;]|$)", text, re.IGNORECASE)
    if m:
        snippet = m.group(1).strip()
        return snippet[:140] + ("…" if len(snippet) > 140 else "")
    return text[:100] + ("…" if len(text) > 100 else "")


def _evidence_snippet(reasoning: str | None, nli_snippet: str | None = None) -> str:
    """En fazla 2 satır kanıt/gerekçe özeti."""
    source = (reasoning or "").strip() or (nli_snippet or "").strip()
    if not source:
        return ""
    lines: list[str] = []
    for raw_line in source.replace("\r", "").split("\n"):
        line = raw_line.strip()
        if line:
            lines.append(line)
        if len(lines) >= 2:
            break
    if not lines:
        return ""
    if len(lines) == 1 and len(lines[0]) > 220:
        text = lines[0]
        cut = text[:220].rsplit(" ", 1)[0]
        return (cut or text[:220]) + "…"
    return "\n".join(lines[:2])


def _nli_disagrees(nli_label: str | None, final_verdict: str | None) -> bool:
    if not nli_label or not final_verdict:
        return False
    if nli_label == "SUPPORTS" and final_verdict == "yanlış":
        return True
    if nli_label == "REFUTES" and final_verdict == "doğrulanmış":
        return True
    return False


def _risk_level(claim_row: dict, flags: set[str]) -> str:
    category = (claim_row.get("category") or "").strip()
    initial_risk = (claim_row.get("initial_risk") or "").strip()
    claim_text = claim_row.get("claim_text") or ""
    if (
        category in HIGH_RISK_CATEGORIES
        or initial_risk == "high"
        or is_drug_interaction_claim(claim_text)
    ):
        return "yüksek"
    evidence_stance = (claim_row.get("evidence_stance") or "").strip()
    source_directness = (claim_row.get("source_directness") or "").strip()
    if (
        initial_risk == "medium"
        or evidence_stance in ("mixed", "insufficient")
        or source_directness in ("indirect", "unrelated")
        or VERDICT_REASONING_MISMATCH_FLAG in flags
        or "default_conf" in flags
    ):
        return "orta"
    return "düşük"


def _one_line_reason(
    *,
    final_verdict: str | None,
    flags: set[str],
    evidence_stance: str | None,
    source_directness: str | None,
    model_disagreement: bool,
) -> str:
    verdict = final_verdict or "belirsiz"
    if VERDICT_REASONING_MISMATCH_FLAG in flags:
        return f"Model '{verdict}' dedi; gerekçe tam destek/çürütme göstermiyor."
    if evidence_stance == "mixed":
        return f"Kanıt karışık — '{verdict}' hükmünde hangi bileşen sorunlu netleştirin."
    if source_directness == "indirect":
        return f"Kaynak iddiayı dolaylı ele alıyor; '{verdict}' için doğrudan kanıt şart."
    if "no_direct_evidence_expected" in flags:
        return "Doğrudan çalışma beklenmiyor; mekanistik/dolaylı kanıt yeterli mi kontrol edin."
    if "web_search_override" in flags:
        return f"Paket dışı kaynakla '{verdict}' — URL ve iddia eşleşmesini doğrulayın."
    if model_disagreement:
        return f"NLI ile model hükmü ('{verdict}') aynı yönde değil."
    if verdict == "tartışmalı":
        return "Model emin değil — hangi kısım destekleniyor/desteklenmiyor ayırın."
    return f"Model '{verdict}' dedi — kaynak ve gerekçe tutarlı mı bakın."


def _build_check_point(
    claim_row: dict,
    flags: set[str],
    *,
    drug_suffix: str,
) -> str:
    reasoning = claim_row.get("reasoning") or ""
    claim_text = claim_row.get("claim_text") or ""
    evidence_stance = (claim_row.get("evidence_stance") or "").strip()
    source_directness = (claim_row.get("source_directness") or "").strip()
    specificity_tier = _specificity_tier(claim_row, flags)
    final_verdict = claim_row.get("final_verdict")

    if VERDICT_REASONING_MISMATCH_FLAG in flags:
        snippet = _extract_mismatch_snippet(reasoning)
        point = (
            f"Verdict kendi gerekçesiyle tam örtüşmüyor — "
            f"{snippet} kısmının doğrudan kanıtı var mı, bakın."
        )
    elif evidence_stance == "mixed" or is_compound_claim(claim_text, reasoning):
        point = (
            "Bileşik iddia — bileşenlerden biri destekleniyor, diğeri "
            "desteklenmiyor olabilir. Hangi bileşenin sorunlu olduğuna bakın."
        )
    elif specificity_tier == "background" or "no_direct_evidence_expected" in flags:
        point = (
            "Bu iddia için literatürde doğrudan bir çalışma bulunamadı; "
            "kanıt dolaylı/mekanistik."
        )
    elif source_directness == "unrelated":
        point = "Atıf yapılan kaynak iddiayla ilgisiz görünüyor — doğru kaynak var mı?"
    elif source_directness == "indirect":
        point = (
            f"Kaynak iddiayı dolaylı ele alıyor — '{final_verdict or 'hüküm'}' "
            "için doğrudan kanıt var mı kontrol edin."
        )
    elif "package_only_forced" in flags:
        point = "Yalnızca sağlanan kanıt paketine dayanıyor — cited URL gerçekten pakette mi?"
    elif "web_search_override" in flags:
        point = (
            "Claude paket dışı bir kaynak buldu — URL'nin iddiayı "
            "doğrudan destekleyip desteklemediğine bakın."
        )
    elif evidence_stance == "insufficient":
        point = "Kanıt yetersiz işaretlendi — mevcut kaynak iddiayı gerçekten ele alıyor mu?"
    elif final_verdict == "tartışmalı" and "default_conf" in flags:
        point = "Model düşük güvenle tartışmalı dedi — hangi kısım belirsiz, netleştirin."
    elif final_verdict == "belirsiz":
        point = "Model belirsiz dedi — iddia için yeterli kanıt var mı, yeniden değerlendirin."
    else:
        point = (
            f"Model '{final_verdict or '—'}' dedi — "
            "kaynak URL'si ve gerekçe iddiayla örtüşüyor mu kontrol edin."
        )

    if drug_suffix:
        point += drug_suffix
    return point


def _is_specific_check_point(check_point: str) -> bool:
    """Genel fallback dışında, incelemeciye yönlendirici nokta sayılır."""
    generic_markers = (
        "kaynak URL'si ve gerekçe iddiayla örtüşüyor mu kontrol edin",
    )
    return not any(marker in check_point for marker in generic_markers)


def is_generic_check_point(check_point: str) -> bool:
    """reviewer_summary genel fallback ürettiyse True — v1 shadow bandı için 'temiz' sinyali."""
    return not _is_specific_check_point(check_point)


BINARY_VERDICTS_V1 = frozenset({"doğrulanmış", "yanlış"})
NLI_AUTO_ACCEPT_LABELS = frozenset({"SUPPORTS", "REFUTES"})
NLI_AUTO_ACCEPT_MIN_CONF = 0.75  # utils.nli.should_escalate ile aynı eşik


def _calibration_flags_harmless_v1(flags: set[str]) -> tuple[bool, str | None]:
    """NLI-only yol: hedge/partial bayrak yok; boş set kabul."""
    if VERDICT_REASONING_MISMATCH_FLAG in flags:
        return False, "calibration_flags:verdict_reasoning_mismatch"
    if not flags:
        return True, None
    disallowed = sorted(flags)
    return False, f"calibration_flags:not_harmless:{disallowed[0]}"


def _is_escalated(claim_row: dict) -> bool:
    val = claim_row.get("escalated")
    if val in (0, False, "0"):
        return False
    if val in (1, True, "1"):
        return True
    return bool(val)


def would_auto_accept_v1(claim_row: dict) -> tuple[bool, str]:
    """
    Shadow-mode aday bandı v1 — üretimde auto_accepted/needs_human DEĞİŞTİRMEZ.

    Aday havuzu: escalated=0 (Claude'a gitmemiş, ucuz NLI yüksek güvenle karar vermiş).
    package_only_forced / specificity_tier==direct iddialar kapsam dışı (ayrı güvenlik kuralı).

    Dönüş: (would_accept, reason_if_not). reason_if_not boş string ise kabul.
    """
    if claim_row.get("parse_failed"):
        return False, "parse_failed"

    flags = _parse_flags(claim_row.get("calibration_flags"))

    if PACKAGE_ONLY_FORCED_FLAG in flags:
        return False, "out_of_scope:package_only_forced"
    tier = _specificity_tier(claim_row, flags)
    if tier == "direct":
        return False, "out_of_scope:specificity_tier_direct"

    if _is_escalated(claim_row):
        return False, "escalated:not_nli_only"

    nli_label = (claim_row.get("nli_label") or "").strip()
    if nli_label not in NLI_AUTO_ACCEPT_LABELS:
        return False, f"nli_label:{nli_label or 'none'}"

    nli_conf = claim_row.get("nli_confidence")
    try:
        conf = float(nli_conf) if nli_conf is not None else None
    except (TypeError, ValueError):
        conf = None
    if conf is None or conf < NLI_AUTO_ACCEPT_MIN_CONF:
        return False, f"nli_confidence:{nli_conf if nli_conf is not None else 'none'}"

    ok_flags, flag_reason = _calibration_flags_harmless_v1(flags)
    if not ok_flags:
        return False, flag_reason or "calibration_flags:blocked"

    verdict = claim_row.get("final_verdict")
    if verdict not in BINARY_VERDICTS_V1:
        return False, f"final_verdict:{verdict or 'none'}"

    if (claim_row.get("initial_risk") or "").strip() == "high":
        return False, "initial_risk:high"

    category = (claim_row.get("category") or "").strip()
    if category in HIGH_RISK_CATEGORIES:
        return False, f"category:{category}"

    if is_drug_interaction_claim(claim_row.get("claim_text") or ""):
        return False, "drug_interaction_claim"

    summary = build_reviewer_summary(claim_row)
    if summary.get("model_disagreement"):
        return False, "model_disagreement"

    if not is_generic_check_point(summary["check_point"]):
        return False, "check_point:not_generic_fallback"

    return True, ""


def build_reviewer_summary(claim_row: dict) -> dict:
    """
    İncelemeci özeti — tamamen kural/şablon; ek LLM yok.

    claim_row beklenen alanlar (eksik olanlar atlanır):
      final_verdict, reasoning, calibration_flags, category, initial_risk,
      claim_text, evidence_stance, source_directness, cite_source,
      specificity_tier, nli_label, nli_evidence_snippet
    """
    flags = _parse_flags(claim_row.get("calibration_flags"))
    final_verdict = claim_row.get("final_verdict")
    cite_key = _cite_source_from_row(claim_row, flags)
    model_disagreement = (
        VERDICT_REASONING_MISMATCH_FLAG in flags
        or _nli_disagrees(claim_row.get("nli_label"), final_verdict)
    )
    drug_suffix = ""
    if is_drug_interaction_claim(claim_row.get("claim_text") or ""):
        drug_suffix = " (ilaç etkileşimi iddiası)"

    check_point = _build_check_point(claim_row, flags, drug_suffix=drug_suffix)

    return {
        "suggested_verdict": final_verdict,
        "one_line_reason": _one_line_reason(
            final_verdict=final_verdict,
            flags=flags,
            evidence_stance=claim_row.get("evidence_stance"),
            source_directness=claim_row.get("source_directness"),
            model_disagreement=model_disagreement,
        ),
        "check_point": check_point,
        "evidence_snippet": _evidence_snippet(
            claim_row.get("reasoning"),
            claim_row.get("nli_evidence_snippet"),
        ),
        "model_disagreement": model_disagreement,
        "risk_level": _risk_level(claim_row, flags),
        "source_note": CITE_FLAG_TO_SOURCE.get(cite_key or "", "Kaynak türü belirtilmedi"),
    }
