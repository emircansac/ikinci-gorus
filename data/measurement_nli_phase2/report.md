# NLI Phase 2 — 6 offline ölçüm

Kod / eşik / production kuralı **değişmedi**. Yeni model yok. `final_verdict` bu raporda **ground truth değil** — mevcut pahalı aşamanın referans verdict'i. Metrikler **NLI accuracy against truth değil**, **NLI agreement / safe-skip against current second-stage verdict**.

## Kohort ve skip tanımları

- Kohort: 554 Dilim 1–5 ID listesi. eligible = `escalated=1` → **n=551** (3 NLI-only, escalated=0, eligible dışı).
- NLI kaydı yok (Dilim 1 `--skip-nli`): **100/551** — sessizce False/0 sayılmadı; `missing/not_available`.
- Production NLI eşiği: **0.75** (bu turda değiştirilmedi).

| Skip tanımı | Kural | Bu raporda |
|---|---|---|
| `nli_threshold_pass` / would_skip_nli | SUPPORTS/REFUTES **ve** conf≥0.75 | Ölçüm 1 formül satırı; #1282 dahildir |
| `would_skip` (current-threshold) | aynı **ve** `evidence_has_partial_caveat` yok | dangerous_false_support paydası; #1282 **dahil değil** |

### #865 vs #1282 (karıştırma)

- **#865** NLI SUPPORTS@0.746 — eşik **altı** → şu an would_skip **değil**. Known dangerous NLI disagreement / regression case. **current-threshold dangerous_false_support SAYISINA KATILMAZ.**
- **#1282** NLI SUPPORTS@0.808 — eşik **üstü** ama parça 2'deki `however` `evidence_has_partial_caveat()` ile escalate etti → would_skip **değil**. **Başarılı regresyon-önleme**, kaçırılma değil. Confidence tek başına yeterli değil.

Binary ters (SUPPORTS→yanlış veya REFUTES→doğrulanmış) 554 kapanış metni **14** dedi; aynı tanımla DB'de **13** bulundu (kapanış JSON `binary=true` de 13). 14. kayıt uydurulmadı. Regresyon seti = binary ters ∪ öncelikli 4 (#865/#905/#961/#1282) → **n=16**.

## Ölçüm 3 ön-denetim — Claude'un kullandığı kanıt izleniyor mu?

Claude'un final kararında hangi paketi kullandığı URL eşlemesiyle kısmen izlenebiliyor (source_url ∩ paket). Eşleşen kayıtta paket title+abstract kullanılır. Eşleşmezse (web_search veya boş URL) alıntılanan metin YOK — uydurulmaz; retrieval en üst sırası PROXY.

| Alan | Durum |
|---|---|
| `verdicts.source_url` | var — Claude'un yazdığı URL |
| `verdicts.reasoning` | var — gerekçe metni; evidence ID yok |
| `verdicts.nli_evidence_snippet` | var — NLI'nın gördüğü 500 karakter; Claude cite'ı değil |
| `evidence_id` | yok |
| `cited_snippet_of_web_search` | yok — web_search içeriği saklanmıyor |
| `pending_batches.jobs[].evidence` | var — retrieval paketi (title/url/abstract) |
| `factcheck_debug.cite_source` | var (kayıt varsa) — retrieval_cited / web_search_* |
| `partial_caveat_matched_index` | 554 kayıtlarında yok (sonradan eklendi) |

- source_url dolu: 546/551 = 99.1% (Wilson 95% CI 97.9–99.6%)
- paket URL eşleşmesi (cited_package_item): 272/551 = 49.4% (Wilson 95% CI 45.2–53.5%)
- proxy relevance (exact cited not tracked): 279/551 = 50.6% (Wilson 95% CI 46.5–54.8%)
- paket/kanıt yok: 0/551 = 0.0% (Wilson 95% CI 0.0–0.7%)
- cite_source (flags): `{"retrieval_cited": 272, "web_search_override": 278, "missing/not_available": 1}`

---

## Ölçüm 1 — Mevcut model benchmark

safe_skip = NLI→verdict aynı yön **ve** final_verdict binary (doğrulanmış/yanlış). tartışmalı/belirsiz would_skip içindeyse paydadan **çıkarılmaz** — başarısız skip / collapse.

**A. Current-threshold would_skip (conf≥0.75 **ve** caveat yok) — dangerous_false_support paydası**

| Metrik | Sayı | Oran |
|---|---:|---|
| eligible_n | 551 | — |
| would_skip_n | 0 | 0/551 = 0.0% (Wilson 95% CI 0.0–0.7%) |
| safe_skip_n | 0 | missing/not_available (denominator=0) |
| dangerous_false_support | 0 | missing/not_available (denominator=0) |
| dangerous_false_refute | 0 | missing/not_available (denominator=0) |
| mixed_collapse | 0 | — |
| uncertain_collapse | 0 | — |
| collapse (mixed+uncertain) | 0 | missing/not_available (denominator=0) |

**B. NLI-only threshold pass (conf≥0.75, caveat kapısı **hariç**) — tanı kesiti**

| Metrik | Sayı | Oran |
|---|---:|---|
| eligible_n | 551 | — |
| would_skip_n | 3 | 3/551 = 0.5% (Wilson 95% CI 0.2–1.6%) |
| safe_skip_n | 2 | 2/3 = 66.7% (Wilson 95% CI 20.8–93.9%) |
| dangerous_false_support | 0 | 0/3 = 0.0% (Wilson 95% CI 0.0–56.2%) |
| dangerous_false_refute | 0 | 0/3 = 0.0% (Wilson 95% CI 0.0–56.2%) |
| mixed_collapse | 1 | — |
| uncertain_collapse | 0 | — |
| collapse (mixed+uncertain) | 1 | 1/3 = 33.3% (Wilson 95% CI 6.1–79.2%) |

nli_threshold_pass id: [1082, 1250, 1282]
current would_skip id: ∅

Yorum (karar değil): Escalated kohortta current-threshold skip **neredeyse/hiç yok** — 0.75 üstü SUPPORTS/REFUTES kayıtların hepsi caveat kapısından geçiyor. Bu, eşiği düşürmenin tek başına yetmeyeceğini (#1282) ve mevcut kapının yüksek-güven NLI-only'yi zaten kestiğini gösterir. #865 eşik altında kaldığı için current-threshold false-support **0** — bu 'iyileşti' değil, eşiğin o vakayı henüz skip etmemesi.

### Öncelikli golden + regresyon seti

| id | NLI | conf | Claude | tier | nli_pass | skip_now | caveat | binary_ters |
|---|---|---|---|---|---|---|---|---|
| #865 ★ | SUPPORTS | 0.746 | yanlış | supportive | False | False | False | True |
| #905 ★ | SUPPORTS | 0.687 | tartışmalı | background | False | False | False | False |
| #961 ★ | SUPPORTS | 0.679 | tartışmalı | supportive | False | False | True | False |
| #1282 ★ | SUPPORTS | 0.808 | tartışmalı | direct | True | False | True | False |
| #829 | SUPPORTS | 0.441 | yanlış | supportive | False | False | False | True |
| #835 | SUPPORTS | 0.535 | yanlış | supportive | False | False | True | True |
| #891 | SUPPORTS | 0.358 | yanlış | supportive | False | False | False | True |
| #913 | SUPPORTS | 0.421 | yanlış | background | False | False | True | True |
| #955 | SUPPORTS | 0.37 | yanlış | background | False | False | True | True |
| #1044 | SUPPORTS | 0.433 | yanlış | background | False | False | False | True |
| #1096 | SUPPORTS | 0.381 | yanlış | supportive | False | False | False | True |
| #1222 | SUPPORTS | 0.477 | yanlış | background | False | False | True | True |
| #1258 | SUPPORTS | 0.453 | yanlış | background | False | False | True | True |
| #1274 | SUPPORTS | 0.39 | yanlış | background | False | False | True | True |
| #1294 | REFUTES | 0.372 | doğrulanmış | background | False | False | False | True |
| #1296 | SUPPORTS | 0.439 | yanlış | background | False | False | False | True |

- **#865:** NLI SUPPORTS@0.746 (eşik ALTI) → current-threshold would_skip değil. Known dangerous NLI disagreement; alakasız kanıt (tekerlekli sandalye). dangerous_false_support SAYISINA KATILMAZ.
- **#905:** NLI SUPPORTS@0.687, Claude tartışmalı — partial evidence → binary collapse.
- **#961:** NLI SUPPORTS@0.679, Claude tartışmalı — complex/qualified evidence → binary collapse.
- **#1282:** NLI SUPPORTS@0.808 (eşik ÜSTÜ) ama partial_caveat (parça 2 'however') escalate etti → would_skip değil. Başarılı regresyon-önleme; kaçırılmadı. Confidence tek başına yeterli değil.

---

## Ölçüm 2 — specificity_tier × confidence

Her satır: eligible = escalated ∩ tier ∩ nli kayıtlı ∩ conf≥eşik. would_skip current = o kümede SUPPORTS/REFUTES ∩ caveat yok (eşik zaten eligible filtresinde).

| Koşul | eligible_n | would_skip_n (current) | safe_skip_precision | dangerous_false_support | collapse_rate |
|---|---:|---:|---|---|---|
| direct + confidence≥0.75 | 2 | 0 | missing/not_available (denominator=0) | 0 (missing/not_available (denominator=0)) | missing/not_available (denominator=0) |
| direct + confidence≥0.70 | 8 | 1 | 0/1 = 0.0% (Wilson 95% CI 0.0–79.3%) | 0 (0/1 = 0.0% (Wilson 95% CI 0.0–79.3%)) | 1/1 = 100.0% (Wilson 95% CI 20.7–100.0%) |
| direct + confidence≥0.65 | 9 | 1 | 0/1 = 0.0% (Wilson 95% CI 0.0–79.3%) | 0 (0/1 = 0.0% (Wilson 95% CI 0.0–79.3%)) | 1/1 = 100.0% (Wilson 95% CI 20.7–100.0%) |
| supportive + confidence≥0.75 | 1 | 0 | missing/not_available (denominator=0) | 0 (missing/not_available (denominator=0)) | missing/not_available (denominator=0) |
| background + confidence≥0.75 | 1 | 0 | missing/not_available (denominator=0) | 0 (missing/not_available (denominator=0)) | missing/not_available (denominator=0) |

NLI-only (caveat hariç) aynı kesitler:

| Koşul | eligible_n | would_skip_n (nli-only) | safe_skip_precision | dangerous_false_support | collapse_rate |
|---|---:|---:|---|---|---|
| direct + confidence≥0.75 | 2 | 2 | 1/2 = 50.0% (Wilson 95% CI 9.5–90.5%) | 0 (0/2 = 0.0% (Wilson 95% CI 0.0–65.8%)) | 1/2 = 50.0% (Wilson 95% CI 9.5–90.5%) |
| direct + confidence≥0.70 | 8 | 8 | 2/8 = 25.0% (Wilson 95% CI 7.1–59.1%) | 0 (0/8 = 0.0% (Wilson 95% CI 0.0–32.4%)) | 6/8 = 75.0% (Wilson 95% CI 40.9–92.9%) |
| direct + confidence≥0.65 | 9 | 9 | 2/9 = 22.2% (Wilson 95% CI 6.3–54.7%) | 0 (0/9 = 0.0% (Wilson 95% CI 0.0–29.9%)) | 7/9 = 77.8% (Wilson 95% CI 45.3–93.7%) |
| supportive + confidence≥0.75 | 1 | 1 | 1/1 = 100.0% (Wilson 95% CI 20.7–100.0%) | 0 (0/1 = 0.0% (Wilson 95% CI 0.0–79.3%)) | 0/1 = 0.0% (Wilson 95% CI 0.0–79.3%) |
| background + confidence≥0.75 | 1 | 0 | missing/not_available (denominator=0) | 0 (missing/not_available (denominator=0)) | missing/not_available (denominator=0) |

Satır kimlikleri / etiket dağılımı (eligible küçük olduğu için):

- direct + confidence≥0.75: n=2 labels={'SUPPORTS': 2} ids=#1250,#1282
- direct + confidence≥0.70: n=8 labels={'SUPPORTS': 8} ids=#262,#793,#960,#1152,#1181,#1200,#1250,#1282
- direct + confidence≥0.65: n=9 labels={'SUPPORTS': 9} ids=#262,#793,#960,#1003,#1152,#1181,#1200,#1250,#1282
- supportive + confidence≥0.75: n=1 labels={'SUPPORTS': 1} ids=#1082
- background + confidence≥0.75: n=1 labels={'NOT_ENOUGH_INFO': 1} ids=#1079

Yorum (karar değil): Direct + 0.75'te skip adayı 2 kayıt (#1282, #1250); ikisi de caveat ile escalate. Eşik 0.70/0.65'e inince aday artar ama çoğu tartışmalı collapse — precision/collapse trade-off. Eşik değiştirilmedi.

Golden tier/conf:

| id | NLI | conf | Claude | tier | conf | caveat |
|---|---|---|---|---|---|---|
| #865 ★ | SUPPORTS | 0.746 | yanlış | supportive | 0.746 | False |
| #905 ★ | SUPPORTS | 0.687 | tartışmalı | background | 0.687 | False |
| #961 ★ | SUPPORTS | 0.679 | tartışmalı | supportive | 0.679 | True |
| #1282 ★ | SUPPORTS | 0.808 | tartışmalı | direct | 0.808 | True |
| #829 | SUPPORTS | 0.441 | yanlış | supportive | 0.441 | False |
| #835 | SUPPORTS | 0.535 | yanlış | supportive | 0.535 | True |
| #891 | SUPPORTS | 0.358 | yanlış | supportive | 0.358 | False |
| #913 | SUPPORTS | 0.421 | yanlış | background | 0.421 | True |
| #955 | SUPPORTS | 0.37 | yanlış | background | 0.370 | True |
| #1044 | SUPPORTS | 0.433 | yanlış | background | 0.433 | False |
| #1096 | SUPPORTS | 0.381 | yanlış | supportive | 0.381 | False |
| #1222 | SUPPORTS | 0.477 | yanlış | background | 0.477 | True |
| #1258 | SUPPORTS | 0.453 | yanlış | background | 0.453 | True |
| #1274 | SUPPORTS | 0.39 | yanlış | background | 0.390 | True |
| #1294 | REFUTES | 0.372 | doğrulanmış | background | 0.372 | False |
| #1296 | SUPPORTS | 0.439 | yanlış | background | 0.439 | False |

---

## Ölçüm 3 — Evidence relevance (cosine)

Embedder: `paraphrase-multilingual-MiniLM-L12-v2` (zaten yüklü). Etiket: **proxy relevance (exact cited evidence not tracked) karışık: cited_package_item=272/551, proxy=279/551**.

| Grup | n | medyan cosine |
|---|---:|---:|
| false_skip (would_skip current ∧ ¬safe_skip) | 0 | missing/not_available |
| safe_skip (would_skip current ∧ safe) | 0 | missing/not_available |
| nli_threshold_pass ∧ ¬safe (caveat-öncesi false) | 1 | 0.751 |
| nli_threshold_pass ∧ safe | 2 | 0.630 |
| relevance hesaplanamayan | 0 | missing/not_available |

nli_threshold_pass false medyan=0.751, safe medyan=0.630. Ayrım net değil; R önerilmedi.

Golden relevance:

| id | NLI | conf | Claude | basis | relevance | kanıt |
|---|---|---|---|---|---|---|
| #865 ★ | SUPPORTS | 0.746 | yanlış | proxy_relevance_exact_cited_not_tracked | 0.267 | A Brain-Controlled and User-Centered Intelligent Wheelchair: A Feasibility Study. |
| #905 ★ | SUPPORTS | 0.687 | tartışmalı | cited_package_item | 0.491 | Diuretic effect and mechanism of action of parsley. |
| #961 ★ | SUPPORTS | 0.679 | tartışmalı | cited_package_item | 0.639 | Physiological processes induced by different types of physical activity that either oppose or enhance postprandial glucose tolerance. |
| #1282 ★ | SUPPORTS | 0.808 | tartışmalı | cited_package_item | 0.751 | Quantification of Chlorogenic Acid and Vanillin from Coffee Peel Extract and its Effect on α-Amylase Activity, Immunoregulation, Mitochondrial Oxidative Stress, and Tumor Suppressor Gene Expression Levels in H2O2-Induced Human Mesenchymal Stem Cells. |
| #829 | SUPPORTS | 0.441 | yanlış | proxy_relevance_exact_cited_not_tracked | 0.597 | Pharmacotherapy for sleep during critical illness and beyond. |
| #835 | SUPPORTS | 0.535 | yanlış | cited_package_item | 0.295 | Anticancer activity of Nigella sativa (black seed) and its relationship with the thermal processing and quinone composition of the seed. |
| #891 | SUPPORTS | 0.358 | yanlış | proxy_relevance_exact_cited_not_tracked | 0.574 | Effect on aging on plasma renin and aldosterone in normal man. |
| #913 | SUPPORTS | 0.421 | yanlış | proxy_relevance_exact_cited_not_tracked | 0.443 | Increased CSF drainage by non-invasive manipulation of cervical lymphatics. |
| #955 | SUPPORTS | 0.37 | yanlış | proxy_relevance_exact_cited_not_tracked | 0.415 | Gastroparesis. |
| #1044 | SUPPORTS | 0.433 | yanlış | proxy_relevance_exact_cited_not_tracked | 0.549 | Keratin: dissolution, extraction and biomedical application. |
| #1096 | SUPPORTS | 0.381 | yanlış | cited_package_item | 0.399 | Cellular transport of lutein is greater from uncooked rather than cooked spinach irrespective of whether it is fresh, frozen, or canned. |
| #1222 | SUPPORTS | 0.477 | yanlış | proxy_relevance_exact_cited_not_tracked | 0.221 | Effectiveness of physical therapy for lower limb lymphedema in gynecological cancer survivors: a systematic review of randomized controlled trials. |
| #1258 | SUPPORTS | 0.453 | yanlış | cited_package_item | 0.626 | Cardiometabolic Impact of Encapsulated Cocoa Powder and Pure Cocoa Ingredients Supplementation: A Comparative Placebo-Controlled RCT in Adults. |
| #1274 | SUPPORTS | 0.39 | yanlış | cited_package_item | 0.452 | Does the muscle protein synthetic response to exercise and amino acid-based nutrition diminish with advancing age? A systematic review. |
| #1294 | REFUTES | 0.372 | doğrulanmış | cited_package_item | 0.467 | Common questions and misconceptions about protein supplementation: what does the scientific evidence really show? |
| #1296 | SUPPORTS | 0.439 | yanlış | cited_package_item | 0.549 | Unravelling Sarcopenia in Chronic Kidney Disease: From Pathogenesis to Diagnosis and Therapeutics. |

---

## Ölçüm 4 — Claim-strength / abartı (iki ayrı flag)

Causal kelimeler otomatik abartı **sayılmadı**.

- strong_language=True: **16/551**
- causal_language=True: **27/551** (ayrı)
- her iki flag: **0**

| Grup | eligible_n | would_skip current | dangerous_false_support_rate | collapse_rate |
|---|---:|---:|---|---|
| strong_language=True | 16 | 0 | missing/not_available (denominator=0) | missing/not_available (denominator=0) |
| strong_language=False | 535 | 0 | missing/not_available (denominator=0) | missing/not_available (denominator=0) |
| causal_language=True | 27 | 0 | missing/not_available (denominator=0) | missing/not_available (denominator=0) |
| causal_language=False | 524 | 0 | missing/not_available (denominator=0) | missing/not_available (denominator=0) |

NLI-only threshold (caveat hariç) — enrichment hangi flag'den geliyor:

| Grup | eligible_n | nli_pass | dangerous_false_support_rate | collapse_rate |
|---|---:|---:|---|---|
| strong_language=True | 16 | 0 | missing/not_available (denominator=0) | missing/not_available (denominator=0) |
| strong_language=False | 535 | 3 | 0/3 = 0.0% (Wilson 95% CI 0.0–56.2%) | 1/3 = 33.3% (Wilson 95% CI 6.1–79.2%) |
| causal_language=True | 27 | 0 | missing/not_available (denominator=0) | missing/not_available (denominator=0) |
| causal_language=False | 524 | 3 | 0/3 = 0.0% (Wilson 95% CI 0.0–56.2%) | 1/3 = 33.3% (Wilson 95% CI 6.1–79.2%) |

Current-threshold skip neredeyse boş olduğu için rate karşılaştırması missing/not_available / anlamsız kalabilir. Enrichment nli-only kesitinde ayrı ayrı okunmalı; causal≠strong.

Golden strength flags:

| id | NLI | conf | Claude | strong | strong_hits | causal | causal_hits |
|---|---|---|---|---|---|---|---|
| #865 ★ | SUPPORTS | 0.746 | yanlış | True | tamamen | False | — |
| #905 ★ | SUPPORTS | 0.687 | tartışmalı | False | — | False | — |
| #961 ★ | SUPPORTS | 0.679 | tartışmalı | False | — | False | — |
| #1282 ★ | SUPPORTS | 0.808 | tartışmalı | False | — | False | — |
| #829 | SUPPORTS | 0.441 | yanlış | False | — | False | — |
| #835 | SUPPORTS | 0.535 | yanlış | False | — | False | — |
| #891 | SUPPORTS | 0.358 | yanlış | False | — | False | — |
| #913 | SUPPORTS | 0.421 | yanlış | False | — | False | — |
| #955 | SUPPORTS | 0.37 | yanlış | False | — | False | — |
| #1044 | SUPPORTS | 0.433 | yanlış | False | — | False | — |
| #1096 | SUPPORTS | 0.381 | yanlış | False | — | False | — |
| #1222 | SUPPORTS | 0.477 | yanlış | False | — | False | — |
| #1258 | SUPPORTS | 0.453 | yanlış | False | — | False | — |
| #1274 | SUPPORTS | 0.39 | yanlış | False | — | False | — |
| #1294 | REFUTES | 0.372 | doğrulanmış | False | — | False | — |
| #1296 | SUPPORTS | 0.439 | yanlış | False | — | False | — |

---

## Ölçüm 5 — Compound / atomicity

Şemada `compound_candidate` yok. Eşdeğer: `is_compound_claim(claim_text, reasoning)`.
compound_tier_mismatch flag: **2** kayıt.

| Grup | n | safe_skip_precision (current) | dangerous_false_support | mixed_collapse |
|---|---:|---|---:|---:|
| compound | 231 | missing/not_available (denominator=0) | 0 | 0 |
| atomic | 320 | missing/not_available (denominator=0) | 0 | 0 |

NLI-only:

| Grup | n | safe_skip_precision | dangerous_false_support | mixed_collapse |
|---|---:|---|---:|---:|
| compound | 231 | 0/1 = 0.0% (Wilson 95% CI 0.0–79.3%) | 0 | 1 |
| atomic | 320 | 2/2 = 100.0% (Wilson 95% CI 34.2–100.0%) | 0 | 0 |

compound_candidate kolonu yok; heuristic kullanıldı. Current-threshold skip boşsa mixed_collapse karşılaştırması nli-only kesitine bakılır.

Golden compound:

| id | NLI | conf | Claude | compound |
|---|---|---|---|---|
| #865 ★ | SUPPORTS | 0.746 | yanlış | True |
| #905 ★ | SUPPORTS | 0.687 | tartışmalı | True |
| #961 ★ | SUPPORTS | 0.679 | tartışmalı | True |
| #1282 ★ | SUPPORTS | 0.808 | tartışmalı | True |
| #829 | SUPPORTS | 0.441 | yanlış | False |
| #835 | SUPPORTS | 0.535 | yanlış | True |
| #891 | SUPPORTS | 0.358 | yanlış | False |
| #913 | SUPPORTS | 0.421 | yanlış | False |
| #955 | SUPPORTS | 0.37 | yanlış | False |
| #1044 | SUPPORTS | 0.433 | yanlış | False |
| #1096 | SUPPORTS | 0.381 | yanlış | False |
| #1222 | SUPPORTS | 0.477 | yanlış | False |
| #1258 | SUPPORTS | 0.453 | yanlış | False |
| #1274 | SUPPORTS | 0.39 | yanlış | False |
| #1294 | REFUTES | 0.372 | doğrulanmış | True |
| #1296 | SUPPORTS | 0.439 | yanlış | False |

---

## Ölçüm 6 — Snippet vs tam metin (yerel NLI ×2)

Stratified/biased örneklem — skip_rate production prevalence DEĞİL. Yalnızca snippet vs full-text ablasyonu.

Örneklem n=39 (en yüksek 10 SUPPORTS + 10 REFUTES + 10 direct + 10 mixed/uncertain + golden; overlap birleşti). **skip_rate production prevalence değil.**

IDs: [262, 275, 793, 829, 835, 854, 858, 865, 891, 905, 913, 955, 960, 961, 966, 987, 990, 1000, 1003, 1044, 1047, 1060, 1077, 1082, 1096, 1152, 1171, 1181, 1200, 1222, 1229, 1250, 1258, 1274, 1282, 1285, 1291, 1294, 1296]

| Kanıt | n_ran | n_missing | would_skip current | safe_skip_precision | dangerous_false_support | conf p50 |
|---|---:|---:|---:|---|---|---:|
| snippet | 39 | 0 | 4 (4/39 = 10.3% (Wilson 95% CI 4.1–23.6%)) | 1/4 = 25.0% (Wilson 95% CI 4.6–69.9%) | 0 | 0.5 |
| full | 39 | 0 | 4 (4/39 = 10.3% (Wilson 95% CI 4.1–23.6%)) | 1/4 = 25.0% (Wilson 95% CI 4.6–69.9%) | 0 | 0.498 |

NLI-only (caveat hariç) aynı ablasyon:

| Kanıt | would_skip_nli | safe_skip_precision | dangerous_false_support | collapse | conf mean |
|---|---:|---|---|---|---:|
| snippet | 5 (5/39 = 12.8% (Wilson 95% CI 5.6–26.7%)) | 1/5 = 20.0% (Wilson 95% CI 3.6–62.4%) | 0 | 4/5 = 80.0% (Wilson 95% CI 37.6–96.4%) | 0.554 |
| full | 5 (5/39 = 12.8% (Wilson 95% CI 5.6–26.7%)) | 1/5 = 20.0% (Wilson 95% CI 3.6–62.4%) | 0 | 4/5 = 80.0% (Wilson 95% CI 37.6–96.4%) | 0.547 |

Snippet vs full current-threshold would_skip 4 vs 4; precision 1/4 = 25.0% (Wilson 95% CI 4.6–69.9%) vs 1/4 = 25.0% (Wilson 95% CI 4.6–69.9%). Bu fark prevalence değil; aynı biased örneklemde kanıt kesiti etkisi. #1282 tek parça (cited item) rerun'da caveat=False (snippet False, full False) — production caveat parça 2'dedir; best-snippet-of-top-item kapısı #1282'yi NLI-only'e sokardı. #905 snippet SUPPORTS@0.946 caveat=False: Claude tartışmalı iken tek-parça NLI yüksek güvenle skip ederdi. Eşik/kural değiştirilmedi.

Golden M6 (snippet vs full):

| id | stored | snippet | full |
|---|---|---|---|
| #865 | SUPPORTS@0.746 | SUPPORTS@0.68 caveat=False | SUPPORTS@0.745 caveat=False |
| #905 | SUPPORTS@0.687 | SUPPORTS@0.946 caveat=False | SUPPORTS@0.948 caveat=False |
| #961 | SUPPORTS@0.679 | SUPPORTS@0.605 caveat=False | SUPPORTS@0.563 caveat=False |
| #1282 | SUPPORTS@0.808 | SUPPORTS@0.941 caveat=False | SUPPORTS@0.797 caveat=False |

---

## Ne değişmedi

- NLI eşiği 0.75 aynı
- Yeni gate/model önerisi **uygulanmadı**
- Bu rapor bir sonraki kararın girdisi; kendisi karar değil

## Kaynaklar

- DB `claims`+`verdicts`
- `data/pending_batches.json` retrieval paketleri
- `data/factcheck_debug.jsonl` cite_source
- Dilim ID: `data/ops_reports/2026-08-18-slice100*-ids.txt`, `2026-08-19-slice154e-ids.txt`
