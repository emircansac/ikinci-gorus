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
