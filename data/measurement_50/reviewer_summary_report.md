# Reviewer summary — measurement_50 (needs_human=True)

Toplam needs_human satır: **47** (auto_accepted=0)

## Özet istatistik

- Spesifik check_point: **45** (95.7%)
- Genel fallback check_point: **2** (4.3%)

### risk_level dağılımı

- yüksek: **21**
- orta: **18**
- düşük: **8**

## Örnek çıktılar

### #248 — mekanizma / doğrulanmış / risk=orta

**İddia:** Yaşlanmayla birlikte anabolik direnç gelişir, bu nedenle yaşlı bir vücudun kas sentezini başlatmak için genç bir bedene göre tek seferde dah…

- check_point: Bu iddia için literatürde doğrudan bir çalışma bulunamadı; kanıt dolaylı/mekanistik.
- one_line_reason: Paket dışı kaynakla 'doğrulanmış' — URL ve iddia eşleşmesini doğrulayın.
- source_note: Claude'un kendi aramasından (pakette değildi)
- model_disagreement: False
- evidence_snippet: Paketteki PubMed özetleri (anabolik direnç kavramı) ve ek aramada bulunan çalışmalar bu iddianın iki bileşenini de destekliyor: (1) yaşlanmayla kas protein sentezinin protein alımı…

### #253 — tedavi / tartışmalı / risk=yüksek

**İddia:** Kollarsız bir sandalyeden elleri kullanmadan günde 2-3 kez 10 tekrar ayağa kalkma egzersizi bacak kaslarını güçlendirmenin en etkili yoludur…

- check_point: Bileşik iddia — bileşenlerden biri destekleniyor, diğeri desteklenmiyor olabilir. Hangi bileşenin sorunlu olduğuna bakın.
- one_line_reason: Kanıt karışık — 'tartışmalı' hükmünde hangi bileşen sorunlu netleştirin.
- source_note: Claude'un kendi aramasından (pakette değildi)
- model_disagreement: False
- evidence_snippet: Retrieval paketindeki kaynaklar (LIFTMOR-M, osteoporoz egzersiz programları, exercise snacking pilot çalışması) doğrudan 'kollarsız sandalyeden 10 tekrar günde 2-3 kez' protokolünü…

### #377 — doz / yanlış / risk=yüksek

**İddia:** Bu pancar rutininin haftada en az 4-5 gün uygulanması gerekir.

- check_point: Verdict kendi gerekçesiyle tam örtüşmüyor — Diğer kaynaklar da (Avustralya Spor Enstitüsü, RunnersConnect, Sporcu Diyetisyenim) 3-6 gün yükleme protokolünden bahsediyor, bu da iddiadak… kısmının doğrudan kanıtı var mı, bakın.
- one_line_reason: Model 'yanlış' dedi; gerekçe tam destek/çürütme göstermiyor.
- source_note: Claude'un kendi aramasından (pakette değildi)
- model_disagreement: True
- evidence_snippet: Sağlanan PubMed/Europe PMC paketi tamamen ilgisizdi (diyet paterni skorlama metodolojileri, RA, post-COVID vb.), bu nedenle ek arama yapıldı. Bulunan güncel bir umbrella review, pa…

### #1028 — diğer / doğrulanmış / risk=düşük

**İddia:** İnsan bağırsağında sayıları 38 trilyonu bulan bir mikroorganizma ekosistemi bulunur.

- check_point: Claude paket dışı bir kaynak buldu — URL'nin iddiayı doğrudan destekleyip desteklemediğine bakın.
- one_line_reason: Paket dışı kaynakla 'doğrulanmış' — URL ve iddia eşleşmesini doğrulayın.
- source_note: Claude'un kendi aramasından (pakette değildi)
- model_disagreement: False
- evidence_snippet: Sender, Fuchs ve Milo'nun 2016 tarihli, yaygın kabul gören referans çalışması, 70 kg 'referans insan'da toplam bakteri sayısını 3.8×10¹³ (38 trilyon) olarak tahmin ediyor ve bu pop…

### #1072 — tanı / doğrulanmış / risk=yüksek

**İddia:** Çevresel görüşte yanıp sönen ışık çakmaları retina yırtılması veya ayrılmasına işaret edebilir.

- check_point: Model 'doğrulanmış' dedi — kaynak URL'si ve gerekçe iddiayla örtüşüyor mu kontrol edin.
- one_line_reason: Model 'doğrulanmış' dedi — kaynak ve gerekçe tutarlı mı bakın.
- source_note: Sağlanan kanıt paketinden
- model_disagreement: False
- evidence_snippet: PubMed kaynağı, retina dekolmanı öncesinde fotopsi (yanıp sönen ışık) ve floater semptomlarının erken uyarı işareti olduğunu belirtiyor; ayrıca posterior vitreus ayrılmasına ilişki…

### #357 — tedavi / tartışmalı / risk=yüksek

**İddia:** 74 yaş civarındaki kişilere pancar suyuna dayalı yüksek nitrat içeren kahvaltı verildiğinde birkaç gün sonra çekilen beyin MR'larında hafıza…

- check_point: Bileşik iddia — bileşenlerden biri destekleniyor, diğeri desteklenmiyor olabilir. Hangi bileşenin sorunlu olduğuna bakın.
- one_line_reason: Kanıt karışık — 'tartışmalı' hükmünde hangi bileşen sorunlu netleştirin.
- source_note: Sağlanan kanıt paketinden
- model_disagreement: False
- evidence_snippet: İddia, muhtemelen Wake Forest Üniversitesi'nin (Presley ve ark., Nitric Oxide 2011) yaşlılarda pancar suyu sonrası fMRI ile frontal lob perfüzyonunda bölgesel artış gösterdiği pilo…

