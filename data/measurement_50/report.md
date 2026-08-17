# 50 iddialık kontrollü ölçüm — rapor

Tarih: 2026-08-17. Batch ID: `msgbatch_01LcAj6W8GcfiuiLmE3y7Ru1`. Artifact: `data/measurement_50/`.

## Seçim

545'lik `veri_eksik` kuyruğundan (DB'de aktif + verdictsiz **636** iddia) stratified örnekleme:

| Kriter | Hedef (kuyruk oranı) | Seçilen 50 |
|--------|----------------------|------------|
| high | ~11% (72/636) | **6** |
| medium | ~50% (316/636) | **25** |
| low | ~39% (248/636) | **19** |

- **14 farklı video** (video başına max 5; bZsor/rHEf/n6vt/K7Yd/jP5 ×5, diğerleri 1–4)
- jP5 ilk-20 + recheck-3 iddiaları **dışarıda** (baseline ile çakışmasın diye)
- Seed: 42 → `selection.json`

Komut (seçim `--recheck-ids` ile; `--limit 50` tek başına high-first sıralama yapardı):

```bash
./venv/bin/python pipeline/03_factcheck.py --batch-submit --recheck-ids <50 id> --skip-nli
./venv/bin/python pipeline/03_factcheck.py --batch-retrieve --wait
```

---

## Before / After tablo

| Metrik | Baseline (geçmiş) | Bu 50'lik tur |
|--------|-------------------|---------------|
| **web_search kullanım oranı** (override / toplam) | **82,6%** (jP5 20+3: 19/23 override) | **74,0%** (37/50 override) |
| ↑ parse fail dahil (tool açılmış say) | 87,0% (20/23) | 76,0% (38/50) |
| **retrieval_cited oranı** | **13,0%** (jP5: 3/23) | **24,0%** (12/50) |
| **specificity_tier dağılımı** | yok (mekanizma yoktu) | background **33**, supportive **14**, direct **3** |
| **no_direct_evidence_expected oranı** | yok | **30,0%** (15/50; submit öncesi sınıflandırıcı) |
| **insan-onayı gereken oranı** (needs_human) | **100%** (jP5 23/23) · **83,2%** (odZg+bZsor+jP5 escalated 89/107) | **94,0%** (47/50) |
| **İddia başına ort. maliyet ($)** | **$0,175** (jP5 sync Sonnet, usage.json) | **$0,058** (batch + cache, tahmini*) |

\*Maliyet: Sonnet $3/M in + $15/M out, batch %50 indirim; cache write $3,75/M, read $0,30/M. Billing API yok — token sayacından türetildi.

### Baseline kaynağı

- **jP5 ilk-20 + recheck-3** (23 iddia): retrieval-escalate + web_search açık pipeline; en yakın apples-to-apples karşılaştırma.
- **odZg / bZsor**: eski tur (cite_source debug'da seyrek; override ~1–5%). Tabloya dahil edilmedi — farklı pipeline nesli.

---

## Specificity tier (yeni mekanizma)

Submit öncesi `assess_evidence_sufficiency` + epistemik sınıflandırıcı:

| Tier | n | % | Not |
|------|---|---|-----|
| background | 33 | 66% | Paket zayıf/genel; web_search açık |
| supportive | 14 | 28% | Paket kısmen alakalı; web_search açık, paket öncelikli |
| direct | 3 | 6% | `force_package_only=True` (357, 810, 1168) |

`no_direct_evidence_expected`: **15/50** — bu iddialarda doğrudan kanıt beklenmiyor flag'i prompt'a eklendi.

---

## Prompt cache (custom_id usage)

| Rol | n |
|-----|---|
| write + read (both) | **46** |
| write only | **4** |
| read only | **0** |
| none | **0** |

Toplam batch usage: input **226 470**, output **47 852**, cache write **1 100 579**, cache read **779 609**.

46/50 istek ikinci+ iddiada cache read aldı → batch içi prompt caching çalışıyor. Maliyet jP5 sync'e göre ~**3× düşük** ($0,058 vs $0,175/iddia).

---

## Verdict dağılımı (50)

| Verdict | n |
|---------|---|
| doğrulanmış | 20 |
| tartışmalı | 16 |
| belirsiz | 10 |
| yanlış | 3 |
| parse fail | 1 (#810, package_only + JSON kırpılması) |

Tek otomasyon bypass (needs_human=False): **#1113** (supportive tier, retrieval_cited, venöz kapak mekanizması).

---

## 752 tipi bileşik iddialar → decomposition gerekir mi?

**752 paterni:** bileşik iddia («A **ve** B») + karışık kanıt → `tartışmalı` (binary kaçış yok) + çoğunlukla `web_search_override`.

Bu 50'de:

| Metrik | Değer |
|--------|-------|
| Bileşik aday (`… ve …`, ≥25 char/clause) | **17/50** (34%) |
| 752-benzeri (bileşik + tartışmalı/belirsiz/mixed stance) | **10/50** (20%) |

752-benzeri ID'ler: **357, 801, 813, 901, 956, 978, 1006, 1043, 1129, 1168**

Örnekler:
- **801** — protein/yağ birleştirme + yürüyüş glisemi → tartışmalı @0,45 (mixed stance; kural çalıştı)
- **956** — gecikmiş mide boşalması + kortizol → tartışmalı @0,35 (mixed)
- **1043** — tiroid/kıl: bileşik → tartışmalı @0,55 (mixed, retrieval_cited)

**Yorum:** Bileşik iddialar örneklemin üçte birinde; bunların yarısından fazlasında (10/17) 752 tipi «karışık bileşen» çıktısı oluştu. Tam decomposition olmadan kural + tier mekanizması çoğunu `tartışmalı`/`belirsiz`'de tutuyor; ancak **%20 sıklık** ayrı claim split'in ROI tartışmasını destekliyor — özellikle maliyetli override çağrılarında (978, 956).

---

## Öne çıkan farklar (before → after)

1. **retrieval_cited 13% → 24%** — specificity tier + paket-öncelikli supportive yolu işe yarıyor; web_search override oranı düşüyor.
2. **Maliyet ~3×** — batch API + prompt cache (46/50 read hit).
3. **no_direct_evidence_expected %30** — yeni sinyal; epistemik sınıflandırıcı submit öncesi devrede.
4. **needs_human %100 → %94** — tek auto-accept (#1113); hâlâ yüksek insan yükü.
5. **Parse fail 1/50** (#810 direct/package_only) — jP5'teki 1265 paterni tekrarlandı.

---

## Kapsam dışı (uyuldu)

- Eski pipeline yeniden koşturulmadı
- Claim decomposition / konu önbelleği uygulanmadı
- 545 kuyruğunun geri kalanına dokunulmadı (50 iddia verdict aldı; kalan ~586 verdictsiz)

Ham veri: `selection.json`, `metrics.json`, `batch_submit.log`, `batch_retrieve.log`, `pending_batches.json` (son batch kaydı).
