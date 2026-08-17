# Shadow-mode auto_accept_candidate_v1 — measurement_50 (güncellenmiş formül)

## Formül değişikliği

**Kaldırıldı:** `specificity_tier==direct` + `cite_source==retrieval_cited` (direct tier ile `package_only_forced` yapısal çelişkisi).

**Yeni aday havuzu:** `escalated=0` — Claude'a gitmemiş, ucuz NLI filtresinin yüksek güvenle (`nli_confidence ≥ 0.75`) net `SUPPORTS`/`REFUTES` verdiği iddialar.

**Kapsam dışı (dokunulmadı):** `package_only_forced` veya `specificity_tier==direct` → `out_of_scope:*`

Diğer koşullar aynı: binary verdict, `initial_risk≠high`, yüksek risk kategorisi yok, hedge/partial bayrak yok, ilaç etkileşimi yok, `parse_failed` yok, `model_disagreement` yok, genel fallback `check_point`.

---

## measurement_50 sonuçları (50/50)

| Metrik | Değer |
|--------|-------|
| **`would_auto_accept_v1=True`** | **0/50** |
| **`would_auto_accept_v1=False`** | **50/50** |
| Örneklemde `escalated=0` | **0** (tüm tur `--skip-nli` + batch escalate) |

### False dağılımı

| İlk kırılan koşul | n |
|-------------------|---|
| `escalated:not_nli_only` | 47 |
| `out_of_scope:package_only_forced` | 3 (#357, #810, #1168 — direct/package_only yolu) |

**Yorum:** Bu 50'lik örneklem NLI-only aday havuzunu **içermiyor**; formülün 0/50 vermesi beklenen bir sonuç. Bandın çalıştığı kanıt DB'deki `escalated=0` satırlarda (aşağıda).

---

## DB'deki NLI-only satırlar (escalated=0, n=2)

| claim_id | would_accept | reason | auto_accepted (üretim) |
|----------|--------------|--------|------------------------|
| **716** | **True** | — | 1 |
| 673 | False | `check_point:not_generic_fallback` (source_directness=indirect) | 0 |

### #716 — True örneği

- `nli_label=SUPPORTS`, `nli_confidence=0.9`, `final_verdict=doğrulanmış`
- `escalated=0`, `calibration_flags` boş
- `model_disagreement=False`, genel fallback check_point

---

## Eksik alan notu (değişmedi)

`independent_source_count` ve `contradiction_detected` formüle dahil değil — olsaydı band daha katı olurdu.
