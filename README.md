# Sağlık Yanlış Bilgisi İzleme Sistemi (Türkçe YouTube)

4 aşamalı pipeline: **Topla → İddia Çıkar → Fact-Check (hibrit) → Şüpheli Listele**

Bu sistem, incelediğimiz iki örnek videoda görülen paterni otomatik yakalamak için
tasarlandı: aynı türde kanalların bazıları meşru araştırmaya dayanan (ama abartılı
sunulan) içerik üretirken, bazıları nadir bir tıbbi durumu genelleştirip kanıtsız
"kendin tedavi et" teknikleri ve satış hunileriyle birleştiriyor. Sistem bu ikisini
**kanal bazında** ayırt edecek şekilde kuruldu.

## Kurulum

```bash
cd health_misinfo_monitor
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt      # torch/transformers ağırsa --skip-nli kullanabilirsiniz (aşağıda)
cp .env.example .env                  # sonra .env dosyasını kendi anahtarlarınızla doldurun
export $(cat .env | xargs)
```

Gerekli anahtarlar:
- **YOUTUBE_API_KEY**: [Google Cloud Console](https://console.cloud.google.com/apis/credentials) → YouTube Data API v3'ü etkinleştirin (ücretsiz, günlük 10.000 unit kota)
- **ANTHROPIC_API_KEY**: [console.anthropic.com](https://console.anthropic.com)

## Deploy etmeden önce: 3 kademeli güven kontrolü

Render'a (ya da başka bir yere) yüklemeden önce "acaba çalışır mı" belirsizliğini
üç kademede azaltın — her kademe öncekinden daha fazla gerçek şey test eder:

**1. Ön kontrol (saniyeler, ~0 maliyet)**
```bash
python pipeline/00_healthcheck.py
```
API anahtarlarınızın geçerli olup olmadığını, bağımlılıkların kurulu olup
olmadığını, veritabanı şemasının hatasız uygulanıp uygulanmadığını kontrol eder.
Tek bir gerçek maliyetli çağrı yapar (Claude'a 5 token'lık bir "merhaba" isteği).
Herhangi bir ✗ varsa deploy etmeden önce onu düzeltin — Render'da build
başarısız olduğunda fark etmek çok daha zor ve zaman alıcıdır.

**2. Küçük gerçek deneme (dakikalar, küçük maliyet)**
```bash
python run_pipeline.py --channels data/channels.csv --max-videos 2
```
Kanal listenizden sadece 1-2 kanalı, kanal başına 2 video ile deneyin.
Çıkan `data/claim_index.csv`'yi elle açıp okuyun: iddialar mantıklı mı,
kategori/risk etiketleri doğru mu, şüphe skorları savunulabilir mi? Bu adım
ön kontrolün YAKALAYAMAYACAĞI şeyleri (prompt kalitesi, gerçek transkript
formatı, gerçek PubMed sonuçları) ortaya çıkarır.

**3. Ancak bundan sonra deploy**
İkinci adımdaki çıktı mantıklı görünüyorsa, kanal listenizin tamamıyla bir
kez daha çalıştırıp (`--max-videos` sınırını kaldırarak) sonucu tekrar
gözden geçirin, sonra Render'a geçin.

## Kanal listenizi hazırlama

Mevcut Excel veritabanınızdan (`ID` sütunu channel_id'nizi içeriyor):

```bash
python3 -c "
import pandas as pd
df = pd.read_excel('Database_of_health-related_YouTube_channels__Turkish_.xlsx')
df[['ID']].rename(columns={'ID':'channel_id'}).to_csv('data/channels.csv', index=False)
"
```

## Çalıştırma

```bash
# Tam pipeline (ilk çalıştırma — 15 video/kanal, transkript dahil)
python run_pipeline.py --channels data/channels.csv --max-videos 15

# Sonraki periyodik taramalar (sadece yeni video/iddiaları işler, idempotent)
python run_pipeline.py --channels data/channels.csv --skip-collect   # sadece 2-4. aşama
python pipeline/01_collect.py --channels data/channels.csv --no-transcripts  # hızlı büyüme kontrolü
```

**Zamanlama**: Bunu bir cron job veya GitHub Actions scheduled workflow olarak
günde bir kez çalıştırın. RSS tabanlı hızlı kontrol için (`--no-transcripts`,
kota harcamaz) günde birkaç kez çalıştırabilirsiniz.

## Aşama 3'ün maliyet mimarisi (önemli)

```
İddia → [HF NLI ilk filtre, ücretsiz/yerel]
           │
           ├─ initial_risk=high?           → HER ZAMAN escalate
           ├─ NLI belirsiz/düşük güven?    → escalate
           └─ NLI net + yüksek güven       → ucuz sonucu kaydet, LLM'e gitme
                    │
                    ▼ (sadece escalate edilenler)
        Claude API + web_search → final_verdict + kaynak
```

Bu, binlerce iddiayı düşük maliyetle triyaj edip yalnızca belirsiz/yüksek riskli
olanlar için pahalı LLM+arama çağrısı yapmanızı sağlar. HF modelini kurmak
istemiyorsanız (`torch` ~2-3GB):

```bash
python pipeline/03_factcheck.py --skip-nli   # her iddiayı doğrudan Claude'a gönderir
```

Şüpheli iddiaları yeniden değerlendirmek (eski verdict silinir, reasoning loglanır):

```bash
python pipeline/03_factcheck.py --recheck-ids 96,110 --skip-nli
python pipeline/06_claim_index.py --export-dir data/
```

Ham `reasoning` `verdicts.reasoning` sütununa ve `data/factcheck_debug.jsonl` dosyasına yazılır
(şablon vs. gerçek gerekçe denetimi için). LLM JSON'u kaydedilmeden önce
`utils/factcheck_calibrate.py` kaynak kademesi / stance tutarlılığı ile kırpılır:
Wikipedia genel sayfasına yüksek güven bağlanamaz; kaynak iddiayı desteklerken
"yanlış" denemez; `tartışmalı` + tam 0.55 varsayılanı insan incelemesine düşer.

**Dil notu**: Varsayılan NLI modeli (`MoritzLaurer/mDeBERTa-v3-base-mnli-xnli`)
çok dilli olduğu için Türkçe iddiaları doğrudan işleyebilir. FEVER/PubHealth
tabanlı İngilizce modeller (ör. `Dzeniks/roberta-fact-check`) kullanmak isterseniz
önce iddiaları İngilizceye çevirmeniz gerekir.

## ⚠️ İnsan onayı — otomasyon karar vermez, öneri üretir

`verdicts.human_reviewed = 0` olan satırlar **kesinleşmiş değildir**. Özellikle:
- `category` = tedavi / doz / mucize-ürün
- `initial_risk` = high

olan iddialar mutlaka bir insan tarafından gözden geçirilmeli. `04_score_suspects.py`
çıktısındaki `pending_human_review` sütunu kaç iddianın hâlâ onay beklediğini
gösterir — bu sütun > 0 olan bir kanalı "acil" diye harekete geçirmeden önce
o onayı tamamlayın.

## Kanal risk skoru nasıl hesaplanıyor

| Bileşen | Ağırlık | Açıklama |
|---|---|---|
| Yanlış/tartışmalı iddia oranı | 40 | `final_verdict` doğrulanmış değilse |
| Yüksek riskli iddia oranı | 25 | `initial_risk = high` |
| Satış hunisi göstergesi | 15 | "ücretsiz rehber", "sabit yorum", link yönlendirmesi vb. anahtar kelime taraması |
| AI-persona itirafı | 10 | Açıklamada "yapay zeka sağlık eğitimcisi" gibi ifadeler |
| Anormal büyüme | 10 | İki snapshot arası abone artışı >%20 |

`risk_tier`: **acil** (≥60) / **incele** (≥30) / **izlemede** (<30)

## Kanıt: sistem gerçekten ayırt edebiliyor mu?

`demo_seed_and_run.py`, konuşmada birlikte analiz ettiğimiz iki gerçek videoyu
(Aşama 2-3 çıktılarıyla) besleyip Aşama 4'ü çalıştırır:

```bash
python demo_seed_and_run.py
```

Çıktı:

| Kanal | Skor | Katman |
|---|---|---|
| Örnek Kanal A (Harvard çalışmasına dayanan, doktor kontrolü tavsiyesi olan video) | **13.3** | izlemede |
| Örnek Kanal B (perine "tekniği" + huni + genelleme) | **83.8** | acil |

Sistem, sizinle birlikte elle yaptığımız değerlendirmeyle aynı sonuca ulaştı.

## Aşama 5 (opsiyonel): Yorum bot analizi

**Dürüst sınır**: YouTube API "bu kanala kim abone oldu" listesini vermez (gizlilik).
Yani "abonelerin kaçı bot" diye doğrudan ölçülemez — bunun tek dolaylı göstergesi
zaten mevcut `growth_anomaly_flag` (anormal abone artış hızı). Ama **yorumlar**
herkese açık, orada gerçek sinyaller var:

```bash
python pipeline/05_comment_authenticity.py --max-comments-per-video 100
python pipeline/04_score_suspects.py --export data/suspects.csv   # bot_comment_ratio'yu skora yansıtmak için tekrar çalıştırın
# ya da tek seferde:
python run_pipeline.py --channels data/channels.csv --with-comments
```

Kullanılan 4 sinyal (`utils/bot_detection.py`):

| Sinyal | Ne yakalar |
|---|---|
| **duplicate** | Aynı/çok benzer metin, farklı kullanıcılar tarafından atılmış (şablon/kampanya) |
| **burst** | Kısa zaman penceresinde, birbirine benzer yorumların yoğunlaşması (koordineli zamanlama) |
| **generic** | "çok faydalı bilgi teşekkürler doktor" tipi, videoya özgü hiçbir detay içermeyen kalıp övgü |
| **new_account** | Yorumcunun kanalı çok yeni açılmış + hiç public videosu yok |

**Önemli tasarım notu**: `burst` bayrağı SADECE zaten bir `duplicate` kümesinin
parçası olan yorumlara uygulanır — geliştirme sırasında ilk versiyonda salt
zamansal yoğunluğa bakıyordum ve tamamen organik, kendine özgü bir yorum sırf bir
bot dalgasının zamanına denk geldiği için yanlışlıkla işaretleniyordu (test
sırasında bizzat gözlemlendi, bkz. `utils/bot_detection.py` docstring'i).
Düzeltildi: artık içerik benzerliği + zamanlama BİRLİKTE aranıyor.

`bot_score` (0-100) **olasılık puanıdır, kesin hüküm değil** — organik bir video
da gerçekten çok sayıda benzer kısa yorum alabilir. %30+ şüpheli yorum oranına
sahip kanalları insan gözden geçirmesine düşürün, otomatik "bot kanal" etiketi
basmayın.

## Aşama 6: İddia indeksi + şüphe skoru (ikili değil, sürekli)

Aşama 3'ün ürettiği `claims`+`verdicts` verisi 3 tabloya dağılmış durumda — "hangi
iddialar üzerinde çalışmaya değer" sorusuna cevap veren tek bir indeks yoktu.
Bu aşama onu üretir, ve **doğrulanmış/yanlış** gibi ikili etiket yerine 0-100
arası **sürekli şüphe skoru** kullanır (`utils/suspicion.py`):

```
suspicion_score = 50 + confidence × verdict_yönü × 50

    0   = tamamen güvenilir (üzerinde çalışmaya gerek yok)
    50  = belirsiz (kanıt yok — yön taşımaz, bu "yarı yanlış" demek DEĞİLDİR)
    100 = yanlışa maksimum yakın (öncelik)
```

Düşük güvenli bir "yanlış" verdict (ör. confidence=0.3) skoru uca değil, merkeze
yakın tutar — sistem kendinden emin değilse skor da aşırı uçta olmamalı.

```bash
python pipeline/06_claim_index.py --export-dir data/
```

Dört çıktı:

1. **`claim_index.csv`** — her **aktif** iddia tek satır, şüphe skoruna göre sıralı.
   `suspicion_score` boş (`veri_eksik`) olan satırlar (parse hatası ya da hiç
   fact-check yapılmamış) listenin EN SONUNA konur, sıfır şüpheli gibi
   görünmesinler diye — bunlar "az şüpheli" değil "henüz bilinmiyor" demektir.
   Dashboard `/api/claims` bunu okur.

2. **`claim_archive.csv`** — `archived_at` dolu iddialar. Dashboard
   `/api/claims/archived` bunu okur (arşiv filtresi).

3. **`narrative_clusters.csv`** — aynı/benzer iddianın **birden fazla kanalda**
   tekrar ettiği kümeler. Tek kanaldaki tek iddiadan çok, aynı yanlış anlatının
   kaç farklı kanalda dolaştığı asıl haber değeridir. `priority_score` burada
   hem şüphe skorunu hem kategori riskini (tedavi/doz/tanı daha ağır) hem de
   kaç kanalı etkilediğini birleştirir.

4. **`videos.csv`** — video bazlı özet (iddia sayısı, max şüphe, thumbnail).
   Dashboard `/api/videos` bunu okur; dosya yoksa boş liste döner (404 değil).

**Kümeleme yöntemi ve sınırı**: `utils/text_similarity.py`, saf harf/kelime
benzerliği (`SequenceMatcher`, single-linkage) kullanıyor — geliştirme sırasında
önce sadece kümenin ilk üyesiyle karşılaştıran bir versiyon yazmıştım, bu sıraya
bağlı kayıplara yol açıyordu (iki farklı kanaldaki AYNI yanlış iddia, aralarına
başka bir metin girince ayrı kümelere düşüyordu); single-linkage'a geçirdim
(yeni öğe kümedeki HERHANGİ BİR üyeye benzerse eklenir). Bunun bilinen bedeli
**zincirleme**: A~B ve B~C eşleşse, A~C doğrudan benzemese bile üçü tek kümede
toplanır — testte tam olarak bu gözlendi (genel bir iddia, daha spesifik bir
iddiayla aynı kümeye çekildi). Daha ciddisi: bu hâlâ SADECE harf dizisi
benzerliği — "X kanseri önler" ile "X tümör oluşumunu azaltır" gibi anlamca
aynı ama farklı kelimelerle yazılmış iki iddiayı YAKALAYAMAZ. Gerçek anlam
benzerliği için embedding tabanlı yaklaşım (ör. `sentence-transformers` ile
cosine similarity) gerekir — bu prototipte kapsam dışı bırakıldı.

## Kanıt getirme: CER makalesinden öğrenilen iyileştirme

Konuşmada bulduğumuz [CER](https://github.com/PRAISELab-PicusLab/CER) (SIGIR 2025,
biyomedikal fact-checking'de HealthFC/BioASQ-7/SciFact'te state-of-the-art)
makalesinden iki fikir alınıp `utils/evidence_retrieval.py`'ye uygulandı:

1. **Sadece başlık değil, gerçek özet.** Önceki sürüm PubMed ESummary ile
   sadece makale BAŞLIĞINI çekiyordu — "Erectile dysfunction and pelvic
   floor: a review" başlığı, bir iddiayı destekleyip desteklemediğini
   söylemez. Artık EFetch ile tam özet (abstract) metni çekiliyor.
2. **Sparse + dense hibrit.** PubMed'in kendi arama motoru (ESearch, "sparse")
   en alakalı sonucu her zaman ilk sıraya koymaz. Şimdi geniş bir aday havuzu
   (10 makale) çekilip, yerel bir çok dilli embedding modeliyle (`sentence-transformers`,
   kurulu değilse otomatik atlanır, çökme olmaz) iddiaya en yakın 3 tanesi
   seçiliyor (dense rerank).
3. **Çeviri sorunu ayrı bir modül gerektirmeden çözüldü.** İddia çıkarma
   aşamasında (`utils/claude_client.py`) Claude, her iddia için kısa bir
   İngilizce arama sorgusu (`search_query_en`) da üretiyor — zaten çalışan bir
   LLM çağrısına bindirildi, ayrı bir çeviri modeli kurmaya gerek kalmadı.

Bu üç değişiklik test edildi (`utils/evidence_retrieval.py` sahte PubMed
yanıtlarıyla; gerçek `eutils.ncbi.nlm.nih.gov` çağrısı bu ortamda ağ kısıtı
nedeniyle test edilemedi — kendi ortamınızda ilk çalıştırmada doğrulayın).

## Bilinen sınırlamalar (bir sonraki iterasyonda ele alınmalı)

İlk versiyonun kod incelemesinde bulunup düzeltilenler: zaman damgalarının
sessizce kaybolması, kanıt bulunamadığında iddianın kendisiyle karşılaştırılması
(yanlış "doğrulandı" riski), 0 iddialı videoların sonsuz tekrar işlenmesi,
Türkçe İ/I büyük-küçük harf hatası, tek satır hatasında tüm batch'in durması,
kanıt getirmede sadece başlık kullanılması ve Türkçe→İngilizce çeviri katmanının
eksikliği (CER makalesinden sonra düzeltildi — bkz. yukarıdaki bölüm).
Hâlâ **çözülmemiş**, bilerek kapsam dışı bırakılan noktalar:

- **Dense rerank embedding modeli genel amaçlı, sağlık-özel değil.**
  `paraphrase-multilingual-MiniLM-L12-v2` iyi bir genel çok dilli model ama
  biyomedikal terminolojiye özel eğitilmemiş (CER'in kullandığı özel
  biyomedikal retriever'lar gibi değil). Daha iyi sonuç için PubMedBERT
  tabanlı bir çok dilli embedding modeli araştırılabilir.
- **Kanallar arası içerik benzerliği tespiti yok.** Konuşmanın başında
  önerdiğimiz "aynı şablon/aynı operatör" tespiti (açıklama metinlerinin
  embedding/cosine benzerliği) hiç kodlanmadı — şu an sadece basit anahtar
  kelime taraması var. Koordineli ağ tespiti için bu eksik.
- **Risk skorunun zaman serisi yok.** `channel_risk_scores` her çalıştırmada
  üzerine yazılıyor (upsert), trend göremezsiniz. `channel_snapshots` gibi
  ayrı bir `channel_risk_score_history` tablosu eklemek gerekir.
- **Uzun video kırpma.** `extract_claims`, 15.000 karakterden uzun
  transkriptleri kırpıyor — çok uzun videolarda sonundaki iddialar kaçabilir.
  Gerçek chunking (örtüşmeli parçalama + birleştirme) eklenmedi.
- **`youtube-transcript-api` sürüm belirsizliği.** Kütüphane 1.0'da API'yi
  değiştirdi; kod her iki sürümü de deniyor ama hangisinin kurulu olduğunu
  `pip show youtube-transcript-api` ile kontrol etmeniz önerilir.
- **Yorum bot tespiti naif O(n²) karşılaştırma kullanıyor.** `find_duplicate_clusters`
  binlerce yorumda yavaşlar; üretimde MinHash/SimHash gibi yaklaşık kümeleme
  yöntemlerine geçin.
- **Fact-check confidence varsayılanı.** Escalate edilen "tartışmalı" iddiaların
  bir kısmı tam `confidence=0.55` / `suspicion=61.0` değerine yığılabiliyor
  (model "emin değilim" sayısı). Prompt + `default_conf` bayrağı bunu
  yakalar; eski kayıtları düzeltmek için `--recheck-ids` gerekir. Ham
  `reasoning` daha önce kaydedilmiyordu — şimdi DB + jsonl'de.

## Bilgisayarınıza hiçbir şey kurmadan deploy (GitHub + Render, tarayıcıdan)

**1. GitHub'a yükleyin (git kurmadan)**
- github.com'da hesap açın, yeni bir repo oluşturun (private yapabilirsiniz)
- Repo sayfasında "Add file → Upload files" ile bu klasördeki TÜM dosyaları
  sürükle-bırak yükleyin

**2. Render'a bağlayın**
- render.com'da hesap açın, GitHub hesabınızı yetkilendirin
- "New → Blueprint" seçip az önce yüklediğiniz repoyu gösterin — Render bu
  projedeki `render.yaml` dosyasını okuyup İKİ servisi otomatik oluşturur:
  - `health-misinfo-dashboard` (web servisi — size bir URL verir)
  - `health-misinfo-pipeline` (cron job — pipeline'ı günlük 06:00'da çalıştırır)
- Render dashboard'ında her iki servis için de `ANTHROPIC_API_KEY` ve
  `YOUTUBE_API_KEY`'i **Environment** sekmesinden elle girin (render.yaml'da
  `sync: false` olduğu için koda hiç yazılmaz, sadece Render'ın kendi
  şifreli ortam değişkeni deposunda durur)

**3. `data/channels.csv`'yi yükleyin**
Cron job ilk çalıştığında `data/channels.csv` dosyasını arayacak — bunu
GitHub reposuna diğer dosyalarla birlikte yüklediğinizden emin olun
(kanal listenizi hazırlama adımı yukarıda).

**4. URL'nizi test edin**
Render, web servisiniz için `https://health-misinfo-dashboard.onrender.com`
gibi bir adres verir. İlk açılışta (cron job henüz çalışmadıysa) dashboard
"henüz veri yok" gösterir — bu normal, cron job'un ilk çalışmasını bekleyin
ya da Render dashboard'ından cron job'u "Trigger Run" ile elle bir kez
tetikleyebilirsiniz.

**Neden iki ayrı servis?** Web servisi (dashboard) her zaman açık kalmalı
ki URL çalışsın; cron job ise sadece günde bir kez, kısa süre çalışıp kapanır.
İkisini birleştirseydik, ağır pipeline çalışırken dashboard'a gelen istekler
zaman aşımına uğrardı.

## Sonraki adımlar (genişletme fikirleri)

- **Wayback Machine entegrasyonu**: `risk_tier=acil` olan kanalların sayfalarını otomatik arşivleyin (kanıt saklama)
- **Slack/e-posta webhook**: yeni `acil` kanal tespit edildiğinde bildirim
- **Görsel/ses AI-üretim tespiti**: ters görsel arama + TTS prozodi analizi (şu an kapsam dışı)
- **GitHub Actions** ile `run_pipeline.py`'yi günlük cron olarak çalıştırma

## Dosya yapısı

```
health_misinfo_monitor/
├── app.py                    # Web sunucusu (dashboard + API) — Render deploy için
├── render.yaml                # Render Blueprint (web + cron servisleri)
├── templates/
│   └── dashboard.html         # Canlı dashboard (API'den fetch eder)
├── run_pipeline.py           # orkestratör
├── demo_seed_and_run.py      # API'siz kanıt çalıştırması
├── requirements.txt
├── .env.example
├── db/schema.sql             # SQLite şeması
├── data/                     # monitor.db, channels.csv, suspects.csv burada oluşur
├── pipeline/
│   ├── 00_healthcheck.py         # Deploy öncesi ön kontrol (yeni)
│   ├── 01_collect.py             # Aşama 1
│   ├── 02_extract_claims.py      # Aşama 2
│   ├── 03_factcheck.py           # Aşama 3
│   ├── 04_score_suspects.py      # Aşama 4
│   ├── 05_comment_authenticity.py # Aşama 5 (opsiyonel, yorum bot analizi)
│   └── 06_claim_index.py         # Aşama 6 (iddia indeksi + şüphe skoru)
└── utils/
    ├── youtube.py            # YouTube API + RSS + transkript + yorumlar
    ├── claude_client.py      # iddia çıkarımı (+ search_query_en) + escalation
    ├── evidence_retrieval.py  # PubMed sparse+dense kanıt getirme (CER'den esinli)
    ├── nli.py                 # ucuz HF NLI sınıflandırma filtresi
    ├── bot_detection.py       # yorum bot skorlama heuristikleri
    ├── text_similarity.py     # paylaşılan metin kümeleme (yorumlar + iddialar)
    ├── suspicion.py            # sürekli şüphe/öncelik skoru
    ├── factcheck_calibrate.py  # kaynak kademesi / tersine-verdict kırpma
    └── db.py
```
