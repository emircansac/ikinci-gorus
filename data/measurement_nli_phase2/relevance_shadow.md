# Shadow relevance — geriye dönük skor (eşik yok)

Kod: `compute_evidence_relevance` + Ölçüm 3 cited/proxy seçimi. Gate kurulmadı. should_escalate / needs_human / calibration_flags değişmedi.

- Kohort: 554 Dilim 1–5, eligible = escalated=1 → **n=551**
- Skor hesaplanan: **551**
- Hesaplanamayan: **0**
- basis: cited_package_item=272, proxy=279, missing=0

## Dağılım

| | değer |
|---|---:|
| n | 551 |
| p25 | 0.401 |
| p50 | 0.502 |
| p75 | 0.606 |
| min | 0.017 |
| max | 0.843 |

Eşik önerisi yok — kanal geneline geçilene kadar veri toplama.

## Golden case'ler

| id | NLI | conf | Claude | basis | relevance | kanıt |
|---|---|---|---|---|---:|---|
| #865 | SUPPORTS | 0.746 | yanlış | proxy_relevance_exact_cited_not_tracked | 0.267 | A Brain-Controlled and User-Centered Intelligent Wheelchair: A Feasibility Study. |
| #905 | SUPPORTS | 0.687 | tartışmalı | cited_package_item | 0.491 | Diuretic effect and mechanism of action of parsley. |
| #961 | SUPPORTS | 0.679 | tartışmalı | cited_package_item | 0.639 | Physiological processes induced by different types of physical activity that either oppose or enhance postprandial glucose tolerance. |
| #1282 | SUPPORTS | 0.808 | tartışmalı | cited_package_item | 0.751 | Quantification of Chlorogenic Acid and Vanillin from Coffee Peel Extract and its Effect on α-Amylase Activity, Immunoregulation, Mitochondrial Oxidative Stress, and Tumor Suppressor Gene Expression Levels in H2O2-Induced Human Mesenchymal Stem Cells. |

**#865 referans 0.267:** eşleşti (hesaplanan 0.267256, ref 0.267256)

Kaynak: DB `claims`+`verdicts`, `data/pending_batches.json` paketleri.
