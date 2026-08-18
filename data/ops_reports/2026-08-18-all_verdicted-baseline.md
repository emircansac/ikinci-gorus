# Üretim izleme raporu — 2026-08-18

_Karşılaştırma yapılamadı, bu ilk rapor._

**Kapsam:** all_verdicted

- Toplam iddia (aktif): **244**
- Verdict almış: **244**
- Video sayısı: **21**

## Özet metrikler

| Metrik | Değer | Δ (önceki rapor) |
|--------|------:|------------------|
| Claim sayısı / video (ort.) | 11.6 | baseline |
| Dedup merge oranı (chunk+global / ham) | 25/241 (10.4%; 3 tam-pipeline video) | baseline |
| Escalation oranı | 98.8% | baseline |
| Web search oranı (escalated) | 35.7% | baseline |
| retrieval_cited oranı (escalated) | 12.4% | baseline |
| topic cache hit oranı (final paket) | — | baseline |
| specificity_tier dağılımı | direct 5, supportive 21, background 63, (yok) 155 | — |
| Parse fail + retry başarı | 0 fail; retry 1/1 (100.0%) | — |
| needs_human oranı | 81.6% | baseline |
| $/claim (tahmini) | $0.0393 (n=87, {'batch': 87}) | baseline |
| $/claim p50/p90/p95/max | p50 $0.0376 / p90 $0.0561 / p95 $0.0643 / max $0.0903 | — |
| web_search_call_count p50/p95/max | p50 2.0 / p95 2.8 / max 3.0 (n=5) | — |
| processed (verdict almış) | 244 | — |
| parse_failed | 0 | — |
| retrieval_failed | 0 | — |
| compound_tier_mismatch sayısı | 1 | — |
| would_auto_accept_after_all_gates | 15 | — |
| shadow gates (verdict/conf/compound) | 135 / 187 / 0 | — |
| embedding_clustering_status | ok (probe: sentence-transformers import; sidecar yok — 06_claim_index çalıştırın) | — |
| escalated=0 (NLI-only) sayısı | 3 | baseline |
| would_auto_accept_v1 | true 0, false 244 | — |
| source_tier dağılımı | guideline 19, primary_study 123, other 96, nutrition_db 2, encyclopedia 3, (boş) 1 | — |

## Video bazında

| video_id | full_pipeline | claim | dedup merge | verdict | escalated |
|----------|:-------------:|------:|-------------|--------:|----------:|
| 19wuSa9GlW0 | hayır | 13 | n/a | 13 | 13 |
| 3bZ0Ew2BiLI | hayır | 6 | n/a | 6 | 6 |
| A2p88jcmeJY | hayır | 4 | n/a | 4 | 4 |
| DEMO_V1 | hayır | 3 | n/a | 3 | 2 |
| DEMO_V2 | hayır | 4 | n/a | 4 | 4 |
| DEMO_V3 | hayır | 1 | n/a | 1 | 1 |
| Iw1akJ5SQgM | hayır | 5 | n/a | 5 | 5 |
| K7YdDLCZmW0 | hayır | 10 | n/a | 10 | 10 |
| LAR4Cm1od0U | hayır | 8 | n/a | 8 | 8 |
| P4m9F9mykQ8 | hayır | 16 | n/a | 16 | 16 |
| QBlBHEf0jm4 | hayır | 9 | n/a | 9 | 9 |
| S79wnve6Bgk | hayır | 10 | n/a | 10 | 10 |
| UnsXi4CBAjU | hayır | 6 | n/a | 6 | 6 |
| WtJzVPZroiY | hayır | 5 | n/a | 5 | 5 |
| bZsorXWeLhM | evet | 27 | 3/86 (3.5%) | 27 | 27 |
| eiB05V3HVes | hayır | 6 | n/a | 6 | 6 |
| jP5XF06OLbo | evet | 29 | 7/78 (9.0%) | 29 | 29 |
| n6vtphTbq9k | hayır | 7 | n/a | 7 | 7 |
| odZgEDFDmbE | evet | 62 | 15/77 (19.5%) | 62 | 61 |
| rHEf_LBFi3k | hayır | 7 | n/a | 7 | 6 |
| ut7LN1iLSGA | hayır | 6 | n/a | 6 | 6 |

## Notlar

- **Dedup merge:** Yalnızca `full_pipeline=evet` satırları gerçek ölçümdür (extraction_chunks veya smoke offline_dedup). `hayır` = measurement kohortundan kısmi örnekleme; dedup hücresi **n/a** (0% anlamına gelmez).
- **specificity_tier=(yok) 155 iddia** (63.5%): bu mekanizma eklenmeden önce fact-check edilmiş — kapsam metriği yalnızca bundan sonraki turlar için anlamlı.

## Kaynaklar

- DB: `data/monitor.db` (claims + verdicts)
- Debug: `data/factcheck_debug.jsonl`
- Batch usage: `data/pending_batches.json` (custom_id)
- Dedup: `data/extraction_chunks/` veya `data/smoke_*/offline_dedup.json`

Maliyet tahmini: Sonnet 5 $2/M in + $10/M out; batch %50; cache write $2.50/M (batch %50); cache read $0.20/M.
