"""
DEMO / KANIT ÇALIŞTIRMASI
=========================
API anahtarları olmadan (bu sandbox'ta YOUTUBE_API_KEY / ANTHROPIC_API_KEY yok)
Aşama 1-2-3'ün ürettiği türden veriyi elle besleyip, Aşama 4'ün (skorlama +
şüpheli listesi) gerçekten çalıştığını uçtan uca gösterir.

Veriler: konuşmada birlikte analiz ettiğimiz iki gerçek transkript
  - DEMO_CH_A: "mastürbasyon faydaları" videosu (Harvard çalışmasına dayanan, nispeten sorumlu)
  - DEMO_CH_B: "perine noktası / 30 saniye teknik" videosu (huni + kanıtsız teknik + abartılı genelleme)

Gerçek kullanımda bu veriler pipeline/01-02-03 tarafından otomatik üretilir.
Siz kendi ANTHROPIC_API_KEY / YOUTUBE_API_KEY'nizi girip run_pipeline.py'yi
çalıştırdığınızda bu dosyaya hiç ihtiyacınız kalmaz.
"""
import sys
from pathlib import Path
sys.path.append(str(Path(__file__).parent))
from utils.db import get_conn, init_db

init_db()
conn = get_conn()

# --- Kanallar -----------------------------------------------------------
conn.execute("""INSERT OR REPLACE INTO channels
    (channel_id, name, description, subscribers, total_videos, total_views, last_checked_at)
    VALUES ('DEMO_CH_A', 'Örnek Kanal A (60+ Erkek Sağlığı)', 'Genel sağlık içerikleri', 8180, 55, 860848, datetime('now'))""")
conn.execute("""INSERT OR REPLACE INTO channels
    (channel_id, name, description, subscribers, total_videos, total_views, last_checked_at)
    VALUES ('DEMO_CH_B', 'Dr. Zeynep Erdem (Örnek Kanal B)', '60 yaş sonrası sağlık, doktor kontrolü yerine geçmez uyarısı yok', 39400, 34, 1700341, datetime('now'))""")
conn.execute("""INSERT OR REPLACE INTO channels
    (channel_id, name, description, subscribers, total_videos, total_views, last_checked_at)
    VALUES ('DEMO_CH_C', 'Örnek Kanal C (Aynı İddiayı Tekrarlıyor)', 'Benzer perine tekniği içeriği', 12000, 10, 300000, datetime('now'))""")

# Büyüme anomalisi göstermek için iki snapshot (B kanalı %25 sıçrama yapmış gibi)
conn.execute("INSERT INTO channel_snapshots (channel_id, subscribers, total_videos, total_views, checked_at) VALUES ('DEMO_CH_B', 31500, 30, 1200000, datetime('now','-7 days'))")
conn.execute("INSERT INTO channel_snapshots (channel_id, subscribers, total_videos, total_views, checked_at) VALUES ('DEMO_CH_B', 39400, 34, 1700341, datetime('now'))")
conn.execute("INSERT INTO channel_snapshots (channel_id, subscribers, total_videos, total_views, checked_at) VALUES ('DEMO_CH_A', 8000, 54, 850000, datetime('now','-7 days'))")
conn.execute("INSERT INTO channel_snapshots (channel_id, subscribers, total_videos, total_views, checked_at) VALUES ('DEMO_CH_A', 8180, 55, 860848, datetime('now'))")

# --- Videolar (transkript kısaltılmış, tam metin konuşmada mevcuttu) ----
conn.execute("""INSERT OR REPLACE INTO videos (video_id, channel_id, title, published_at, transcript, transcript_lang)
    VALUES ('DEMO_V1', 'DEMO_CH_A', '60 Yaş Üstü Erkeklerde Mastürbasyonun 10 Faydası',
            '2026-07-01', 'Mastürbasyon prostat sağlığı kalp stres uyku libido ... (tam transkript pipeline''da saklanır)', 'tr')""")
conn.execute("""INSERT OR REPLACE INTO videos (video_id, channel_id, title, published_at, transcript, transcript_lang)
    VALUES ('DEMO_V2', 'DEMO_CH_B', 'Doktorların Söylemediği Perine Noktası',
            '2026-07-10', 'Perine noktasi pudendal sinir 30 saniye teknik ücretsiz rehber sabit yorum ... (tam transkript pipeline''da saklanır)', 'tr')""")
conn.execute("""INSERT OR REPLACE INTO videos (video_id, channel_id, title, published_at, transcript, transcript_lang)
    VALUES ('DEMO_V3', 'DEMO_CH_C', 'Bu Gizli Nokta Sertleşme Sorununu Çözüyor',
            '2026-07-20', 'Benzer perine noktası tekniği anlatımı ... (tam transkript pipeline''da saklanır)', 'tr')""")

conn.commit()

# --- İddialar (Aşama 2 çıktısı — az önce elle çıkardığımızın aynısı) ----
claims_A = [
    (120, "Daha sık boşalma, prostat kanseri riskinin daha düşük olmasıyla ilişkilidir", "mekanizma", "medium"),
    (211, "Orgazm sonrası kortizol düşüşü stres/gerginliği azaltır", "mekanizma", "low"),
    (525, "40-50 yaş üstü veya ailede prostat kanseri öyküsü olanlar için düzenli doktor kontrolü/PSA testi önemlidir", "önleme", "low"),
]
claims_B = [
    (100, "Doktorların bahsetmediği özel bir nokta 60+ erkeklerde sertleşme sorununun asıl kaynağıdır", "diğer", "medium"),
    (490, "Perine bölgesindeki pudendal sinir sıkışması çoğu 60+ erkekteki sertleşme sorununun kaynağıdır", "tanı", "high"),
    (604, "Perineye 2 parmakla 30 saniye baskı uygulamak sinir sıkışmasını çözer ve sertleşme yanıtını iyileştirir", "tedavi", "high"),
    (1014, "Sabit yorumdaki ücretsiz rehber bu tekniği tamamlar", "mucize-ürün", "high"),
]
# DEMO_CH_C: DEMO_CH_B'deki (tanı) iddiasının farklı kelimelerle tekrarı — çapraz-kanal
# kümeleme (narrative_clusters.csv) bunu YAKALAMALI çünkü aynı yanlış anlatı iki
# farklı kanalda dolaşıyor.
claims_C = [
    (150, "Perine noktasındaki sinir baskısı 60 yaş üstü erkeklerin çoğunda sertleşme sorununa yol açan asıl nedendir", "tanı", "high"),
]

for ts, text, cat, risk in claims_A:
    conn.execute("INSERT INTO claims (video_id, channel_id, timestamp_sec, claim_text, category, initial_risk) VALUES ('DEMO_V1','DEMO_CH_A',?,?,?,?)", (ts, text, cat, risk))
for ts, text, cat, risk in claims_B:
    conn.execute("INSERT INTO claims (video_id, channel_id, timestamp_sec, claim_text, category, initial_risk) VALUES ('DEMO_V2','DEMO_CH_B',?,?,?,?)", (ts, text, cat, risk))
for ts, text, cat, risk in claims_C:
    conn.execute("INSERT INTO claims (video_id, channel_id, timestamp_sec, claim_text, category, initial_risk) VALUES ('DEMO_V3','DEMO_CH_C',?,?,?,?)", (ts, text, cat, risk))
conn.commit()

# --- Verdicts (Aşama 3 çıktısı — az önce web_search ile doğruladığımızın aynısı) ----
verdicts = [
    # (claim_text parçası ile eşleştirip claim_id buluyoruz)
    ("prostat kanseri riskinin daha düşük", "SUPPORTS", 0.81, 1, "doğrulanmış", 0.8, "https://hsph.harvard.edu/news/why-more-sex-may-lower-prostate-cancer-risk/", 1),
    ("kortizol düşüşü", "NOT_ENOUGH_INFO", 0.55, 1, "tartışmalı", 0.5, "", 1),
    ("PSA testi önemlidir", "SUPPORTS", 0.9, 0, "doğrulanmış", 0.9, "", 1),
    ("asıl kaynağıdır", "NOT_ENOUGH_INFO", 0.4, 1, "tartışmalı", 0.5, "", 0),
    ("çoğu 60+ erkekteki sertleşme sorununun kaynağıdır", "REFUTES", 0.77, 1, "yanlış", 0.85, "https://www.ncbi.nlm.nih.gov/books/NBK544272/", 0),
    ("sinir sıkışmasını çözer", "REFUTES", 0.7, 1, "yanlış", 0.75, "https://www.ncbi.nlm.nih.gov/pmc/articles/PMC529451/", 0),
    ("tekniği tamamlar", "NOT_ENOUGH_INFO", 0.3, 1, "tartışmalı", 0.6, "", 0),
    ("asıl nedendir", "REFUTES", 0.72, 1, "yanlış", 0.8, "https://www.ncbi.nlm.nih.gov/books/NBK544272/", 0),
]

for text_frag, nli_label, nli_conf, esc, verdict, conf, url, human in verdicts:
    row = conn.execute("SELECT claim_id FROM claims WHERE claim_text LIKE ?", (f"%{text_frag}%",)).fetchone()
    if row:
        conn.execute("""INSERT OR REPLACE INTO verdicts
            (claim_id, nli_label, nli_confidence, escalated, final_verdict, confidence, source_url, human_reviewed)
            VALUES (?,?,?,?,?,?,?,?)""", (row["claim_id"], nli_label, nli_conf, esc, verdict, conf, url, human))
conn.commit()
conn.execute("UPDATE videos SET claims_extracted_at = datetime('now') WHERE video_id IN ('DEMO_V1','DEMO_V2','DEMO_V3')")
conn.commit()
conn.close()

print("✅ Demo verisi yüklendi. Şimdi Aşama 4 (skorlama) çalıştırılıyor...\n")

import subprocess
subprocess.run([sys.executable, "pipeline/04_score_suspects.py", "--export", "data/suspects.csv"])

print("\n✅ Şimdi Aşama 6 (iddia indeksi + şüphe skoru) çalıştırılıyor...\n")
subprocess.run([sys.executable, "pipeline/06_claim_index.py", "--export-dir", "data/"])
