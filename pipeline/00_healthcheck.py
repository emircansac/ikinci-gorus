"""
AŞAMA 0: Ön kontrol (preflight healthcheck).

Amaç: Render'a (ya da başka bir yere) deploy etmeden ÖNCE, gerçek bir video/iddia
işlemeden, sistemin gerçekten çalışmaya hazır olduğunu doğrulamak. Her kontrol
küçük, ucuz (ya da ücretsiz) bir "dokunuş" yapar — asıl pipeline'ı çalıştırmaz.

Kullanım:
    python pipeline/00_healthcheck.py
    python pipeline/00_healthcheck.py --local-only   # API anahtarı kontrolü atlanır

Her satır ✓ / ✗ ile raporlanır. Tek bir ✗ varsa deploy etmeden önce onu düzeltin —
gerisi boşuna zaman/para kaybı olur (özellikle Render'da build başarısız
olursa fark etmeniz saatler alabilir).
"""
import os
import sys
import sqlite3
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent))

RESULTS = []
LOCAL_ONLY = "--local-only" in sys.argv or os.environ.get("HEALTHCHECK_LOCAL") == "1"


def check(name):
    """Dekoratör: her kontrol fonksiyonunu çalıştırır, hatayı yakalar, sonucu kaydeder."""
    def wrapper(fn):
        try:
            detail = fn()
            RESULTS.append((True, name, detail or ""))
        except Exception as e:
            RESULTS.append((False, name, str(e)))
    return wrapper


@check("ANTHROPIC_API_KEY tanımlı ve geçerli")
def _():
    if LOCAL_ONLY:
        return "yerel mod — atlandı"
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise RuntimeError("Ortam değişkeni ANTHROPIC_API_KEY boş — .env dosyasını export ettiniz mi?")
    import anthropic
    client = anthropic.Anthropic(api_key=key)
    # En ucuz mümkün çağrı: 1 token'lık bir istek. Bu gerçek parasal maliyeti olan
    # TEK kontrol — ama saniyenin altında ve kuruşun altında bir maliyet.
    resp = client.messages.create(
        model=os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5"),
        max_tokens=5,
        thinking={"type": "disabled"},
        messages=[{"role": "user", "content": "merhaba"}],
    )
    text = next((b.text for b in resp.content if getattr(b, "text", None)), None)
    if not text:
        raise RuntimeError(f"beklenmeyen yanıt blokları: {[type(b).__name__ for b in resp.content]}")
    return f"model yanıt verdi: {text[:20]!r}"


@check("YOUTUBE_API_KEY tanımlı ve geçerli")
def _():
    if LOCAL_ONLY:
        return "yerel mod — atlandı"
    key = os.environ.get("YOUTUBE_API_KEY")
    if not key:
        raise RuntimeError("Ortam değişkeni YOUTUBE_API_KEY boş")
    import requests
    # Bilinen, hep var olan bir kanalla (YouTube'un kendi resmi kanalı) test —
    # kotanız varsa 1 unit harcar, hiçbir gerçek veriye dokunmaz.
    r = requests.get("https://www.googleapis.com/youtube/v3/channels",
                      params={"part": "snippet", "id": "UCBR8-60-B28hp2BmDPdntcQ", "key": key}, timeout=10)
    data = r.json()
    if "error" in data:
        raise RuntimeError(f"API hata döndü: {data['error'].get('message')}")
    if not data.get("items"):
        raise RuntimeError("Beklenmeyen boş yanıt — anahtar geçerli ama YouTube Data API v3 etkin mi kontrol edin")
    return "kota ve anahtar geçerli"


@check("channels.csv dosyası var ve okunabiliyor")
def _():
    import pandas as pd
    path = os.environ.get("CHANNELS_CSV", "data/channels.csv")
    if not Path(path).exists():
        raise RuntimeError(f"{path} bulunamadı — README'deki 'Kanal listenizi hazırlama' adımını yaptınız mı?")
    df = pd.read_csv(path)
    if "channel_id" not in df.columns:
        raise RuntimeError(f"{path} bir 'channel_id' sütunu içermiyor")
    n = df["channel_id"].dropna().shape[0]
    if n == 0:
        raise RuntimeError(f"{path} içinde geçerli hiçbir channel_id yok")
    return f"{n} kanal ID'si okundu"


@check("Veritabanı şeması sorunsuz uygulanıyor")
def _():
    from utils.db import get_conn, init_db, DB_PATH
    # Gerçek veriyi bozmamak için ayrı, geçici bir dosyada test ediyoruz.
    test_path = DB_PATH.parent / "_healthcheck_test.db"
    if test_path.exists():
        test_path.unlink()
    conn = sqlite3.connect(test_path)
    with open(Path(__file__).parent.parent / "db" / "schema.sql", encoding="utf-8") as f:
        conn.executescript(f.read())
    conn.close()
    test_path.unlink()
    return "schema.sql temiz bir veritabanında hatasız çalıştı"


@check("youtube-transcript-api kurulu ve import edilebiliyor")
def _():
    import youtube_transcript_api
    return f"sürüm bilgisi mevcut değilse de kütüphane import edildi ({youtube_transcript_api.__file__})"


@check("HF NLI modeli yüklenebiliyor (opsiyonel — --skip-nli kullanacaksanız önemsiz)")
def _():
    if os.environ.get("SKIP_NLI_CHECK") == "1":
        return "SKIP_NLI_CHECK=1, atlandı"
    from utils.nli import _get_pipeline
    _get_pipeline()  # ilk çağrıda modeli indirir/yükler — bu yüzden biraz sürebilir
    return "model belleğe yüklendi"


@check("sentence-transformers kurulu (opsiyonel — kanıt getirmede dense rerank için)")
def _():
    from utils.evidence_retrieval import _get_embedder
    embedder = _get_embedder()
    if embedder is None:
        return "kurulu değil — sparse (PubMed sıralaması) moduna düşülecek, sistem çökmez ama kalite biraz düşer"
    return "kurulu, dense rerank aktif"


def main():
    print("\n=== ÖN KONTROL SONUÇLARI ===\n")
    all_ok = True
    for ok, name, detail in RESULTS:
        mark = "✓" if ok else "✗"
        print(f"{mark} {name}")
        if detail:
            print(f"    {detail}")
        if not ok:
            all_ok = False

    print()
    if all_ok:
        print("✅ Tüm kontroller geçti. Render'a (ya da başka bir ortama) deploy etmeye hazırsınız.")
        print("   Ama bu, GERÇEK VERİYLE UÇTAN UCA çalıştığını KANITLAMAZ — sadece parçaların")
        print("   ayrı ayrı çalışabildiğini gösterir. Bir sonraki adım: 'python run_pipeline.py "
              "--channels data/channels.csv --max-videos 2' ile 1-2 gerçek kanalda küçük bir "
              "deneme yapıp çıkan claim_index.csv'yi elle gözden geçirin.")
    else:
        print("❌ Bir veya daha fazla kontrol başarısız. Deploy etmeden önce yukarıdaki ✗ satırlarını düzeltin.")
        sys.exit(1)


if __name__ == "__main__":
    main()
