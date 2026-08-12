"""
AŞAMA 5: Yorum özgünlük analizi.

DÜRÜST SINIR: YouTube API "bu kanala kim abone oldu" listesini vermiyor (gizlilik).
Yani "abonelerin kaçı bot" diye doğrudan ölçemeyiz — bunun için elimizdeki tek
dolaylı sinyal zaten 04_score_suspects.py'deki growth_anomaly_flag (anormal
abone artış hızı). BURADA ölçebildiğimiz şey YORUMLAR — çünkü yorumcunun kanal
ID'si, metni ve zamanı herkese açık.

Kullandığımız 4 sinyal (her biri tek başına kanıt değil, BİRLİKTE anlamlı):

1. NEREDEYSE AYNI METİN (duplicate/near-duplicate) — aynı ya da çok benzer yorum
   metni birden fazla farklı kişi tarafından (ya da aynı kişi tarafından farklı
   videolarda) atılmışsa şablon/bot şüphesi.
2. PATLAMA (burst) — çok kısa bir zaman penceresinde aynı videoya/kanala anormal
   yoğunlukta yorum düşmesi (koordineli kampanya paterni).
3. ÇOK YENİ HESAP — yorumcunun kanalı çok yakın zamanda açılmışsa ve hiç public
   videosu yoksa (tipik "amplifier" bot hesabı paterni).
4. JENERİK ÖVGÜ ŞABLONU — "çok faydalı bilgi teşekkürler doktor" gibi, hiçbir
   videoya özgü detay içermeyen, kopyala-yapıştır hissi veren kısa övgüler.

Bu dört sinyal ne "kesin bot" ne de "kesin insan" kanıtıdır — organik bir video da
gerçekten çok sayıda benzer içerikte yorum alabilir (herkes "çok faydalı" yazar).
Bu yüzden bot_score bir OLASILIK puanıdır, kesin hüküm değildir; yüksek puanlı
kanallar insan gözden geçirmesine düşürülmelidir (tıpkı Aşama 3'teki gibi).
"""
import re
from datetime import datetime, timedelta
from utils.text_similarity import find_similar_clusters, normalize as _normalize

GENERIC_PRAISE_PATTERNS = [
    r"^çok (faydalı|güzel|yararlı).{0,30}(bilgi|video)?.{0,20}(teşekkür)",
    r"^teşekkürler (doktor|hocam)",
    r"^allah razı olsun",
    r"^harika bir? video",
    r"^emeğinize sağlık",
]

BURST_WINDOW_MINUTES = 3
BURST_MIN_COUNT = 5          # aynı pencerede 5+ yorum -> şüpheli patlama
DUPLICATE_SIMILARITY_THRESHOLD = 0.85
NEW_ACCOUNT_DAYS = 30        # kanal 30 günden yeniyse ve 0 videosu varsa şüpheli


def _is_generic_praise(text: str) -> bool:
    norm = _normalize(text)
    return any(re.match(p, norm) for p in GENERIC_PRAISE_PATTERNS)


def find_duplicate_clusters(comments: list[dict]) -> dict:
    """
    Yorumları normalize edip birbirine çok benzeyenleri kümeler.
    Dönüş: {comment_id: cluster_size} — cluster_size 1 ise benzersiz demektir.
    (Ortak mantık utils/text_similarity.py'ye taşındı — 06_claim_index.py da
    aynı fonksiyonu iddialar üzerinde kullanıyor.)
    """
    return find_similar_clusters(comments, id_key="comment_id", text_key="text",
                                  threshold=DUPLICATE_SIMILARITY_THRESHOLD)


def find_burst_windows(comments: list[dict], dup_clusters: dict) -> set:
    """
    Aynı videoya kısa zaman penceresinde yığılan yorumların comment_id'lerini döner.

    ÖNEMLİ DÜZELTME: "burst" bayrağı SADECE zaten bir kopya kümesinin (dup_clusters'ta
    cluster_size>1 olan) parçası olan yorumlara uygulanır. Aksi halde tamamen organik,
    benzersiz bir yorum -sırf tesadüfen bir bot dalgasının zamanına denk geldiği için-
    yanlışlıkla "burst" olarak işaretlenir (test sırasında tam olarak bu senaryo
    gözlendi — bkz. README "Bilinen sınırlamalar"). Salt zamansal yoğunluk tek başına
    yeterli kanıt değildir; içerik benzerliğiyle birleşmesi gerekir.
    """
    parsed = []
    for c in comments:
        try:
            ts = datetime.fromisoformat(c["published_at"].replace("Z", "+00:00"))
            parsed.append((ts, c["comment_id"]))
        except (ValueError, AttributeError, KeyError):
            continue
    parsed.sort()

    burst_ids = set()
    window = timedelta(minutes=BURST_WINDOW_MINUTES)
    for ts, cid in parsed:
        if dup_clusters.get(cid, 1) < 2:
            continue  # kümede değilse burst kontrolüne bile girmiyor
        count = sum(1 for ts2, cid2 in parsed if ts <= ts2 <= ts + window and dup_clusters.get(cid2, 1) >= 2)
        if count >= BURST_MIN_COUNT:
            for ts2, cid2 in parsed:
                if ts <= ts2 <= ts + window and dup_clusters.get(cid2, 1) >= 2:
                    burst_ids.add(cid2)
    return burst_ids


def score_comments(comments: list[dict], commenter_profiles: dict) -> list[dict]:
    """
    Her yoruma 0-100 bot_score ve tetiklenen bayrakları (bot_flags) atar.
    commenter_profiles: {author_channel_id: {"channel_created_at":..., "public_video_count":...}}
    (utils/youtube.get_channel_creation_dates ile doldurulur; boş sözlük geçilebilir,
    o zaman sadece metin/zaman tabanlı 3 sinyal kullanılır.)
    """
    dup_clusters = find_duplicate_clusters(comments)
    burst_ids = find_burst_windows(comments, dup_clusters)

    scored = []
    for c in comments:
        flags = []
        score = 0

        cluster_size = dup_clusters.get(c["comment_id"], 1)
        if cluster_size >= 5:
            flags.append("duplicate")
            score += 40
        elif cluster_size >= 2:
            score += 15

        if c["comment_id"] in burst_ids:
            flags.append("burst")
            score += 25

        if _is_generic_praise(c.get("text", "")):
            flags.append("generic")
            score += 15

        profile = commenter_profiles.get(c.get("author_channel_id"))
        if profile and profile.get("channel_created_at"):
            try:
                created = datetime.fromisoformat(profile["channel_created_at"].replace("Z", "+00:00"))
                age_days = (datetime.now(created.tzinfo) - created).days
                if age_days < NEW_ACCOUNT_DAYS and profile.get("public_video_count", 0) == 0:
                    flags.append("new_account")
                    score += 20
            except ValueError:
                pass

        scored.append({**c, "bot_score": min(score, 100), "bot_flags": ",".join(flags)})
    return scored
