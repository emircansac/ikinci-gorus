# Direct/package_only parse fail — kök neden analizi

Tarih: 2026-08-17. Artifact: `parse_compare_pre.json`, `parse_recheck.log`.

## 4 satırlık karşılaştırma tablosu (gerçek log)

| alan | 357 ✓ | 1168 ✓ | 810 ✗ | 1265 ✗ |
|------|-------|--------|---------|----------|
| **input_tokens** | 7 622 | 2 919 | 11 200 | 105 778 |
| **output_tokens** | 584 | 440 | 515 | 1 719 |
| **max_tokens** | 2 000 | 2 000 | 2 000 | 2 000 |
| **stop_reason** | `end_turn` | `end_turn` | `end_turn` | `end_turn` (sync log) |
| **evidence_count** | 5 | 5 | 5 | 5 |
| **evidence_chars** | 15 875 | 7 121 | 21 754 | 31 894 |
| **JSON parse error** | — | — | `Expecting ',' delimiter: line 4 column 602` | `no JSON object in response` |
| **raw output SON 200 char** | `…"source_tier": "systematic_review"\n}\`\`\`` | `…"source_tier": "primary_study"\n}\`\`\`` | `…"source_tier": "primary_study"\n}\`\`\`` | `…tek bir hastanın 8 haftalık takibini iç` (düz metin, `{` yok) |

Kaynak: batch API mesajları (#357, #1168, #810 — `msgbatch_01LcAj6W8GcfiuiLmE3y7Ru1`); #1265 jP5 sync log (`factcheck_20.log` satır 82–83).

## Kök neden kategorileri

| ID | `parse_failure_category` | Açıklama |
|----|--------------------------|----------|
| **810** | **`invalid_json`** | JSON gövdesi `end_turn` ile tamamlanmış görünse de `reasoning` içinde kaçışsız `"gıdadaki"` tırnağı → `json.loads` satır 4'te patlar. **Truncation değil** (`stop_reason ≠ max_tokens`, output 515 ≪ 2000). |
| **1265** | **`invalid_json`** | Model düz Türkçe paragraf döndürmüş; hiç `{` yok. **Truncation değil** (output 1 719, stop_reason end_turn). |

**Not:** #1265 orijinal jP5 turunda **`force_package_only=False`** (web_search açık, input 105k). #810 ise **`force_package_only=True`** (direct tier). İkisi de parse fail yaşadı ama mod tam aynı değil — ortak neden «JSON yerine/ içinde geçersiz metin», max_tokens değil.

Başarılı direct örnekler (#357, #1168): aynı `max_tokens=2000`, `stop_reason=end_turn`, ```json fence ile geçerli JSON ( `_extract_json` fence'i strip ediyor).

## max_tokens kararı

Truncation **doğrulanmadı** → `max_tokens` körlemesine artırılmadı. #810'un paketi en büyük (21 754 char) ama fail nedeni token limiti değil, JSON syntax.

## Uygulanan düzeltmeler

1. **`classify_parse_failure()`** — debug log'a kategori: `truncated | invalid_json | schema_validation | missing_field | wrong_enum | unknown`
2. **JSON-only retry** — ilk parse fail'de aynı paket, `temperature=0`, `[JSON RETRY]` suffix; `force_package_only` modunda ek web_search yok
3. **Batch retrieve** — batch mesajı parse fail olursa senkron retry devreye girer
4. **`factcheck_debug.jsonl`** — yeni alanlar: `parse_failure_category`, `parse_error`, `stop_reason`, `max_tokens`, `raw_output_last_200`, `parse_retry*`

## Recheck sonuçları (810, 1265, 357 — düzeltme sonrası)

```
./venv/bin/python pipeline/03_factcheck.py --recheck-ids 810,1265,357 --skip-nli
```

| ID | Sonuç | stop_reason | parse_retry gerekli mi? |
|----|-------|-------------|-------------------------|
| **810** | `doğrulanmış` @0.72, retrieval_cited | `end_turn` | Hayır — ilk çağrıda parse OK |
| **1265** | `belirsiz` @0.20, retrieval_cited | `end_turn` | Hayır — ilk çağrıda parse OK |
| **357** | `tartışmalı` @0.40, retrieval_cited (regresyon yok) | `end_turn` | Hayır |

Üçünde de `parse_failed=False`, `stop_reason=end_turn`. Retry mekanizması unit test'te doğrulandı (`test_escalate_parse_retry_on_invalid_json`); bu recheck'te tetiklenmedi (model geçerli JSON üretti).

## Sonuç

- Parse fail kök nedeni: **model çıktı disiplini** (invalid JSON / prose), **max_tokens truncation değil**.
- #810: kaçışsız iç tırnak; retry + sıkı JSON talimatı hedeflenen fix.
- #1265: JSON yerine düz metin; aynı retry yolu web_search modunda da geçerli (maliyet daha yüksek).
