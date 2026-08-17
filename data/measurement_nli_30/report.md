# measurement_nli_30 — NLI etkin fact-check + shadow v1

## Seçim

- **30 iddia**, seed=43, stratified (high=3, medium=15, low=12)
- measurement_50 ile **çakışma yok**
- **NLI açık** (`--skip-nli` yok); batch API + prompt cache
- Batch ID: `msgbatch_01TtRfUPQcppAvcLfC3htxSB`

## Ana sonuçlar

| Metrik | Değer |
|--------|-------|
| Toplam işlenen | **30/30** |
| **escalated=0** (NLI-only) | **0/30** |
| **escalated=1** (Claude batch) | **30/30** |
| **would_auto_accept_v1=True** (tüm 30) | **0/30** |
| **would_auto_accept_v1=True** (escalated=0 alt kümesi) | **0/0** (alt küme boş) |

### Neden escalated=0 yok?

Submit aşamasında **30/30** iddia `should_escalate=True` ile batch kuyruğuna gitti; 0 iddia senkron (NLI-only) yazıldı. NLI güveni ≥0.75 olan SUPPORTS/REFUTES tek başına yeterli olmadı — çoğu iddiada güven <0.75, NOT_ENOUGH_INFO veya partial-caveat/high-risk tetikleyicileri var.

### False dağılımı (would_auto_accept_v1)

- escalated:not_nli_only: **29**
- out_of_scope:package_only_forced: **1**

### Verdict dağılımı

- doğrulanmış: **12**
- tartışmalı: **11**
- belirsiz: **5**
- yanlış: **2**

## would_auto_accept_v1=True örnekleri

Bu örneklemde **hiç True yok**. Tüm iddialar escalated=1; 29/30 `escalated:not_nli_only`, 1/30 `out_of_scope:package_only_forced` (#909 parse retry sonrası).

## Tam tablo (30 iddia)

| ID | risk | esc | NLI | verdict | shadow | reason |
|----|------|-----|-----|---------|--------|--------|
| 251 | medium | 1 | NOT_ENOUGH_INFO@0.359 | doğrulanmış | 0 | escalated:not_nli_only |
| 258 | medium | 1 | SUPPORTS@0.650 | doğrulanmış | 0 | escalated:not_nli_only |
| 354 | medium | 1 | NOT_ENOUGH_INFO@0.373 | tartışmalı | 0 | escalated:not_nli_only |
| 744 | low | 1 | SUPPORTS@0.374 | tartışmalı | 0 | escalated:not_nli_only |
| 773 | medium | 1 | SUPPORTS@0.357 | doğrulanmış | 0 | escalated:not_nli_only |
| 822 | low | 1 | SUPPORTS@0.484 | tartışmalı | 0 | escalated:not_nli_only |
| 826 | medium | 1 | SUPPORTS@0.383 | belirsiz | 0 | escalated:not_nli_only |
| 860 | low | 1 | SUPPORTS@0.468 | yanlış | 0 | escalated:not_nli_only |
| 878 | medium | 1 | SUPPORTS@0.511 | tartışmalı | 0 | escalated:not_nli_only |
| 899 | medium | 1 | SUPPORTS@0.367 | tartışmalı | 0 | escalated:not_nli_only |
| 909 | low | 1 | SUPPORTS@0.456 | belirsiz | 0 | out_of_scope:package_only_forced |
| 911 | low | 1 | SUPPORTS@0.451 | tartışmalı | 0 | escalated:not_nli_only |
| 927 | high | 1 | SUPPORTS@0.585 | doğrulanmış | 0 | escalated:not_nli_only |
| 950 | low | 1 | SUPPORTS@0.359 | belirsiz | 0 | escalated:not_nli_only |
| 992 | low | 1 | REFUTES@0.431 | tartışmalı | 0 | escalated:not_nli_only |
| 1005 | medium | 1 | SUPPORTS@0.349 | belirsiz | 0 | escalated:not_nli_only |
| 1026 | low | 1 | SUPPORTS@0.430 | doğrulanmış | 0 | escalated:not_nli_only |
| 1031 | medium | 1 | NOT_ENOUGH_INFO@0.398 | doğrulanmış | 0 | escalated:not_nli_only |
| 1071 | high | 1 | SUPPORTS@0.375 | doğrulanmış | 0 | escalated:not_nli_only |
| 1120 | low | 1 | SUPPORTS@0.418 | yanlış | 0 | escalated:not_nli_only |
| 1121 | low | 1 | SUPPORTS@0.494 | doğrulanmış | 0 | escalated:not_nli_only |
| 1172 | medium | 1 | SUPPORTS@0.455 | tartışmalı | 0 | escalated:not_nli_only |
| 1177 | medium | 1 | SUPPORTS@0.396 | tartışmalı | 0 | escalated:not_nli_only |
| 1201 | medium | 1 | NOT_ENOUGH_INFO@0.421 | doğrulanmış | 0 | escalated:not_nli_only |
| 1207 | high | 1 | SUPPORTS@0.371 | tartışmalı | 0 | escalated:not_nli_only |
| 1214 | medium | 1 | SUPPORTS@0.391 | tartışmalı | 0 | escalated:not_nli_only |
| 1246 | low | 1 | SUPPORTS@0.418 | doğrulanmış | 0 | escalated:not_nli_only |
| 1273 | medium | 1 | SUPPORTS@0.390 | doğrulanmış | 0 | escalated:not_nli_only |
| 1301 | medium | 1 | SUPPORTS@0.389 | belirsiz | 0 | escalated:not_nli_only |
| 1305 | low | 1 | NOT_ENOUGH_INFO@0.397 | doğrulanmış | 0 | escalated:not_nli_only |

<details><summary>claim_text (30)</summary>

**#251** — Kaslara hareket sinyali verilmezse, yeterli beslenmeye rağmen vücut kasları gereksiz görüp eritmeye başlar.

**#258** — Sarkopeni (kas kaybı) genellikle yağ birikiminin arkasına saklanır, bu yüzden kilo değişmese bile kas kaybı yaşanabilir.

**#354** — Kırmızı pancarın damar sistemini desteklediği MR sonuçlarıyla kanıtlanmıştır.

**#744** — Kivinin glisemik indeksi 38, glisemik yükü 9'dur.

**#773** — İnsülin duyarlılığı sabah saatlerinde daha yüksek, öğleden sonra ve akşam saatlerinde daha düşüktür, bu nedenle aynı meyve sabah yenildiğinde daha kontrollü glisemik yanıt oluşturur.

**#822** — Çörek otu, B vitamini ve çinko gibi uyku düzenini destekleyen besin öğelerini içerir.

**#826** — Vaka: 2 hafta çörek otu tüketimi sonrası hastanın uyku süresi 5. günde sabah 4'e, 10. günde sabah 6'ya uzamıştır.

**#860** — Platisma kasının çene hattındaki sarkma, deri gevşemesi değil kas gerginliği nedeniyle oluşan mekanik bir çekilmedir.

**#878** — 2-3 haftalık tutarlı pratik sonrasında boyun arkasındaki sıkışma azalır ve sabahları yüzdeki şişlik ile ağırlık hissi kaybolur.

**#899** — Göz altı derisine iki parmakla 5 saniye bastırıp bırakarak yapılan basit bir 'pitting' testiyle evde sıvı birikimi teşhis edilebilir; derinin eski haline dönme süresi sıvı birikiminin derecesini gösterir.

**#909** — Yaban mersini ve böğürtlen gibi koyu renkli orman meyvelerindeki antosyaninler kılcal damar duvarlarını güçlendirerek gözaltına sıvı sızıntısını yavaşlatır.

**#911** — Sabah kalkmadan önce göz iç köşesinden elmacık kemiğine doğru yapılan lenf masajı, lenf sıvısının doğal akış yönünü takip ederek sabah şişliğinin mekanik olarak tahliyesini destekler.

**#927** — Yüz ve bacaklarda geçmeyen inatçı şişliklerin diğer semptomlarla birlikte görülmesi acil klinik değerlendirme gerektirir.

**#950** — Vaka: 67 yaşındaki bir danışan 4 ay boyunca günlük kaloriyi 2000'den 1400'e düşürüp günlük 45 dakika yürüyüş yaptığı halde sadece 2 kilo verebilmiştir.

**#992** — Başın öne doğru eğik durması (öne kayık duruş) boyun kaslarına ekstra yük bindirir ve platizma ile hiyoid bölgesi kaslarının işlevsiz kalıp gevşemesine yol açar.

**#1005** — Boyundaki mekanik baskıyı (öne eğik duruş) azaltmak en pahalı kremlerden bile daha etkili bir sarkma önleme yöntemidir.

**#1026** — Bağırsaklarda yaklaşık 38 trilyon bakteri bulunur ve bu mikroorganizmalar vitamin üretimi, bağışıklık dengesi ve iltihap kontrolü sağlar.

**#1031** — Artan bağırsak geçirgenliği (leaky gut) durumunda bakteri parçacıkları ve toksinler kan dolaşımına sızar ve düşük seviyeli kronik iltihaplanmaya yol açar.

**#1071** — Gözde daha önce olmayan çok sayıda siyah nokta veya uçuşan cismin aniden belirmesi ciddi bir durumdur.

**#1120** — 40 yaşından sonra, özellikle 60'lı yaşlara doğru vücuttaki kolajen üretiminin azalması damar duvarı ve kapakçıkların zayıflamasına neden olur.

**#1121** — Kronik kabızlık, sürekli öksürük veya karın bölgesindeki fazla kilo gibi karın içi basıncını artıran durumlar bu baskıyı bacak damarlarına ileterek kapakçıkları zayıflatır.

**#1172** — Lenfatik sıvı tahliyesi yavaşlaması hiçbir ilaçla düzeltilemeyen, fiziksel hareket eksikliğine bağlı bir durumdur.

**#1177** — İşlenmiş gıdalardaki gizli sodyum tüketimi vücutta su tutulmasına neden olarak lenf ödemini kötüleştirir; her fazladan gram tuz yaklaşık dört bardak suyun vücutta tutulmasına yol açar.

**#1201** — Dokularda biriken sıvı kaynaklı ödemde idrar söktürücü ilaçlar her zaman beklenen rahatlamayı yaratmaz.

**#1207** — Bir bacakta birkaç saat içinde aniden gelişen, kızarıklık, sıcaklık ve şiddetli ağrı eşlik eden şiddetli şişlik derin ven trombozuna işaret edebilir.

**#1214** — Lenfatik kaynaklı ödem parmakla bastırıldığında çukur bırakan yumuşak bir şişlik olarak ayak sırtında yoğunlaşır.

**#1246** — Sarkopeni, yaşlanmaya bağlı olarak kas kütlesi ve kas gücünün kademeli kaybıdır.

**#1273** — İleri yaştaki bireylerde kas protein sentezi şalterinin açılması için tek seferde yaklaşık 3 gram lösine ihtiyaç vardır.

**#1301** — Vaka: 72 yaşındaki bir hastanın kahvaltısı sıfır protein içeriyordu, kahvesine peynir altı suyu proteini, MCT yağı ve tarçın eklenince bir ay içinde enerjisi arttı ve yogaya geri döndü.

**#1305** — Peynir altı suyu proteini kaslar üzerinde en hızlı etkiyi yaratan maddedir.

</details>

## Referans: DB'deki tüm escalated=0 satırlar (2 adet, bu 30'un dışında)

Formülün hedef popülasyonu (`escalated=0`) kuyrukta son derece nadir. Tüm DB'de yalnızca 2 satır var:

- **#673** shadow_recompute=False reason=check_point:not_generic_fallback
  - NLI: SUPPORTS@0.759 · verdict: doğrulanmış
  - claim: Kırmızı biberdeki folik asit homosisteini zararsız maddelere dönüştürerek damar duvarlarını korur.

- **#716** shadow_recompute=True reason=
  - NLI: SUPPORTS@0.900 · verdict: doğrulanmış
  - claim: 40-50 yaş üstü veya ailede prostat kanseri öyküsü olanlar için düzenli doktor kontrolü/PSA testi önemlidir
