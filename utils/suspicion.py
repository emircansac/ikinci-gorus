"""
İKİLİ (doğru/yanlış) ETİKET YERİNE SÜREKLİ ŞÜPHE SKORU.

Neden: "doğrulanmış" / "yanlış" gibi keskin etiketler, "tartışmalı" (kısmen
destekleniyor, kısmen değil) ile "belirsiz" (hiç kanıt yok) arasındaki farkı
yok ediyor ve confidence bilgisini kullanmıyordu. Bunun yerine 0-100 arası
sürekli bir ŞÜPHE SKORU hesaplıyoruz:

    0   = tamamen güvenilir/doğrulanmış (üzerinde çalışmaya gerek yok)
    50  = nötr/belirsiz (kanıt yok, ne doğru ne yanlış yönünde sinyal var)
    100 = yanlışa maksimum yakın (üzerinde çalışılması gereken öncelik)

Mantık: her verdict bir YÖN taşır (yanlışa mı doğruya mı yaklaşıyor), confidence
ise bu yönün NE KADAR GÜÇLÜ olduğunu belirler. Düşük güvenli bir "yanlış" verdict,
yüksek güvenli bir "yanlış" kadar şüpheli sayılmamalı — çünkü sistem kendinden
emin değil. Bu yüzden skor merkezden (50) confidence kadar uzaklaşır, uçlara
(0 ya da 100) değil.

    suspicion_score = 50 + confidence * verdict_direction * 50

Örnek:
    final_verdict=yanlış,      confidence=0.9  -> 50 + 0.9*1*50   = 95  (çok şüpheli)
    final_verdict=yanlış,      confidence=0.3  -> 50 + 0.3*1*50   = 65  (hafif şüpheli, kanıt zayıf)
    final_verdict=tartışmalı,  confidence=0.8  -> 50 + 0.8*0.4*50 = 66
    final_verdict=doğrulanmış, confidence=0.9  -> 50 + 0.9*-1*50  = 5   (şüphesiz)
    final_verdict=belirsiz,    confidence=*    -> her zaman 50 (yön yok, gerçekten bilmiyoruz)
"""

VERDICT_DIRECTION = {
    "yanlış": 1.0,
    "tartışmalı": 0.4,
    "belirsiz": 0.0,      # yön taşımaz — "bilmiyoruz" demek, "yarı yanlış" demek değil
    "doğrulanmış": -1.0,
}

# Yüksek riskli kategorilerde (tedavi/doz/tanı/mucize-ürün), aynı şüphe skoru
# daha fazla "acele et" anlamına gelmeli — zarar potansiyeli daha büyük. Bu,
# şüphe skorunu DEĞİŞTİRMEZ (o saf bir doğruluk tahminidir); sadece ayrı bir
# öncelik skoru hesaplarken çarpan olarak kullanılır (bkz. compute_priority).
CATEGORY_STAKES_WEIGHT = {
    "tedavi": 1.3,
    "doz": 1.3,
    "tanı": 1.2,
    "mucize-ürün": 1.3,
    "mekanizma": 1.0,
    "önleme": 0.9,
    "diğer": 0.8,
}


def compute_suspicion(final_verdict: str | None, confidence: float | None,
                       parse_failed: bool = False) -> tuple[float | None, str]:
    """
    Dönüş: (suspicion_score veya None, durum_notu)

    final_verdict None ise (parse hatası, ya da hiç fact-check yapılmadıysa) skor
    HESAPLANAMAZ — bunu sessizce 50 (nötr) yapmak yanlış olur, çünkü "gerçekten
    belirsiz" ile "veri eksik/işlenemedi" farklı şeylerdir. None döndürüp
    "veri_eksik" notunu ayrıca taşıyoruz; 06_claim_index.py bunları ayrı bir
    kovaya (needs_more_data) koyar, şüphe sıralamasına HİÇ karıştırmaz.
    """
    if parse_failed or final_verdict is None:
        return None, "veri_eksik"
    if confidence is None:
        confidence = 0.5  # emniyet: confidence hiç gelmediyse orta güven varsay

    direction = VERDICT_DIRECTION.get(final_verdict)
    if direction is None:
        return None, "bilinmeyen_verdict"

    score = 50 + confidence * direction * 50
    score = max(0.0, min(100.0, score))

    if final_verdict == "belirsiz":
        note = "belirsiz"
    elif score >= 75:
        note = "yüksek_şüpheli"
    elif score >= 55:
        note = "şüpheli"
    elif score <= 25:
        note = "şüphesiz"
    else:
        note = "hafif_şüpheli"
    return round(score, 1), note


def compute_priority(suspicion_score: float, category: str, channels_affected: int = 1) -> float:
    """
    Şüphe skorunu 'bunun üzerine önce mi çalışmalıyım' önceliğine çevirir.
    Şüphe skorundan farklı olarak burada kategori (zarar potansiyeli) ve kaç
    farklı kanalda aynı/benzer iddianın tekrarlandığı (yaygınlık) devreye girer.

    Tasarım: skorun 50'nin altındaki kısmı (doğruya yakın taraf) önceliğe hiç
    katkı yapmaz — sadece 50'yi AŞAN kısım (yanlışa eğilim) kategori/yaygınlık
    çarpanıyla büyütülür. 'excess'i 2 ile çarpıp 0-100 aralığına yayıyoruz ki
    stakes/spread çarpanlarının etkisi orta skorlarda görülebilsin (yoksa yüksek
    skorlarda çarpan hemen 100 tavanına vurup birbirinden ayrılamaz hale gelir).
    """
    stakes = CATEGORY_STAKES_WEIGHT.get(category, 1.0)

    spread_multiplier = 1.0
    if suspicion_score > 50 and channels_affected > 1:
        # 2 kanal -> x1.1, 5 kanal -> x1.4, sonrasında yavaşça doyar (üst sınır x1.5)
        spread_multiplier = 1.0 + min(0.5, 0.1 * (channels_affected - 1))

    excess = max(0.0, suspicion_score - 50)  # 0-50 arası: ne kadar yanlışa eğilimli
    priority = excess * 2 * stakes * spread_multiplier
    return round(min(100.0, priority), 1)


def compute_channel_risk(
    claim_rows: list[dict],
    *,
    funnel_flag: bool = False,
    ai_persona_flag: bool = False,
    growth_anomaly_flag: bool = False,
    bot_comment_ratio: float = 0.0,
) -> tuple[float | None, str, dict]:
    """
    Kanal bazlı risk skoru (0-100) ve kademe.

    Eski formül yanlış/tartışmalı oranını TÜM iddialara böldüğü için, fact-check
    bekleyen (henüz hüküm yok) iddialar skoru yapay olarak düşürüyordu.
    Yeni formül:
      - Kontrol edilmiş iddiaların ortalama şüphe skoru (en güçlü sinyal)
      - Kontrol edilmişler arasında yüksek şüphe (≥75) oranı
      - Henüz kontrol edilmemiş ama initial_risk=high iddialar (düşük ağırlık)
      - Genel yüksek risk yoğunluğu + davranış bayrakları (hunisi, AI-persona…)

    Düşük fact-check kapsamında (<20%) kademe en fazla 'incele' olur — skor
    erken yükselse bile kesin 'acil' etiketi verilmez.
    """
    total = len(claim_rows)
    if total == 0:
        return None, "yetersiz_veri", {"total_claims": 0, "fact_check_coverage": 0.0}

    scored_suspicions: list[float] = []
    unscored_high = 0
    high_risk = 0
    false_or_disputed = 0

    for row in claim_rows:
        if row.get("initial_risk") == "high":
            high_risk += 1
        verdict = row.get("final_verdict")
        if verdict in ("yanlış", "tartışmalı"):
            false_or_disputed += 1
        if verdict is not None:
            score, _ = compute_suspicion(verdict, row.get("confidence"))
            if score is not None:
                scored_suspicions.append(score)
        elif row.get("initial_risk") == "high":
            unscored_high += 1

    coverage = len(scored_suspicions) / total

    suspicion_component = 0.0
    peak_component = 0.0
    avg_suspicion = None
    if scored_suspicions:
        avg_suspicion = sum(scored_suspicions) / len(scored_suspicions)
        suspicion_component = (avg_suspicion / 100) * 40
        high_susp_share = sum(1 for s in scored_suspicions if s >= 75) / len(scored_suspicions)
        peak_component = high_susp_share * 10

    unscored_component = min(unscored_high / total, 1.0) * 15
    high_risk_component = min(high_risk / total, 1.0) * 10
    flag_component = (
        (15 if funnel_flag else 0)
        + (5 if ai_persona_flag else 0)
        + (10 if growth_anomaly_flag else 0)
        + min(max(bot_comment_ratio, 0.0), 1.0) * 15
    )

    score = suspicion_component + peak_component + unscored_component + high_risk_component + flag_component
    score = round(min(100.0, max(0.0, score)), 1)

    if score >= 60:
        tier = "acil"
    elif score >= 30:
        tier = "incele"
    else:
        tier = "izlemede"

    if coverage < 0.2 and tier == "acil":
        tier = "incele"

    meta = {
        "total_claims": total,
        "scored_claims": len(scored_suspicions),
        "fact_check_coverage": round(coverage, 3),
        "avg_suspicion": round(avg_suspicion, 1) if avg_suspicion is not None else None,
        "false_or_disputed": false_or_disputed,
        "high_risk_claims": high_risk,
        "score_components": {
            "avg_suspicion": round(suspicion_component, 1),
            "high_suspicion_peak": round(peak_component, 1),
            "unscored_high_risk": round(unscored_component, 1),
            "high_risk_density": round(high_risk_component, 1),
            "behavior_flags": round(flag_component, 1),
        },
    }
    return score, tier, meta
