"""
AŞAMA 2 (iddia çıkarımı) ve AŞAMA 3'ün üst katmanı (LLM+arama ile escalation) için
Claude API çağrıları.

Gerekli ortam değişkeni: ANTHROPIC_API_KEY
pip install anthropic
"""
import os
import json
import time
import anthropic

client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

# Konfigüre edilebilir: model kimlikleri zamanla değişir, hardcoded bırakmayın.
# Güncel model listesi için: https://docs.claude.com/en/docs/about-claude/models
MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5")

MAX_RETRIES = 3


def _response_text(resp) -> str:
    """Metin bloklarını birleştir (extended thinking modellerinde ThinkingBlock atlanır)."""
    parts = [b.text for b in resp.content if getattr(b, "text", None)]
    return "\n".join(parts).strip()


def _call_with_retry(**kwargs):
    """Rate limit / geçici API hatalarında basit exponential backoff."""
    kwargs.setdefault("thinking", {"type": "disabled"})
    for attempt in range(MAX_RETRIES):
        try:
            return client.messages.create(**kwargs)
        except (anthropic.RateLimitError, anthropic.APIConnectionError, anthropic.InternalServerError) as e:
            if attempt == MAX_RETRIES - 1:
                raise
            wait = 2 ** attempt * 2
            print(f"  [claude] {type(e).__name__}, {wait}s bekleyip tekrar deneniyor...")
            time.sleep(wait)


def _extract_json(text: str) -> dict | None:
    """
    Modelin çıktısına markdown fence veya açıklama metni sızmış olsa bile JSON'ı
    bulmaya çalışır. extract_claims ve escalate_factcheck arasında tutarlılık için
    ortak fonksiyona çıkarıldı (öncesinde ikisi farklı/eksik mantık kullanıyordu).
    """
    text = text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end != -1:
            try:
                return json.loads(text[start:end + 1])
            except json.JSONDecodeError:
                return None
        return None

CLAIM_EXTRACTION_SYSTEM = """\
Sen bir sağlık dezenformasyonu araştırma asistanısın. Görevin, YouTube video \
transkriptlerinden doğrulanabilir tıbbi/sağlık iddialarını çıkarmak.

Kurallar:
- Her iddiayı tek cümlelik, kontrol edilebilir bir önermeye dönüştür \
  (ör. "X bitkisi Y hastalığını tedavi eder", "günde 5000mg Z alınmalı").
- Genel motivasyon cümlelerini, selamlamaları, abone çağrılarını İDDİA SAYMA.
- Her iddiaya bir kategori ata: tedavi | tanı | doz | önleme | mucize-ürün | mekanizma | diğer
- Her iddiaya kaba bir ilk risk tahmini ver: low | medium | high
  (high = spesifik doz/tedavi değişikliği öneriyor, doktora danışmadan uygulanabilir \
  bir "teknik" iddia ediyor, ya da ağır bir hastalığı (kanser, kalp, böbrek) hedefliyorsa)
- Her iddia için ayrıca PubMed'de arama yapmaya uygun, KISA (3-6 kelime) bir İNGİLİZCE
  arama sorgusu üret (search_query_en). Bu, iddianın kendisinin çevirisi DEĞİL —
  PubMed'in bulacağı makale türünü hedefleyen anahtar kelimelerdir. Örnek:
  iddia "Perine bölgesindeki sinir sıkışması sertleşme sorununun ana nedenidir" ise
  search_query_en: "pudendal nerve entrapment erectile dysfunction".
  (Neden burada: Türkçe iddiayi doğrudan PubMed'e göndermek neredeyse hiç sonuç
  getirmez, PubMed büyük ölçüde İngilizce indekslidir. Ayrı bir çeviri modeli
  kurmak yerine, zaten çalıştırdığımız bu LLM çağrısına bindiriyoruz.)
- Transkript metninde her parça [Ns] formatında zaman damgasıyla etiketlenmiştir
  (ör. "[42s] Bu da..."). timestamp_sec'i UYDURMAYIN — sadece iddiaya en yakın
  [Ns] etiketindeki sayıyı kullanın. Transkriptte hiç [Ns] etiketi yoksa
  timestamp_sec alanını null bırakın.
- SADECE JSON döndür, başka hiçbir metin ekleme.

Çıktı şeması:
{
  "claims": [
    {"timestamp_sec": 180, "claim_text": "...", "category": "...", "initial_risk": "...",
     "search_query_en": "..."}
  ]
}
"""

FACTCHECK_ESCALATION_SYSTEM = """\
Sen bir tıbbi iddia doğrulama asistanısın. Sana bir iddia verilecek. Web araması \
kullanarak bu iddiayı güvenilir kaynaklara (PubMed, Cochrane, WHO, resmi sağlık \
kurumları) dayanarak değerlendir.

Kurallar:
- Kaynak göstermeden asla "yanlış" veya "doğru" deme.
- Kanıt yoksa veya tartışmalıysa bunu açıkça belirt ("belirsiz"/"tartışmalı").
- Nadir bir durumu genel bir nedenmiş gibi sunan abartılı genellemeleri özellikle işaretle.
- SADECE JSON döndür.

Çıktı şeması:
{
  "final_verdict": "doğrulanmış|yanlış|tartışmalı|belirsiz",
  "confidence": 0.0-1.0,
  "reasoning": "1-2 cümlelik gerekçe",
  "source_url": "en güçlü kaynağın URL'si"
}
"""


def extract_claims(transcript: str) -> list[dict]:
    """
    Bir video transkriptinden atomik iddiaları çıkarır.

    NOT — uzun videolar: transkript ~15000 karakterin üzerindeyse kırpılır, yani
    çok uzun (>1 saat) videolarda son kısımdaki iddialar kaçırılabilir. Üretimde
    bunun yerine transkripti örtüşmeli parçalara bölüp (chunking) her parçayı ayrı
    işlemek ve claim'leri birleştirmek daha doğru olur; bu prototipte basit
    kırpma tercih edildi.
    """
    if len(transcript) > 15000:
        print(f"  [claude] uyarı: transkript {len(transcript)} karakter, 15000'e kırpılıyor "
              f"(videonun sonundaki iddialar kaçabilir)")
    resp = _call_with_retry(
        model=MODEL,
        max_tokens=2000,
        system=CLAIM_EXTRACTION_SYSTEM,
        messages=[{"role": "user", "content": transcript[:15000]}],
    )
    parsed = _extract_json(_response_text(resp))
    if parsed is None or "claims" not in parsed:
        raw = _response_text(resp)
        print(f"[claude] JSON parse hatası. Ham çıktı: {raw[:300]}")
        return []
    return parsed["claims"]


def escalate_factcheck(claim_text: str) -> dict:
    """
    NLI ilk filtresi 'belirsiz'/'düşük güven' dediğinde, ya da initial_risk=high
    olduğunda çağrılır. Web search tool açık şekilde kullanılmalı (aşağıdaki
    tools parametresi API tarafında web search'ü etkinleştirir).
    """
    resp = _call_with_retry(
        model=MODEL,
        max_tokens=1500,
        system=FACTCHECK_ESCALATION_SYSTEM,
        tools=[{"type": "web_search_20250305", "name": "web_search"}],
        messages=[{"role": "user", "content": f"İddiayı değerlendir: {claim_text}"}],
    )
    # Son text bloğunu al (tool_use/tool_result aralarına serpiştirilmiş olabilir)
    text_blocks = [b.text for b in resp.content if getattr(b, "text", None)]
    full_text = "\n".join(text_blocks).strip()
    parsed = _extract_json(full_text)
    if parsed is None:
        # ÖNEMLİ: parse hatası "belirsiz" değil "işlenemedi" anlamına gelir — bunu
        # 04_score_suspects.py'de "doğrulanmış" gibi göstermemek için final_verdict'i
        # None bırakıyoruz ve ayrı bir bayrak koyuyoruz. Önceki sürüm burada sessizce
        # "belirsiz"/0.0 güven yazıp riski hafiflettiği için düzeltildi.
        print(f"[claude] escalate_factcheck JSON parse hatası. Ham çıktı: {full_text[:300]}")
        return {"final_verdict": None, "confidence": None,
                "reasoning": "LLM çıktısı parse edilemedi — insan gözden geçirmeli", "source_url": "",
                "parse_failed": True}
    return parsed
