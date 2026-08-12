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
