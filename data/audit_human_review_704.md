# İnsan onay denetimi — claim [704]

**İddia:** Lahana ve marulda bulunan yüksek K vitamini, kan sulandırıcı antikoagülan ilaçlarla doğrudan etkileşime girer.

**Tarih:** 2026-08-13 (odZg fact-check turu sonrası)

## DB durumu

| Alan | Değer |
|---|---|
| `claim_id` | 704 |
| `category` | mekanizma *(HIGH_RISK_HUMAN_REVIEW_CATEGORIES içinde değil)* |
| `initial_risk` | high |
| `human_reviewed` | **0** (insan onayı bekliyor) |
| `final_verdict` | tartışmalı @ 0.55 |
| `calibration_flags` | default_conf |
| `source_tier` | systematic_review |
| `escalated` | 1 |

## Hangi kural tetikledi?

`pipeline/03_factcheck.py` içindeki `needs_human` mantığı:

```python
needs_human = (
    category in HIGH_RISK_HUMAN_REVIEW_CATEGORIES  # mekanizma → HAYIR
    or initial_risk == "high"                       # → EVET ✓
    or parse_failed
    or final_verdict is None
    or (escalated and calibrated.needs_human)       # default_conf → EVET ✓
)
```

**Sonuç:** Kategori kuralı (`mekanizma`) bu iddiayı kaçırmadı — **`initial_risk=high`** birincil tetikleyici. Ek olarak `default_conf` bayrağı `calibrate_factcheck().needs_human=True` üretir; ikinci güvenlik ağı.

## Karşı-olgu: initial_risk=medium olsaydı?

- `escalated=1` (NLI belirsiz veya kanıt yok senaryosunda)
- `calibration_flags=default_conf` → `needs_human=True`
- Yine `human_reviewed=0` olurdu.

İlaç-etkileşimi riski taşıyan bu iddia **iki bağımsız ağ** ile korunuyor.

## Klinik bağlam

Fact-check reasoning: warfarin/vitamin K antagonist etkileşimi yerleşik klinik gerçek; DOAC'lar bu etkileşimden etkilenmez; "kan sulandırıcı" genellemesi abartılı. Verdict **tartışmalı** — insan incelemesi uygun.

## Opsiyonel iyileştirme (uygulandı)

`03_factcheck.py`'ye `is_drug_interaction_claim()` eklendi — antikoagülan/warfarin/DOAC/ilaç-etkileşimi regex'i `mekanizma` kategorisinde bile `human_reviewed=0` zorunlu kılar.
