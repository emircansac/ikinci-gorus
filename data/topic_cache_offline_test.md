# Topic evidence cache — offline test

Cache satır sayısı: **5**

## Seed

- #362 topic_key=`pancar` source=pending_batches

## Test iddiaları

### #372 (QBlBHEf0jm4) — `pancar`

> Büyük üniversiteler MRI (memre) cihazlarıyla pancarın beyin kan akışına etkisini kanıtlamıştır.…

| Mod | n | cache | live | sufficient | tier | reason |
|-----|--:|------:|-----:|:----------:|------|--------|
| cache_only | 5 | 5 | 0 | True | supportive | ok |
| live_only | 5 | 0 | 5 | True | background | ok |
| hybrid | 5 | 0 | 5 | True | background | ok |
- hybrid path: `topic_cache+pubmed_mesh+europepmc`; cache_in_final=0; sufficiency_changed=False

### #889 (WtJzVPZroiY) — `gfr`

> 40 yaşından sonra glomerüler filtrasyon hızı (GFR) her yıl yaklaşık %1 oranında azalır.…

| Mod | n | cache | live | sufficient | tier | reason |
|-----|--:|------:|-----:|:----------:|------|--------|
| cache_only | 0 | 0 | 0 | False | none | no_evidence |
| live_only | 5 | 0 | 5 | True | background | ok |
| hybrid | 5 | 0 | 5 | True | background | ok |
- hybrid path: `pubmed+europepmc+medlineplus`; cache_in_final=0; sufficiency_changed=False

### #907 (WtJzVPZroiY) — `potasyum,salatalık`

> Salatalığın yüksek su ve potasyum içeriği fazla tuzu dengeler ve içindeki silika bağ dokusunu güçlen…

| Mod | n | cache | live | sufficient | tier | reason |
|-----|--:|------:|-----:|:----------:|------|--------|
| cache_only | 0 | 0 | 0 | False | none | no_evidence |
| live_only | 5 | 0 | 5 | True | background | ok |
| hybrid | 5 | 0 | 5 | True | background | ok |
- hybrid path: `europepmc+medlineplus`; cache_in_final=0; sufficiency_changed=False
