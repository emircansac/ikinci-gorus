# measurement_50 — bileşik iddia decomposition (offline)

Kayıtlı `pending_batches.json` kanıt paketleri. Yeni PubMed/Serper/Claude yok; yerel `assess_evidence_sufficiency` (NLI) bileşen bazında yeniden puanlandı.

- İşlenen: **10/10**
- Heuristik ≥2 parça: **10/10**
- Anlamlı tier farkı (biri direct/supportive, diğeri background/none): **2/10**

## Özet tablo

| ID | split | whole | bileşen tier'ları | gap |
|----|-------|-------|-------------------|-----|
| 357 | 2 | direct | direct(kept=5), direct(kept=5) | hayır |
| 801 | 2 | background | background(kept=5), background(kept=5) | hayır |
| 813 | 2 | supportive | supportive(kept=5), background(kept=5) | evet |
| 901 | 2 | background | background(kept=5), background(kept=5) | hayır |
| 956 | 2 | background | background(kept=2), background(kept=4) | hayır |
| 978 | 2 | background | background(kept=5), background(kept=5) | hayır |
| 1006 | 2 | background | background(kept=5), background(kept=5) | hayır |
| 1043 | 2 | supportive | supportive(kept=5), background(kept=5) | evet |
| 1129 | 2 | background | background(kept=5), background(kept=5) | hayır |
| 1168 | 2 | direct | direct(kept=5), supportive(kept=5) | hayır |

## Her ID

### #357
- **claim:** 74 yaş civarındaki kişilere pancar suyuna dayalı yüksek nitrat içeren kahvaltı verildiğinde birkaç gün sonra çekilen beyin MR'larında hafıza ve karar vermeden sorumlu bölgelere giden kan akışında nokta atışı artış görüldü.
- **parça 1:** 74 yaş civarındaki kişilere pancar suyuna dayalı yüksek nitrat içeren kahvaltı verildiğinde birkaç gün sonra çekilen beyin MR'larında hafıza
- **parça 2:** karar vermeden sorumlu bölgelere giden kan akışında nokta atışı artış görüldü
- **whole tier:** direct (job specificity_tier=direct)
- **bileşen 1** tier=`direct` reason=`ok` kept=5 — Caloric Restriction (CR) Plus High-Nitrate Beetroot Juice Does Not Amplify CR-In; Nitric Oxide Pathways in Neurovascular Coupling Under Normal and Stress Conditio; Effects of increased nitrate intake from beetroot juice on blood markers of oxid
- **bileşen 2** tier=`direct` reason=`ok` kept=5 — Caloric Restriction (CR) Plus High-Nitrate Beetroot Juice Does Not Amplify CR-In; Nitric Oxide Pathways in Neurovascular Coupling Under Normal and Stress Conditio; Effects of increased nitrate intake from beetroot juice on blood markers of oxid
- **gap:** hayır — bileşenler aynı kademe bandında

### #801
- **claim:** Yüksek glisemik yüklü meyveleri protein veya yağla birleştirmek, günün erken saatlerinde tüketmek, sonrasında yürüyüş yapmak ve miktarı kontrol etmek kan şekeri etkisini azaltır.
- **parça 1:** Yüksek glisemik yüklü meyveleri protein veya yağla birleştirmek, günün erken saatlerinde tüketmek, sonrasında yürüyüş yapmak
- **parça 2:** miktarı kontrol etmek kan şekeri etkisini azaltır
- **whole tier:** background (job specificity_tier=background)
- **bileşen 1** tier=`background` reason=`ok` kept=5 — Efficacy of a low-carbohydrate diet combined with exercise on glycemic control a; Effects of different types of meals on postprandial glycaemia in healthy subject; Integrative Strategies for Preventing and Managing Metabolic Syndrome: The Impac
- **bileşen 2** tier=`background` reason=`ok` kept=5 — Efficacy of a low-carbohydrate diet combined with exercise on glycemic control a; Effects of different types of meals on postprandial glycaemia in healthy subject; Integrative Strategies for Preventing and Managing Metabolic Syndrome: The Impac
- **gap:** hayır — bileşenler aynı kademe bandında

### #813
- **claim:** Triptofanı serotonine ve melatonine dönüştüren enzimler magnezyum, B6 vitamini ve çinko gibi vitaminlere ihtiyaç duyar.
- **parça 1:** Triptofanı serotonine ve melatonine dönüştüren enzimler magnezyum, B6 vitamini
- **parça 2:** çinko gibi vitaminlere ihtiyaç duyar
- **whole tier:** supportive (job specificity_tier=supportive)
- **bileşen 1** tier=`supportive` reason=`ok` kept=5 — A Comprehensive Review of Nutritional Influences on the Serotonergic System.; [Nutrition in improving sleep quality and fighting insomnia].; B Vitamins
- **bileşen 2** tier=`background` reason=`ok` kept=5 — A Comprehensive Review of Nutritional Influences on the Serotonergic System.; [Nutrition in improving sleep quality and fighting insomnia].; B Vitamins
- **gap:** evet — decomposition yeni bilgi ekliyor

### #901
- **claim:** Akşam saat 6'dan sonra işlenmiş gıdalardaki gizli sodyum tüketimi, vücudun sıvı tutmasına ve gece boyunca gözaltı bölgesinde sıvı birikimine yol açar.
- **parça 1:** Akşam saat 6'dan sonra işlenmiş gıdalardaki gizli sodyum tüketimi, vücudun sıvı tutmasına
- **parça 2:** gece boyunca gözaltı bölgesinde sıvı birikimine yol açar
- **whole tier:** background (job specificity_tier=background)
- **bileşen 1** tier=`background` reason=`ok` kept=5 — Mechanism of attenuated thirst in aging: role of central volume receptors.; Impact of sodium citrate ingestion during recovery after dehydrating exercise on; Sodium
- **bileşen 2** tier=`background` reason=`ok` kept=5 — Mechanism of attenuated thirst in aging: role of central volume receptors.; Impact of sodium citrate ingestion during recovery after dehydrating exercise on; Sodium
- **gap:** hayır — bileşenler aynı kademe bandında

### #956
- **claim:** Gecikmiş mide boşalması, kronik kortizol bozulmasının belgelenmiş bir sonucudur ve insülin problemini daha karmaşık hale getirir.
- **parça 1:** Gecikmiş mide boşalması, kronik kortizol bozulmasının belgelenmiş bir sonucudur
- **parça 2:** insülin problemini daha karmaşık hale getirir
- **whole tier:** background (job specificity_tier=background)
- **bileşen 1** tier=`background` reason=`ok` kept=2 — Dissecting the causal link between transient hypoglycemic coma and gut-brain axi; Therapeutic nutrition strategies for the shared pathophysiology of obesity and s
- **bileşen 2** tier=`background` reason=`ok` kept=4 — Diabetes Type 1; Prediabetes; Therapeutic nutrition strategies for the shared pathophysiology of obesity and s
- **gap:** hayır — bileşenler aynı kademe bandında

### #978
- **claim:** Retinol, peptit ve hyaluronik asit içeren kremler sadece epidermiste etkilidir ve derinin 1 cm altındaki kas/bağ dokusuna ulaşamaz.
- **parça 1:** Retinol, peptit ve hyaluronik asit içeren kremler sadece epidermiste etkilidir
- **parça 2:** derinin 1 cm altındaki kas/bağ dokusuna ulaşamaz
- **whole tier:** background (job specificity_tier=background)
- **bileşen 1** tier=`background` reason=`ok` kept=5 — Cellular, immunologic and biochemical characterization of topical retinoic acid-; Novel Cyclized Hexapeptide-9 Outperforms Retinol Against Skin Aging: A Randomize; Topical retinol attenuates stress-induced ageing signs in human skin ex vivo, th
- **bileşen 2** tier=`background` reason=`ok` kept=5 — Cellular, immunologic and biochemical characterization of topical retinoic acid-; Novel Cyclized Hexapeptide-9 Outperforms Retinol Against Skin Aging: A Randomize; Topical retinol attenuates stress-induced ageing signs in human skin ex vivo, th
- **gap:** hayır — bileşenler aynı kademe bandında

### #1006
- **claim:** Ayşe Hanım vaka örneğinde, rutine başladıktan 8 hafta sonra çenesiyle boğazı arasındaki sarkık açıda gözle görülür ve ölçülebilir bir toparlanma oluştu.
- **parça 1:** Ayşe Hanım vaka örneğinde, rutine başladıktan 8 hafta sonra çenesiyle boğazı arasındaki sarkık açıda gözle görülür
- **parça 2:** ölçülebilir bir toparlanma oluştu
- **whole tier:** background (job specificity_tier=background)
- **bileşen 1** tier=`background` reason=`ok` kept=5 — Face-neck lifting and ancillary procedures: A series of 203 cases.; Chin Tuck Exercise and Obstructive Sleep Apnea: A Case Report.; A 13-Year Long-Term Follow-Up of a Case Report With Continued Improvement in Sev
- **bileşen 2** tier=`background` reason=`ok` kept=5 — Face-neck lifting and ancillary procedures: A series of 203 cases.; Chin Tuck Exercise and Obstructive Sleep Apnea: A Case Report.; A 13-Year Long-Term Follow-Up of a Case Report With Continued Improvement in Sev
- **gap:** hayır — bileşenler aynı kademe bandında

### #1043
- **claim:** Tiroid bezi saç hücrelerinin bölünme ve keratin üretme hızını belirler.
- **parça 1:** Tiroid bezi saç hücrelerinin bölünme
- **parça 2:** keratin üretme hızını belirler
- **whole tier:** supportive (job specificity_tier=supportive)
- **bileşen 1** tier=`supportive` reason=`ok` kept=5 — Thyroid hormone signaling controls hair follicle stem cell function.; Human female hair follicles are a direct, nonclassical target for thyroid-stimul; Growth Hormone and the Human Hair Follicle.
- **bileşen 2** tier=`background` reason=`ok` kept=5 — Thyroid hormone signaling controls hair follicle stem cell function.; Human female hair follicles are a direct, nonclassical target for thyroid-stimul; Growth Hormone and the Human Hair Follicle.
- **gap:** evet — decomposition yeni bilgi ekliyor

### #1129
- **claim:** Flavonoidler damar duvarları ve kapakçıkların dayanıklılığını doğrudan korur, eksikliği zamanla damarları zayıflatır
- **parça 1:** Flavonoidler damar duvarları
- **parça 2:** kapakçıkların dayanıklılığını doğrudan korur, eksikliği zamanla damarları zayıflatır
- **whole tier:** background (job specificity_tier=background)
- **bileşen 1** tier=`background` reason=`ok` kept=5 — The Roles of Oxidative Stress and Red Blood Cells in the Pathology of the Varico; The Use of Plants That Seal Blood Vessels in Preparations Applied Topically to t; Efficacy and mechanism of escin in improving the tissue microenvironment of bloo
- **bileşen 2** tier=`background` reason=`ok` kept=5 — The Roles of Oxidative Stress and Red Blood Cells in the Pathology of the Varico; The Use of Plants That Seal Blood Vessels in Preparations Applied Topically to t; Efficacy and mechanism of escin in improving the tissue microenvironment of bloo
- **gap:** hayır — bileşenler aynı kademe bandında

### #1168
- **claim:** Başka hastalık belirtisi göstermeyen ve kronik akşam şişkinliği yaşayan 60 yaş üstü bireylerin çoğu lenfatik kategoride yer alır.
- **parça 1:** Başka hastalık belirtisi göstermeyen
- **parça 2:** kronik akşam şişkinliği yaşayan 60 yaş üstü bireylerin çoğu lenfatik kategoride yer alır
- **whole tier:** direct (job specificity_tier=direct)
- **bileşen 1** tier=`direct` reason=`ok` kept=5 — A rare case of sporotrichosis in a patient with late latent syphilis and diabete; Lipedema: Clinical Features, Diagnosis, and Management.; LIMPRINT in Italy.
- **bileşen 2** tier=`supportive` reason=`ok` kept=5 — A rare case of sporotrichosis in a patient with late latent syphilis and diabete; Lipedema: Clinical Features, Diagnosis, and Management.; LIMPRINT in Italy.
- **gap:** hayır — bileşenler aynı kademe bandında

## Canlı deneme

Offline gap çıkan **2 ID** (`813`, `1043`) `--recheck-ids` ile normal fact-check akışında koşuldu (üçüncü gap yoktu).

| ID | escalate? | verdict önce → sonra | cite önce → sonra | map Claude'a gitti mi? |
|----|-----------|----------------------|-------------------|------------------------|
| **813** | hayır (NLI SUPPORTS@0.77) | tartışmalı@0.45 → **doğrulanmış@0.77** | web_search_override → (NLI, cite yok) | hayır |
| **1043** | evet | tartışmalı@0.55 → **tartışmalı@0.45** | retrieval_cited → retrieval_cited | evet (supportive / background) |

### #813 — map test edilemedi

measurement_50 `--skip-nli` ile escalate olmuştu; bu koşumda ucuz NLI kendi başına karar verdi (`escalated=0`). Verdict değişimi **decomposition değil**, NLI yolundan. `component_evidence_map` escalate paketine girmedi.

### #1043 — map escalate'e girdi

- **Verdict:** aynı (`tartışmalı`); confidence 0.55 → 0.45 (`default_conf` kalktı, kalibrasyon eşiği değişmedi).
- **cite_source:** aynı (`retrieval_cited`).
- **Reasoning:** önce de A/B ayrımı vardı; sonra da var ve netleşti — proliferasyon güçlü, keratin üretim hızı dolaylı/zayıf; «bileşenlerden biri … diğeri …» dilini kullanıyor.

**Sonra (tam):**
> Kanıtlar, tiroid hormonunun ve reseptörlerinin (TRα1/TRβ) saç folikülü kök hücre fonksiyonunu ve foliküler hücre proliferasyonunu düzenlediğini güçlü şekilde destekliyor; TR eksikliği olan farelerde saç döngüsü bozuk ve foliküler hücre çoğalması azalmış bulunmuş. Ancak 'keratin üretme hızını belirler' kısmı doğrudan test edilmemiş; TSH'nin keratin K5 gen ekspresyonunu değiştirdiği gösterilse de bu TSH'ye ait ayrı bir mekanizma olup tiroid hormonunun keratin üretim hızını 'belirlediği' iddiasını doğrudan kanıtlamıyor. İddia 'belirler' gibi kesin ve tek yönlü bir nedensellik öne sürüyor, oysa kanıt kümesi düzenleyici/etkileyici bir rol gösteriyor ve bileşenlerden biri (hücre bölünmesi) güçlü, diğeri (keratin üretim hızı) dolaylı/zayıf destekli; bu nedenle bileşik iddia karışık kanıt düzeyinde kalıyor.

## Heuristik notu

10/10 `ve` ile bölündü ama bazı kesimler cümle ortası: #1043 «bölünme | keratin», #357 «hafıza | karar vermeden», #1006 «görülür | ölçülebilir». Gap 2/10'da gerçekten farklı NLI kademesi üretti; diğer 8'de aynı paket her iki parçaya da benzer puan verdi.

Ham JSON: `decompose_offline.json`, `decompose_live_before.json`, `decompose_live_after.json`. Log: `decompose_live.log`.
