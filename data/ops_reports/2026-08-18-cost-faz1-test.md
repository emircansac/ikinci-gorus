# Faz 1 canlı test — before/after

**İddialar:** [752, 1284, 1243, 1247, 1248]

**Denetim:** [`2026-08-18-cost-audit-1284-1243.md`](2026-08-18-cost-audit-1284-1243.md)

| claim_id | before input (jP5) | after input | Δ input | before verdict | after verdict | after searches | max_budget | not |
|---|---:|---:|---:|---|---|---:|---:|---|
| 1243 | 112,864 | 1,019 | −99.1% | yanlış | yanlış | 1 | 1 | Serper paket 2201 char; gerekçe dolaylı |
| 1247 | 73,075 | 22,945 | −68.6% | belirsiz | belirsiz | 2 | 1 | |
| 1248 | 26,763 | 15,068 | −43.7% | tartışmalı | tartışmalı | 2 | 1 | |
| 752 | — | 23,019 | — | tartışmalı | tartışmalı | 3 | 1 | |
| 1284 | 27,122 | 7,179 | −73.5% | tartışmalı | **tartışmalı**† | 0 | 1 | †compound_tier_mismatch cap (recheck doğrulandı) |

† #1284: model yine binary `doğrulanmış` eğiliminde; bileşen tier farkı (Alzheimer supportive / Parkinson direct) nedeniyle sunucu cap ile `tartışmalı` korunuyor — **snippet değil, bileşik-iddia kuralı ihlali**.

## Cache token düzeltmesi (eski formül vs düzeltilmiş)

Aynı **after** kaydı: cache yok sayılıyordu → şimdi sayılıyor. Bu, Faz 1 before→after değil.

Sync fiyat: $2/M in + $10/M out + cache write $2.50/M + cache read $0.20/M (batch %50 yok).

| claim_id | eski after $ | düzeltilmiş after $ | formül % | cache_write | cache_read |
|---|---:|---:|---:|---:|---:|
| 1243 | 0.0091 | 0.0371 | +308.6% | 10549 | 8352 |
| 1284 | 0.0195 | 0.0231 | +18.6% | 1452 | 0 |

## Faz 1 before→after (aynı düzeltilmiş sync formül)

jP5 `factcheck_20.log` (cache basılmamış = 0, before alt sınır) vs debug after.

| claim_id | jP5 before $ | Faz 1 after $ | Δ | % |
|---|---:|---:|---:|---:|
| 1243 | 0.2432 | 0.0371 | −0.2061 | −84.7% |
| 1284 | 0.0623 | 0.0231 | −0.0392 | −62.9% |
| 1247 | 0.1598 | 0.0572 | −0.1026 | −64.2% |
| 1248 | 0.0612 | 0.0401 | −0.0210 | −34.4% |
