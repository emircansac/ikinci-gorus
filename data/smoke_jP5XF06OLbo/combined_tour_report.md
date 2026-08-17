# Birleşik düşük maliyetli doğrulama turu — rapor

Tarih: 2026-08-17. Video: `jP5XF06OLbo` (kahve/kas, v2.1). Artifact dizini: `data/smoke_jP5XF06OLbo/`.

Kapsam dışı tutuldu: kalan 51 jP5 iddiası, 545’lik kuyruğa toplu fact-check, cosine/lexical/`tier_cap` eşikleri.

---

## Adım 1 — Extraction + API’siz doğrulama

Komut: `SAVE_EXTRACTION_CHUNKS=1 python pipeline/08_reextract_compare.py --video-id jP5XF06OLbo --extraction-version v2.1 --export-dir data/smoke_jP5XF06OLbo`

### Sayılar (odZg / P4m9 / bZsor protokolü)

| Metrik | Değer |
|--------|-------|
| Transkript | 43 458 karakter → **5 parça** (son parça recap) |
| Chunk ham | 15 + 14 + 19 + 19 + 11 = **78** |
| Chunk-içi local dedup | 15 + 14 + 19 + 19 + 11 = **78** (parça içinde 0 düşüş) |
| Pipeline (window + recap) | **71** |
| DB after (aktif v2.1) | **71** |
| pipeline vs DB | **eşleşti** |
| Dup küme (0.85) after | **0** |
| v1 arşiv | 71 (silinmedi) |
| Risk | v1 8/40/23 high/med/low → v2.1 **4 / 38 / 29** |
| `diğer` kategori | 8 → **2** |

Chunk log: `data/extraction_chunks/jP5XF06OLbo.json`. Offline replay: `data/smoke_jP5XF06OLbo/offline_dedup.json`.

**Dedup öncesi/sonrası:** ham 78 → pipeline 71 (−7). Düşenler recap/pencere tekrarları; örnekler:

- «Seylan tarçını insülin duyarlılığını artırır.» (daha uzun mekanizma iddiası tutuldu)
- «Günde 10–15 gram hidrolize kolajen 12 hafta… eklem ağrılarını belirgin ölçüde azaltır.» (chunk 3/4 tekrarı)
- Recap: «Uyandıktan hemen sonra aç karna boş kahve içmek…»

Chunk-içi katman bu videoda boş çalıştı (her parça zaten tekil); global pencere + recap filtresi 7 iddiayı kesti — beklenen davranış.

### Audit (`07_audit_extraction.py --video-id jP5XF06OLbo`)

1 yapısal uyarı:

- `cok_uzun_iddia` claim_id **1265** (41 kelime): «61 yaşındaki bir hastanın günlük yürüyüş yapmasına rağmen düşük proteinli kahvaltı (4 gramın altında…»

Örnek tutulan iddia: «İleri yaşta kalça kırığı geçiren her dört kişiden biri eski bağımsızlığına kavuşamaz.»

---

## Adım 2 — 15–20 iddialık fact-check (bileşik kural)

Seçim: mevcut `ORDER BY high → medium → claim_id` ile ilk 20. Top-20’de sıkı bileşik yoktu; son 3 slot (1273, 1274, 1275) yerine **1281, 1284, 1256** alındı. Liste: `factcheck_ids.txt`.

```
--recheck-ids 1244,1245,1252,1253,1242,1243,1247,1248,1254,1255,1261,1262,1263,1265,1267,1268,1271,1281,1284,1256 --skip-nli
```

20/20 işlendi, 0 API hatası. Dağılım: **11 tartışmalı, 4 doğrulanmış, 3 belirsiz, 1 yanlış, 1 parse fail (1265).**

Serper bu 20’de yalnızca **1255**’te (native yetersiz → `europepmc+serper`).

### İşaretli bileşik iddialar

**1281** — «62–76 yaş… karabiberli zerdeçal… inflamasyon belirteçlerini düşürmüş **ve** egzersiz sonrası kas eklem ağrılarını azaltmıştır.»

- Ham / kalibre: **belirsiz** @ 0.30 (binary kaçış yok)
- `cite_source`: **retrieval_cited** (paket PubMed 38561618)
- `retrieval_tier`: native; path `pubmed_mesh+europepmc`
- `verdict_reasoning_mismatch`: hayır
- Neden tartışmalı değil: paket spesifik 62–76 / 12 hafta çalışmasını bulamadı; her iki bileşen de yetersiz → kuralın «biri destekli diğeri değil» kolu tetiklenmedi. Muhafazakâr `belirsiz` doğru.

**1284** — «Ölçülü kahve tüketimi **Alzheimer ve Parkinson** … riskini azaltır.»

- Ham / kalibre: **tartışmalı** @ 0.40
- Stance: **mixed**. Reasoning: Parkinson epidemiyolojisi daha tutarlı; Alzheimer/demans meta-analizi (Larsson & Orsini 2018) anlamlı ilişki bulamadı.
- Kural **canlıda çalıştı** — karışık bileşenlerde binary’ye kaçmadı.
- `cite_source`: web_search_override (PMC6213481, paketteki pubmed URL’leriyle kesişmedi)
- `source_tier`: model `systematic_review` → URL `primary_study` (`tier_url:systematic_review->primary_study`) — kademe model beyanına düşmedi.
- mismatch: hayır

**1256** — «Kakao flavonolleri … damar esnekliğini **ve** kas dokusuna kan akışını artırır.»

- Ham / kalibre: **doğrulanmış** @ 0.70, stance **supports**
- Kural izin veriyor: her iki bileşen aynı yönde. Binary kaçış değil.
- `cite_source`: web_search_override (`doi.org/10.3390/nu13051646`, pakette pubmed host’ları)
- mismatch: hayır (reasoning’de küçük-örnek uyarısı var ama `PARTIAL_REASONING_RE` tetiklemedi)

### `verdict_reasoning_mismatch` (20’lik sette)

Bayrak **2 iddiada** tetiklendi; verdict değiştirilmedi, insan kuyruğuna düştü:

| ID | Verdict | Neden |
|----|---------|--------|
| 1252 | doğrulanmış @ 0.65 | BfR/ANSES kumarin–karaciğer; flags `tier_url:guideline->other` + mismatch |
| 1253 | doğrulanmış @ 0.68 | Warfarin etkileşimi; reasoning «ancak klinik kanıt henüz sınırlı» |

1265: JSON parse fail → verdict yazılmadı (`None`); insan incelemesi.

---

## Adım 3 — Retrieval-escalate canlı (745, 752, 663)

`--recheck-ids 745,752,663 --skip-nli`. Üçü de native paketten escalate edildi; **Serper çağrılmadı**. Healio.com düşüşü **yok**.

| ID | Paket kademesi | path | cite_source | source_url host | source_tier (ham→kalibre) | verdict |
|----|----------------|------|-------------|-----------------|---------------------------|---------|
| 745 | native (5) | pubmed+europepmc+medlineplus | web_search_override | journals.physiology.org | primary_study → primary_study | tartışmalı @ 0.35 |
| 752 | native (1) | europepmc+medlineplus | web_search_override | ncbi.nlm.nih.gov/pmc | primary_study → primary_study | tartışmalı @ 0.35 |
| 663 | native (5) | europepmc+medlineplus | web_search_override | tools.myfooddata.com | **nutrition_db → other** | tartışmalı @ 0.35 |

### 652/healio tipi kontrol

Hiçbirinde healio/healthline blog düşüşü yok. Override’lar:

- **745:** paket 5 PubMed; model AJP Heart publisher URL’si seçti (aynı makale pakette olmayabilir).
- **752:** alaka filtresi 14 adayı eledi, pakette **tek** PubMed (41377596) kaldı; model PMC4488775’i web_search ile aldı. NCBI idconv: **farklı makaleler** (aşağıdaki ek not). Köprü gerilemesi değil.
- **663:** paket PubMed; model USDA ayna sitesi myfooddata. Model `nutrition_db` dedi, `infer_source_tier` URL’den **other** yazdı (`tier_url:nutrition_db->other`). Kademe hâlâ URL/metadata, model beyanı değil.

752 bileşik kuralı da tuttu: insülin duyarlılığı karışık RCT’ler + mikrovasküler «kanıtlandı» abartısı → **tartışmalı** (önceki turda ham `yanlış` kalibre `tartışmalı` idi; bu turda model zaten tartışmalı verdi).

---

## Toplam maliyet

Projede billing API yok; [console.anthropic.com](https://console.anthropic.com) bu ortamdan okunamadı. Token sayaçları `usage.json`:

| Aşama | Claude çağrısı | input | output |
|-------|----------------|-------|--------|
| Extraction (5 chunk) | 5 | 32 689 | 10 477 |
| Fact-check 20 | 20 | 1 097 388 | 24 484 |
| Recheck 3 | 3 | 107 131 | 3 032 |
| **Toplam** | **28** | **1 237 208** | **37 993** |

Fact-check input’u web_search tool trafiğini içerir (tek iddiada 100k+ input görüldü). Kaba sipariş: Sonnet sınıfı ~$3/M input + $15/M output ≈ **$4.3 token** + web_search tool ücreti (konsolda ayrı satır). Gerçek $ için console Usage sayfasına bakın.

Kuyruk: jP5 v2.1’de **51** iddia hâlâ verdict’siz (bilinçli). Global pending ~646; 545’lik kuyruğa `--limit` ile dokunulmadı. Reextract, jP5 v1 `veri_eksik` satırlarını arşivledi (silinmedi).

`pipeline/06_claim_index.py` çalıştırıldı: 798 aktif iddia → `data/claim_index.csv`.

---

## Ek notlar (752 köprü / token / sayısal koruma)

### 752 — PMC4488775 paketteki PubMed ile aynı makale değil

Ham JSON: paket `https://pubmed.ncbi.nlm.nih.gov/41377596/`; Claude `https://www.ncbi.nlm.nih.gov/pmc/articles/PMC4488775/`.

NCBI idconv + esummary (2026-08-17):

| | Paket | Claude cite |
|--|-------|-------------|
| PMID | **41377596** | **26024297** |
| PMCID | **PMC12687994** | **PMC4488775** |
| DOI | 10.4162/nrp.2025.19.6.839 | 10.3390/nu7064107 |
| Yıl | 2025 | 2015 |
| Başlık | *Vaccinium oldhamii* fruits improve insulin resistance by inhibiting inflammation in macrophages and adipocytes | Blueberries improve endothelial function, but not blood pressure, in adults with metabolic syndrome (Stull RCT) |

Kesişim boş (PMID/PMCID/DOI hiçbiri örtüşmüyor). `web_search_override` **doğru**; DOI/PMCID köprüsü bu örnekte test edilmedi (aynı makalenin iki URL’si yok). Köprü ancak Claude `PMC12687994` verseydi sınanırdı.

### Token: override medyanı pakete yakın, kuyruk pahalı

Fact-check+recheck 23 çağrı. `cite_source` tool-use sayacı değil (Claude paketi cite edip yine web_search yapabilir); yine de eldeki tek ayırıcı.

| Grup | n | input ortalama | medyan | min–max |
|------|---|----------------|--------|---------|
| retrieval_cited | 1 (1281) | 31 911 | 31 911 | — |
| web_search_override | 21 | 50 797 | 35 739 | 22 711–112 864 |
| parse fail (cite=—) | 1 (1265) | 105 778 | — | — |

Override/cited: ortalama **1.59×**, medyan **1.12×**. 752 override olmasına rağmen **en ucuz** (22 711; pakette 1 parça). Pahalı kuyruk (hepsi override veya parse fail): 1243=112k, 1265=105k, 1255=95k, 1267=93k, 1247=73k, 1262=71k. Medyan farkı küçük; asıl maliyet 6 şişmiş çağrıda. 545 kuyruğuna geçmeden: kademeli getirme ortalama faturayı ancak Claude’un web_search’ü **hiç açmamasını** sağlarsa keser — şu an 22/23 escalate hâlâ override veya fail.

### Sayısal koruma — audit aramadı; dedup bu videoda şeftali/armut birleşmesini durdurmak zorunda kalmadı

`07_audit_extraction.py` sayısal çatışma aramaz (uzunluk, tekrar, recap, timestamp). bZsor’daki şeftali GI=42 vs armut GI=38 tipi tarama **yapılmadı**.

Ayrı (API’siz) tarama, 71 aktif jP5 iddiası:

- 22/71 iddia koruma eşiğine uygun (≥2 ayrı sayı).
- `pair_merge_blocked` 226 çift (çoğu düşük cosine/lexical; zaten birleşmezdi).
- Cosine≥0.8055 **ve** lexical≥0.35 olup korumanın durdurduğu çift: **0**. En yakın: 1265 vs 1300 (vaka 4 g vs 5 g protein) cos=0.814 ama jac=0.231 — lexical zaten ayırıyor.
- Tek yüksek-cosine sayısal çift 1275 vs 1298 (cos=0.861) **aynı** sayılar (60 / 20–30 g); koruma doğru olarak dokunmaz, lexical 0.16 ayrı tutar.

Sonuç: (b) audit bunu aramadı; (a) bu videoda şeftali/armut tipi bir birleşme adayı da yoktu. Koruma kodu extraction sırasında çalışıyor ama bu turda canlı bir «kurtarma» gözlenmedi.
