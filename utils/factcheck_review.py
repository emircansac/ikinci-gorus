"""Fact-check insan onayı / otomasyon kararı — saf fonksiyonlar (test edilebilir)."""
import re

from utils.reasoning_patterns import PARTIAL_REASONING_RE

HIGH_RISK_HUMAN_REVIEW_CATEGORIES = {"tedavi", "doz", "mucize-ürün", "tanı"}

VERDICT_REASONING_MISMATCH_FLAG = "verdict_reasoning_mismatch"
PACKAGE_ONLY_FORCED_FLAG = "package_only_forced"
COMPOUND_TIER_MISMATCH_FLAG = "compound_tier_mismatch"
BINARY_VERDICTS_FOR_MISMATCH = frozenset({"doğrulanmış", "yanlış"})

_DRUG_INTERACTION_RE = re.compile(
    r"antikoag[üu]lan|warfarin|"
    r"\bdoac\b|apiksaban|rivaroksaban|dabigatran|edoksaban|"
    r"kan\s*suland[ıi]r[ıi]c[ıi]|"
    r"ila[çc].{0,40}etkile[sş]im|vitamin\s*k.{0,30}(ila[çc]|warfarin)",
    re.IGNORECASE,
)


def is_drug_interaction_claim(claim_text: str) -> bool:
    return bool(_DRUG_INTERACTION_RE.search(claim_text or ""))


def security_risk_triggers(
    *,
    category: str | None,
    initial_risk: str | None,
    claim_text: str,
    calibration_flags: str | None = None,
) -> list[str]:
    """Otomatik kabulde olmaması gereken hassas/yüksek risk sinyalleri."""
    flags = {f.strip() for f in (calibration_flags or "").split(",") if f.strip()}
    triggers: list[str] = []
    cat = (category or "").strip()
    if cat in HIGH_RISK_HUMAN_REVIEW_CATEGORIES:
        triggers.append(f"kategori={cat}")
    if (initial_risk or "").strip() == "high":
        triggers.append("initial_risk=high")
    if is_drug_interaction_claim(claim_text):
        triggers.append("drug_interaction")
    if VERDICT_REASONING_MISMATCH_FLAG in flags:
        triggers.append("verdict_reasoning_mismatch")
    if PACKAGE_ONLY_FORCED_FLAG in flags:
        triggers.append("package_only_forced")
    return triggers


def apply_verdict_reasoning_mismatch(final: dict) -> bool:
    """
    doğrulanmış/yanlış verdict ile kısmi-reasoning çelişkisini bayrakla.
    Verdict değiştirilmez — yalnızca calibration_flags güncellenir.
    Dönüş: bayrak eklendiyse True.
    """
    verdict = final.get("final_verdict")
    if verdict not in BINARY_VERDICTS_FOR_MISMATCH:
        return False
    if not PARTIAL_REASONING_RE.search(final.get("reasoning") or ""):
        return False
    flags = {f.strip() for f in (final.get("calibration_flags") or "").split(",") if f.strip()}
    if VERDICT_REASONING_MISMATCH_FLAG in flags:
        return True
    flags.add(VERDICT_REASONING_MISMATCH_FLAG)
    final["calibration_flags"] = ",".join(sorted(flags))
    return True


def apply_compound_component_cap(
    final: dict,
    component_evidence_map: dict | None,
) -> bool:
    """
    Bileşik iddiada alt-bileşen specificity tier'ları farklıysa binary verdict'i
    tartışmalı'ya indir (prompt kuralının sunucu tarafı yedeği).
    """
    comps = (component_evidence_map or {}).get("components") or []
    if len(comps) < 2:
        return False
    tiers = {(c.get("tier") or "none").strip() or "none" for c in comps}
    if len(tiers) <= 1:
        return False
    verdict = final.get("final_verdict")
    if verdict not in BINARY_VERDICTS_FOR_MISMATCH:
        return False
    final["final_verdict"] = "tartışmalı"
    flags = {f.strip() for f in (final.get("calibration_flags") or "").split(",") if f.strip()}
    flags.add(COMPOUND_TIER_MISMATCH_FLAG)
    final["calibration_flags"] = ",".join(sorted(flags))
    return True


def compute_needs_human(
    *,
    category: str | None,
    initial_risk: str | None,
    claim_text: str,
    parse_failed: bool,
    final_verdict: str | None,
    escalated_flag: int,
    calibrated: dict,
    source_directness: str | None,
    library_review_hit: dict | None = None,
    calibration_flags: str | None = None,
    extra_needs_human: bool = False,
) -> bool:
    flags = {f.strip() for f in (calibration_flags or "").split(",") if f.strip()}
    return (
        extra_needs_human
        or (category in HIGH_RISK_HUMAN_REVIEW_CATEGORIES)
        or (initial_risk == "high")
        or is_drug_interaction_claim(claim_text)
        or parse_failed
        or (final_verdict is None)
        or (escalated_flag == 1 and bool(calibrated.get("needs_human")))
        or (source_directness == "indirect")
        or (library_review_hit is not None)
        or ("library_flag_review" in flags)
        or (VERDICT_REASONING_MISMATCH_FLAG in flags)
        or (PACKAGE_ONLY_FORCED_FLAG in flags)
    )


def review_flags(*, needs_human: bool) -> tuple[int, int]:
    """Pipeline asla human_reviewed=1 yazmaz; auto_accepted otomasyon kararını taşır."""
    return 0, 0 if needs_human else 1
