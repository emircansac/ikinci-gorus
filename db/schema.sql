-- Sağlık Yanlış Bilgisi İzleme Sistemi — Veritabanı Şeması
-- SQLite. Her aşama kendi tablolarını doldurur, hiçbir aşama öncekini üzerine yazmaz.

CREATE TABLE IF NOT EXISTS channels (
    channel_id      TEXT PRIMARY KEY,      -- YouTube channel ID (UCxxxx)
    name            TEXT,
    username        TEXT,
    channel_url     TEXT,
    description     TEXT,
    joined_date     TEXT,
    location        TEXT,
    subscribers     INTEGER,
    total_videos    INTEGER,
    total_views     INTEGER,
    first_seen_at   TEXT DEFAULT (datetime('now')),
    last_checked_at TEXT
);

-- Zaman serisi: her tarama günü kanalın anlık durumu (büyüme hızı için gerekli)
CREATE TABLE IF NOT EXISTS channel_snapshots (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    channel_id      TEXT NOT NULL,
    checked_at      TEXT DEFAULT (datetime('now')),
    subscribers     INTEGER,
    total_videos    INTEGER,
    total_views     INTEGER,
    FOREIGN KEY (channel_id) REFERENCES channels(channel_id)
);

CREATE TABLE IF NOT EXISTS videos (
    video_id            TEXT PRIMARY KEY,
    channel_id          TEXT NOT NULL,
    title               TEXT,
    published_at        TEXT,
    transcript          TEXT,
    transcript_lang     TEXT,
    fetched_at          TEXT DEFAULT (datetime('now')),
    watch_source        TEXT DEFAULT 'channel',  -- 'channel' (abonelik) | 'direct' (tek video)
    claims_extracted_at TEXT,   -- NULL = Aşama 2 henüz çalışmadı. 0 iddia bulunsa bile burası set edilir,
                                -- yoksa 02_extract_claims.py her çalıştırmada aynı videoyu tekrar işler.
    active_extraction_version TEXT DEFAULT 'v1',  -- claims tablosundaki aktif extraction_version
    FOREIGN KEY (channel_id) REFERENCES channels(channel_id)
);

-- AŞAMA 2 çıktısı
CREATE TABLE IF NOT EXISTS claims (
    claim_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id        TEXT NOT NULL,
    channel_id      TEXT NOT NULL,
    timestamp_sec   INTEGER,               -- videodaki saniye
    claim_text      TEXT NOT NULL,         -- atomik, kontrol edilebilir önerme
    search_query_en TEXT,                  -- PubMed için İngilizce arama sorgusu (çeviri değil, anahtar kelime)
    category        TEXT,                  -- tedavi|tanı|doz|önleme|mucize-ürün|mekanizma|diğer
    initial_risk    TEXT,                  -- LLM'in ilk kaba tahmini: low|medium|high
    extracted_at    TEXT DEFAULT (datetime('now')),
    extraction_version TEXT DEFAULT 'v1',   -- Aşama 2 prompt/pipeline sürümü
    archived_at     TEXT,                  -- NULL = ana dashboard akışında
    archive_reason  TEXT,                  -- manual | reject | auto_low_risk
    FOREIGN KEY (video_id) REFERENCES videos(video_id),
    FOREIGN KEY (channel_id) REFERENCES channels(channel_id)
);

-- AŞAMA 3 çıktısı
CREATE TABLE IF NOT EXISTS verdicts (
    claim_id            INTEGER PRIMARY KEY,
    nli_label           TEXT,       -- SUPPORTS|REFUTES|NOT_ENOUGH_INFO (ucuz ilk filtre)
    nli_confidence      REAL,
    nli_evidence_snippet TEXT,
    escalated           INTEGER DEFAULT 0,   -- LLM+arama+insan onayına gönderildi mi
    final_verdict       TEXT,       -- doğrulanmış|yanlış|tartışmalı|belirsiz
    confidence          REAL,
    source_url          TEXT,
    reasoning           TEXT,       -- LLM gerekçesi (kalibrasyon denetimi için saklanır)
    source_directness   TEXT,       -- direct|indirect|unrelated
    evidence_stance     TEXT,       -- supports|contradicts|mixed|insufficient
    source_tier         TEXT,       -- guideline|systematic_review|primary_study|nutrition_db|encyclopedia|other
    calibration_flags   TEXT,       -- virgülle: inverted_verdict, default_conf, tier_cap:encyclopedia, …
    human_reviewed      INTEGER DEFAULT 0,   -- yalnızca utils/review.py (gerçek insan onayı)
    auto_accepted       INTEGER DEFAULT 0,   -- otomasyon: incelemeye gerek yok kararı
    would_auto_accept_v1 INTEGER DEFAULT 0,  -- shadow: v1 bandı aday (davranışı etkilemez)
    would_auto_accept_reason TEXT,           -- shadow: v1 False ise ilk kırılan koşul
    would_require_human_verdict_gate INTEGER DEFAULT 0,     -- shadow: tartışmalı/belirsiz
    would_require_human_confidence_gate INTEGER DEFAULT 0,  -- shadow: confidence < 0.7
    would_require_human_compound_gate INTEGER DEFAULT 0,    -- shadow: compound_tier_mismatch kaçtı
    would_auto_accept_after_all_gates INTEGER DEFAULT 0,    -- shadow: tüm gate'ler kapalı + needs_human=0
    reviewer_note        TEXT,
    library_match       INTEGER DEFAULT 0,   -- verified_claim_library auto-eşleşme
    verified_at         TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (claim_id) REFERENCES claims(claim_id)
);

-- AŞAMA 5 (opsiyonel): Yorum özgünlük analizi
CREATE TABLE IF NOT EXISTS comments (
    comment_id          TEXT PRIMARY KEY,
    video_id            TEXT NOT NULL,
    channel_id          TEXT NOT NULL,          -- videonun sahibi kanal (yorumcunun değil)
    author_channel_id   TEXT,                    -- yorumu yazanın kanal ID'si (varsa)
    author_name         TEXT,
    text                TEXT,
    published_at        TEXT,
    like_count          INTEGER,
    bot_score           REAL,                    -- 0-100, hesaplanan bot ihtimali
    bot_flags           TEXT,                    -- virgülle ayrılmış: duplicate,burst,generic,new_account
    fetched_at          TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (video_id) REFERENCES videos(video_id),
    FOREIGN KEY (channel_id) REFERENCES channels(channel_id)
);

-- Yorumcu kanalının (varsa) genel bilgisi — tekrar tekrar API çağırmamak için önbellek
CREATE TABLE IF NOT EXISTS commenter_profiles (
    author_channel_id   TEXT PRIMARY KEY,
    channel_created_at  TEXT,
    public_video_count  INTEGER,
    checked_at          TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS channel_risk_scores (
    channel_id          TEXT PRIMARY KEY,
    total_claims        INTEGER,
    false_claims        INTEGER,
    high_risk_claims    INTEGER,
    funnel_flag         INTEGER DEFAULT 0,   -- satış hunisi/ücretli ürün yönlendirmesi var mı
    ai_persona_flag     INTEGER DEFAULT 0,   -- AI-üretim şüphesi (şablon metin, itiraf vb.)
    growth_anomaly_flag INTEGER DEFAULT 0,   -- anormal abone artışı
    bot_comment_ratio   REAL DEFAULT 0,      -- Aşama 5: şüpheli/bot yorum oranı (0-1)
    risk_score          REAL,                -- 0-100
    risk_tier           TEXT,                -- izlemede|incele|acil
    computed_at         TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (channel_id) REFERENCES channels(channel_id)
);

CREATE TABLE IF NOT EXISTS verified_claim_library (
    library_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    claim_text        TEXT NOT NULL,
    claim_text_norm   TEXT NOT NULL,
    embedding         BLOB,
    final_verdict     TEXT NOT NULL,
    confidence        REAL,
    source_url        TEXT,
    source_tier       TEXT,
    reasoning         TEXT,
    origin_claim_id   INTEGER,
    created_at        TEXT DEFAULT (datetime('now'))
);

-- Topic evidence cache (kanıt only — verdict taşımaz)
CREATE TABLE IF NOT EXISTS evidence_topic_cache (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    topic_key           TEXT NOT NULL,
    source_url          TEXT NOT NULL,
    title               TEXT,
    abstract            TEXT,
    source_tier         TEXT,
    retrieval_tier      TEXT,
    publication_types   TEXT,
    fetched_at          TEXT DEFAULT (datetime('now')),
    origin_claim_id     INTEGER,
    UNIQUE(topic_key, source_url)
);
CREATE INDEX IF NOT EXISTS idx_topic_cache_key ON evidence_topic_cache(topic_key);

-- İnsan incelemesi öğrenme kaydı (yalnızca approve/reject; arşivle yazılmaz)
CREATE TABLE IF NOT EXISTS review_outcomes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    claim_id INTEGER NOT NULL,
    reviewed_at TEXT DEFAULT (datetime('now')),
    ai_verdict TEXT,
    ai_confidence REAL,
    human_verdict TEXT,
    agreed INTEGER NOT NULL,
    calibration_flags_at_review TEXT,
    specificity_tier_at_review TEXT,
    reviewer_check_point_category TEXT,
    FOREIGN KEY (claim_id) REFERENCES claims(claim_id)
);
CREATE INDEX IF NOT EXISTS idx_review_outcomes_reviewed_at
    ON review_outcomes(reviewed_at);
CREATE INDEX IF NOT EXISTS idx_review_outcomes_category
    ON review_outcomes(reviewer_check_point_category);
CREATE INDEX IF NOT EXISTS idx_review_outcomes_agreed
    ON review_outcomes(agreed);
