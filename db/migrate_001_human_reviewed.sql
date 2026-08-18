-- migrate_001: human_reviewed / auto_accepted semantik ayrımı
-- utils/db.py _migrate_human_reviewed_semantics() tarafından otomatik uygulanır.

ALTER TABLE verdicts ADD COLUMN auto_accepted INTEGER DEFAULT 0;

-- Otomasyon kararı yanlışlıkla human_reviewed=1 yazılmış satırlar
UPDATE verdicts
SET human_reviewed = 0, auto_accepted = 1
WHERE human_reviewed = 1
  AND (reviewer_note IS NULL OR TRIM(reviewer_note) = '');

-- Gerçek insan onayları auto_accepted taşımaz
UPDATE verdicts
SET auto_accepted = 0
WHERE human_reviewed = 1
  AND reviewer_note IS NOT NULL
  AND TRIM(reviewer_note) != '';

-- Indirect kanıt: otomasyon bypass sayılmaz (claim 673 tipi)
UPDATE verdicts
SET auto_accepted = 0
WHERE source_directness = 'indirect'
  AND human_reviewed = 0
  AND (reviewer_note IS NULL OR TRIM(reviewer_note) = '');

-- Güvenlik bayraklı otomasyon: utils/db.py _reconcile_stale_auto_accepted()
-- (claim 709 tipi — drug_interaction kuralı öncesi fact-check edilmiş kayıtlar)

-- Origin artık human_reviewed=1 olmayan kütüphane kayıtlarını sil
DELETE FROM verified_claim_library
WHERE origin_claim_id IN (
    SELECT vcl.origin_claim_id
    FROM verified_claim_library vcl
    JOIN verdicts vr ON vr.claim_id = vcl.origin_claim_id
    WHERE vr.human_reviewed != 1
);
