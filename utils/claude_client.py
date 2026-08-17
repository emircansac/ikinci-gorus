"""
AŞAMA 2 (iddia çıkarımı) ve AŞAMA 3'ün üst katmanı (LLM+arama ile escalation) için
Claude API çağrıları.

Gerekli ortam değişkeni: ANTHROPIC_API_KEY
pip install anthropic
"""
import os
import json
import re
import time
from pathlib import Path

import anthropic

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent.parent / ".env")
except ImportError:
    pass

from utils.claim_dedup import (
    dedupe_claims as _dedupe_claims,
    dedupe_claims_local,
    dedupe_pipeline,
)

EXTRACTION_CHUNKS_DIR = Path(__file__).parent.parent / "data" / "extraction_chunks"
SAVE_EXTRACTION_CHUNKS = os.environ.get("SAVE_EXTRACTION_CHUNKS", "").lower() in ("1", "true", "yes")

client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

# Uzun videolar parçalara bölünür; tek parça için yeterli çıktı token'ı gerekir
# (2000 token eski limit JSON'u ortasında kesiyordu → Hasan vb. kanallarda 0 iddia).
TRANSCRIPT_CHUNK_CHARS = int(os.environ.get("CLAIM_CHUNK_CHARS", "10000"))
CLAIM_EXTRACTION_MAX_TOKENS = int(os.environ.get("CLAIM_MAX_TOKENS", "8192"))

# Konfigüre edilebilir: model kimlikleri zamanla değişir, hardcoded bırakmayın.
# Güncel model listesi için: https://docs.claude.com/en/docs/about-claude/models
MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5")

MAX_RETRIES = 3
WEB_SEARCH_TOOL = {"type": "web_search_20250305", "name": "web_search"}
BATCH_CUSTOM_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")


def _cached_system(text: str) -> list[dict]:
    """System prompt'u prompt-caching breakpoint'i ile sar."""
    return [{"type": "text", "text": text, "cache_control": {"type": "ephemeral"}}]


def _usage_dict(usage) -> dict:
    if usage is None:
        return {}
    if isinstance(usage, dict):
        return {
            "input_tokens": usage.get("input_tokens"),
            "output_tokens": usage.get("output_tokens"),
            "cache_creation_input_tokens": usage.get("cache_creation_input_tokens") or 0,
            "cache_read_input_tokens": usage.get("cache_read_input_tokens") or 0,
        }
    return {
        "input_tokens": getattr(usage, "input_tokens", None),
        "output_tokens": getattr(usage, "output_tokens", None),
        "cache_creation_input_tokens": getattr(usage, "cache_creation_input_tokens", None) or 0,
        "cache_read_input_tokens": getattr(usage, "cache_read_input_tokens", None) or 0,
    }


def summarize_cache_roles(usage_by_custom_id: dict) -> dict:
    """İstek bazında cache write/read: n_write_gt0 / n_read_gt0 (both her iki sayıya girer)."""
    roles: dict[str, str] = {}
    n_write_only = n_read_only = n_both = n_none = 0
    for cid, raw in (usage_by_custom_id or {}).items():
        u = raw or {}
        w = int(u.get("cache_creation_input_tokens") or 0) > 0
        r = int(u.get("cache_read_input_tokens") or 0) > 0
        if w and r:
            role = "both"
            n_both += 1
        elif w:
            role = "write"
            n_write_only += 1
        elif r:
            role = "read"
            n_read_only += 1
        else:
            role = "none"
            n_none += 1
        roles[str(cid)] = role
    return {
        "roles": roles,
        "n_write_gt0": n_write_only + n_both,
        "n_read_gt0": n_read_only + n_both,
        "n_both": n_both,
        "n_write_only": n_write_only,
        "n_read_only": n_read_only,
        "n_none": n_none,
    }


def _log_usage(usage) -> dict:
    fields = _usage_dict(usage)
    if not fields:
        return fields
    print(
        f"  [claude] usage input={fields.get('input_tokens')} "
        f"output={fields.get('output_tokens')} "
        f"cache_write={fields.get('cache_creation_input_tokens')} "
        f"cache_read={fields.get('cache_read_input_tokens')}"
    )
    return fields


def _response_text(resp) -> str:
    """Metin bloklarını birleştir (extended thinking modellerinde ThinkingBlock atlanır)."""
    parts = [b.text for b in resp.content if getattr(b, "text", None)]
    return "\n".join(parts).strip()


def _call_with_retry(**kwargs):
    """Rate limit / geçici API hatalarında basit exponential backoff."""
    kwargs.setdefault("thinking", {"type": "disabled"})
    for attempt in range(MAX_RETRIES):
        try:
            resp = client.messages.create(**kwargs)
            _log_usage(getattr(resp, "usage", None))
            return resp
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
                pass
        return _repair_truncated_claims_json(text)


def _repair_truncated_claims_json(text: str) -> dict | None:
    """
    max_tokens sınırında kesilmiş JSON'dan tamamlanmış claim nesnelerini kurtarır.
    Örn. son iddia yarım kalmışsa önceki geçerli iddialar yine de alınır.
    """
    if '"claims"' not in text and "'claims'" not in text:
        return None
    objects = []
    for m in re.finditer(
        r'\{\s*"timestamp_sec"\s*:\s*(?:null|\d+)\s*,\s*"claim_text"\s*:\s*"[^"\\]*(?:\\.[^"\\]*)*"\s*,\s*"category"\s*:\s*"[^"]+"\s*,\s*"initial_risk"\s*:\s*"[^"]+"\s*,\s*"search_query_en"\s*:\s*"[^"\\]*(?:\\.[^"\\]*)*"\s*\}',
        text,
        re.DOTALL,
    ):
        try:
            objects.append(json.loads(m.group(0)))
        except json.JSONDecodeError:
            continue
    if objects:
        return {"claims": objects}
    return None


def _split_transcript_chunks(transcript: str, max_chunk: int = TRANSCRIPT_CHUNK_CHARS) -> list[str]:
    """[Ns] sınırlarında transkripti parçalar — ortadan kesilince iddia kaybı azalır."""
    transcript = transcript.strip()
    if len(transcript) <= max_chunk:
        return [transcript]
    parts = re.split(r"(?=\[\d+s\])", transcript)
    chunks: list[str] = []
    current = ""
    for part in parts:
        if not part.strip():
            continue
        if len(current) + len(part) <= max_chunk:
            current += part
            continue
        if current.strip():
            chunks.append(current.strip())
        # Parça sınırında bağlam kaybetmemek için önceki parçanın sonunu taşı
        tail = current[-800:] if len(current) > 800 else current
        current = (tail + part).strip() if len(tail) + len(part) <= max_chunk else part.strip()
    if current.strip():
        chunks.append(current.strip())
    return chunks or [transcript[:max_chunk]]


# Son chunk özet/kapanış bölümüyse tekrar iddia üretimini kısıtlamak için işaretler
_RECAP_CHUNK_MARKERS = re.compile(
    r"(toparlay|özetle|sonuç olarak|madalyonun|konuyu toparla|gördüğünüz gibi|"
    r"amacımız|kısaca|hatırlarsanız videonun)",
    re.IGNORECASE,
)

_RECAP_CHUNK_HINT = (
    "\n\n[NOT: Bu transkript parçası videonun kapanış veya özet bölümü olabilir. "
    "Daha önceki bölümlerde zaten söylenmiş iddiaları TEKRARLAMA; yalnızca bu parçada "
    "ilk kez geçen yeni, kontrol edilebilir iddiaları çıkar.]"
)


def _is_recap_chunk(chunk: str, *, is_last: bool) -> bool:
    if not is_last:
        return False
    return bool(_RECAP_CHUNK_MARKERS.search(chunk))


def _extract_claims_once(transcript_slice: str, *, recap_hint: bool = False) -> tuple[list[dict], bool, str]:
    """
    Tek transkript parçasından iddia çıkarır.
    Dönüş: (claims, parse_ok, raw_text_for_debug)
    """
    resp = _call_with_retry(
        model=MODEL,
        max_tokens=CLAIM_EXTRACTION_MAX_TOKENS,
        system=_cached_system(CLAIM_EXTRACTION_SYSTEM),
        messages=[{"role": "user", "content": transcript_slice + (_RECAP_CHUNK_HINT if recap_hint else "")}],
    )
    raw = _response_text(resp)
    parsed = _extract_json(raw)
    if parsed is None or "claims" not in parsed:
        return [], False, raw
    claims = parsed.get("claims") or []
    if not isinstance(claims, list):
        return [], False, raw
    return claims, True, raw

CLAIM_EXTRACTION_SYSTEM = """\
Sen bir sağlık dezenformasyonu araştırma asistanısın. Görevin, YouTube video \
transkriptlerinden doğrulanabilir tıbbi/sağlık iddialarını çıkarmak.

Kurallar:
- Her iddiayı tek cümlelik, kontrol edilebilir bir önermeye dönüştür \
  (ör. "X bitkisi Y hastalığını tedavi eder", "günde 5000mg Z alınmalı").
- Genel motivasyon cümlelerini, selamlamaları, abone çağrılarını, "kanala abone ol" \
  çağrılarını ve "bu bulgular tanı koymaz" gibi saf disclaimer cümlelerini İDDİA SAYMA.
- Hasta vakalarında geçen sayısal sonuçları (lab değerleri, iyileşme süreleri, evre \
  bilgisi) ayrı, atomik iddialar olarak çıkar — bunlar genelde tanı veya tedavi \
  kategorisindedir; aynı vaka için en fazla 2–3 atomik iddia (sayısal sonuçlar ayrı kalabilir).
- Anlamlı tıbbi içerik varsa çıkar; zaten söylenmiş iddiaları farklı kelimelerle \
  tekrarlama. Aynı mekanizmanın paraphrase varyantlarını birleştir (en fazla 1 iddia).
- Aynı sebze/konu başlığında en fazla 2–3 iddia (mekanizma + doz/uygulama ayrı kalabilir).
- Parça başına en fazla 30 iddia çıkar; aşarsan en düşük öncelikli (genel tekrar, \
  aynı mekanizma paraphrase) olanları bırak.
- Her iddiaya bir kategori ata: tedavi | tanı | doz | önleme | mucize-ürün | mekanizma | diğer
- "diğer" yalnızca hiçbir kategoriye uymayan nötr bilgiler içindir; fizyoloji, patofizyoloji \
  ve biyokimyasal mekanizmalar için "mekanizma" kullan (potasyum birikimi, miyelin hasarı vb.).
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
Sen bir tıbbi iddia doğrulama asistanısın. Sana bir iddia ve varsa bir retrieval \
kanıt paketi verilecek.

Kurallar:
- Kaynak göstermeden asla "yanlış" veya "doğru" deme.
- ÖNCE paketteki parçalara bak. İddianın SPESİFİK önermesini doğrudan ele alan \
  bir parça varsa source_url olarak PAKETTEKİ url'yi kullan. Genel konu sayfası \
  ("Diabetes nedir", genel Wikipedia) iddiayı ele almış sayılmaz.
- Paket boşsa, yetersizse veya ilgisizse web_search ile ek kaynak ara.
- source_url, iddianın SPESİFİK önermesini doğrudan ele alan sayfa olmalı. \
  Genel Wikipedia/ansiklopedi sayfası, konuyla ilgili blog veya dolaylı bir \
  derleme YETERLİ KAYNAK DEĞİLDİR — bunlarla en fazla "belirsiz" veya \
  "tartışmalı" de ve confidence ≤ 0.45.
- Kaynak iddiayı DESTEKLIYORSA "yanlış" deme. Yönü doğru ama abartılı/mekanizma \
  spekülatifse "tartışmalı" de. (Örnek: CKD diyetinde sebzeyi haşlayıp suyunu \
  dökme / leaching tavsiyesi yerleşiktir; haşlama suyunu içmeyin diyen bir \
  iddiayı, kidney.org gibi bir kaynak bunu doğruluyorsa "yanlış" işaretleme.)
- Bileşik iddialar (örn. "X hem A hem B etkisi yapar"): bileşenlerden biri \
  güçlü kanıtla desteklenirken diğeri desteklenmiyor/zayıf kanıtlıysa \
  final_verdict="tartışmalı" ver. TÜM bileşenler aynı yönde (hepsi destekli \
  VEYA hepsi çürütülmüş) değilse asla "doğrulanmış" veya "yanlış" verme. \
  Kullanıcı paketinde "Bileşen kanıt haritası" varsa o kademeleri kullan; \
  yoksa bileşenleri kendin ayır.
- Besin miktarı iddialarında Wikipedia değil USDA / ulusal gıda bileşimi \
  tablosu kullan. Karşılaştırmalı iddia ("X, Y'den düşük potasyum") ancak \
  her iki besinin değeri kaynakta varsa "doğrulanmış/yanlış" olabilir.
- "tartışmalı" için 0.55 (veya 0.50/0.60) varsayılanını KULLANMA. Kanıt ne \
  kadar karışıksa 0.30–0.70 arasında gerekçeye bağlı gerçek bir değer seç. \
  Emin değilsen "belirsiz" + düşük confidence (≤ 0.35) kullan — zorla bir \
  kutuya sıkıştırma.
- confidence ≥ 0.75 SADECE kaynak kılavuz / sistematik derleme / besin \
  veritabanı VE source_directness=direct ise.
- reasoning, bu iddiaya özgü olsun (şablon cümle yok). Hangi cümle/sayı \
  kaynağın iddiayı nasıl ele aldığını yaz.
- SADECE JSON döndür. cite_source alanını SEN YAZMA — sunucu atar.

Çıktı şeması:
{
  "final_verdict": "doğrulanmış|yanlış|tartışmalı|belirsiz",
  "confidence": 0.0-1.0,
  "reasoning": "2-4 cümle: kanıt, kaynak iddiayı nasıl ele alıyor, neden bu etiket",
  "source_url": "en güçlü kaynağın URL'si",
  "source_directness": "direct|indirect|unrelated",
  "evidence_stance": "supports|contradicts|mixed|insufficient",
  "source_tier": "guideline|systematic_review|primary_study|nutrition_db|encyclopedia|other"
}
"""


def _save_chunk_artifacts(video_id: str | None, artifacts: list[dict]) -> None:
    if not SAVE_EXTRACTION_CHUNKS or not video_id:
        return
    EXTRACTION_CHUNKS_DIR.mkdir(parents=True, exist_ok=True)
    path = EXTRACTION_CHUNKS_DIR / f"{video_id}.json"
    path.write_text(json.dumps({"video_id": video_id, "chunks": artifacts}, ensure_ascii=False, indent=2), encoding="utf-8")


def extract_claims(transcript: str, *, video_id: str | None = None) -> tuple[list[dict], bool]:
    """
    Bir video transkriptinden atomik iddiaları çıkarır.

    Dönüş: (claims, success). success=False → JSON parse tamamen başarısız;
    video claims_extracted_at ile işaretlenmemeli (pipeline tekrar denesin).

    Uzun transkriptler [Ns] sınırlarında parçalanır; her parça ayrı API çağrısı
    alır, sonuçlar chunk-local + window + recap dedup ile birleştirilir.
    """
    chunks = _split_transcript_chunks(transcript)
    if len(chunks) > 1:
        print(f"  [claude] transkript {len(transcript)} karakter → {len(chunks)} parça")
    chunk_lists: list[dict] = []
    chunk_artifacts: list[dict] = []
    any_ok = False
    for i, chunk in enumerate(chunks, 1):
        recap = _is_recap_chunk(chunk, is_last=(i == len(chunks)))
        if recap:
            print(f"  [claude] parça {i}/{len(chunks)}: özet/kapanış modu")
        claims, ok, raw = _extract_claims_once(chunk, recap_hint=recap)
        if ok:
            any_ok = True
            local = dedupe_claims_local(claims)
            chunk_lists.append({"chunk_index": i, "is_recap": recap, "claims": claims})
            chunk_artifacts.append({
                "chunk_index": i,
                "is_recap": recap,
                "raw_count": len(claims),
                "local_dedup_count": len(local),
                "claims": claims,
            })
            if len(chunks) > 1:
                print(f"  [claude] parça {i}/{len(chunks)}: {len(claims)} iddia")
        else:
            print(f"[claude] JSON parse hatası (parça {i}/{len(chunks)}). Ham çıktı: {raw[:300]}")
    merged = dedupe_pipeline(chunk_lists) if chunk_lists else []
    _save_chunk_artifacts(video_id, chunk_artifacts)
    # Tüm parçalar başarısızsa retry; kısmi başarı kabul (en az bir parça OK)
    success = any_ok or len(chunks) == 0
    return merged, success


SUPPORTIVE_PACKAGE_NOTE = (
    "Bu paket iddiayla orta güvenle ilgili kanıt içeriyor. Paketi kullan; "
    "yalnızca paket AÇIKÇA yetersizse veya iddiayı hiç ele almıyorsa ek arama yap."
)
NO_DIRECT_EVIDENCE_NOTE = (
    "Bu iddia için pakette muhtemelen doğrudan kanıt yok. "
    "'belirsiz' olarak değerlendirmeyi düşün; gereksiz yere aramaya devam etme."
)
JSON_RETRY_USER_SUFFIX = (
    "\n\n[JSON RETRY] Yalnızca geçerli JSON döndür; açıklama metni veya markdown "
    "code fence (```) ekleme. reasoning içindeki çift tırnakları \\\" ile escape et."
)

ESCALATE_MAX_TOKENS = 2000
VALID_ESCALATE_VERDICTS = frozenset({"doğrulanmış", "yanlış", "tartışmalı", "belirsiz"})
REQUIRED_ESCALATE_FIELDS = (
    "final_verdict", "confidence", "reasoning", "source_url",
    "source_directness", "evidence_stance", "source_tier",
)


def _escalate_user_notes(
    specificity_tier: str | None = None,
    epistemic_class: str | None = None,
) -> str:
    notes: list[str] = []
    if specificity_tier == "supportive":
        notes.append(SUPPORTIVE_PACKAGE_NOTE)
    if epistemic_class == "no_direct_evidence_expected":
        notes.append(NO_DIRECT_EVIDENCE_NOTE)
    if not notes:
        return ""
    return "\n\n" + "\n".join(notes)


def _format_component_map_note(component_evidence_map: dict | None) -> str:
    comps = (component_evidence_map or {}).get("components") or []
    if len(comps) < 2:
        return ""
    labels = "ABCDEFGHIJ"
    lines = ["", "Bileşen kanıt haritası (aynı paket, yeni arama yok):"]
    for i, row in enumerate(comps):
        letter = labels[i] if i < len(labels) else str(i + 1)
        text = (row.get("text") or "").strip()
        if len(text) > 180:
            text = text[:180] + "…"
        tier = row.get("tier") or "none"
        kept = row.get("kept", 0)
        lines.append(f'- [{letter}] "{text}" → tier={tier} (kept={kept})')
    lines.append(
        "Bileşenler farklı kademedeyse mevcut bileşik-iddia kuralına uy "
        "(final_verdict=tartışmalı; tümü aynı yönde değilse doğrulanmış/yanlış verme)."
    )
    return "\n".join(lines)


def _format_evidence_package(
    claim_text: str,
    evidence: list[dict],
    component_evidence_map: dict | None = None,
) -> str:
    lines = [
        f"İddia: {claim_text}",
        "",
        "Retrieval kanıt paketi (PubMed / Europe PMC / MedlinePlus / Serper). "
        "Önce bunlara bak; iddiayı doğrudan ele alan parça varsa source_url "
        "olarak o parçanın url'sini yaz. Yetersiz/ilgisizse web_search yap. "
        "evidence_content_type=search_snippet başlık+özet kırıntısıdır, tam abstract değildir.",
        "",
    ]
    for i, item in enumerate(evidence, 1):
        title = (item.get("title") or "").strip()
        url = (item.get("url") or "").strip()
        abstract = (item.get("abstract") or "").strip()[:900]
        lines.append(f"[{i}] {title}")
        lines.append(f"    url: {url}")
        r_tier = (item.get("retrieval_tier") or "").strip()
        if r_tier:
            lines.append(f"    retrieval_tier: {r_tier}")
        content_type = (item.get("evidence_content_type") or "").strip()
        if content_type:
            lines.append(f"    content_type: {content_type}")
        doi = (item.get("doi") or "").strip()
        if doi:
            lines.append(f"    doi: {doi}")
        pmcid = (item.get("pmcid") or "").strip()
        if pmcid:
            lines.append(f"    pmcid: {pmcid}")
        extras = item.get("extra_urls") or item.get("publisher_urls") or []
        if isinstance(extras, str):
            extras = [extras]
        seen = {url}
        for extra in extras:
            extra = (extra or "").strip()
            if extra and extra not in seen:
                seen.add(extra)
                lines.append(f"    alt_url: {extra}")
        if item.get("weak_key_term_match"):
            lines.append(
                "    not: zayıf alaka (key-term eşleşmedi; cosine-only fallback)"
            )
        if abstract:
            lines.append(f"    {abstract}")
        lines.append("")
    body = "\n".join(lines).strip()
    return body + _format_component_map_note(component_evidence_map)


def classify_parse_failure(
    full_text: str,
    stop_reason: str | None,
    parsed: dict | None,
) -> tuple[str | None, str | None]:
    """
    Parse başarısızlık kategorisi. (None, None) = başarılı parse + şema OK.
    Kategoriler: truncated, invalid_json, schema_validation, missing_field,
    wrong_enum, unknown.
    """
    if parsed is not None:
        missing = [
            f for f in REQUIRED_ESCALATE_FIELDS
            if f not in parsed or parsed.get(f) is None
        ]
        if missing:
            return "missing_field", f"missing or empty: {missing}"
        verdict = parsed.get("final_verdict")
        if verdict not in VALID_ESCALATE_VERDICTS:
            return "wrong_enum", f"final_verdict={verdict!r}"
        try:
            conf = float(parsed.get("confidence"))
            if not (0.0 <= conf <= 1.0):
                return "schema_validation", f"confidence out of range: {conf}"
        except (TypeError, ValueError):
            return "schema_validation", f"confidence not numeric: {parsed.get('confidence')!r}"
        return None, None

    if stop_reason == "max_tokens":
        return "truncated", "stop_reason=max_tokens"

    text = (full_text or "").strip()
    if not text:
        return "missing_field", "empty response"
    if "{" not in text:
        return "invalid_json", "no JSON object in response"

    stripped = text.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start == -1:
        return "invalid_json", "no opening brace"
    chunk = stripped[start:end + 1] if end != -1 else stripped[start:]
    try:
        json.loads(chunk)
    except json.JSONDecodeError as e:
        if stop_reason == "max_tokens" or end == -1:
            return "truncated", str(e)
        return "invalid_json", str(e)
    return "unknown", "extract_json failed but json.loads succeeded"


def _merge_usage(a: dict, b: dict) -> dict:
    keys = ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")
    return {k: int(a.get(k) or 0) + int(b.get(k) or 0) for k in keys}


def build_escalate_params(
    claim_text: str,
    evidence: list[dict] | None = None,
    *,
    force_package_only: bool = False,
    specificity_tier: str | None = None,
    epistemic_class: str | None = None,
    json_retry: bool = False,
    component_evidence_map: dict | None = None,
) -> dict:
    """Messages API params — senkron ve Batch aynı gövdeyi kullanır."""
    package = list(evidence or [])[:5]
    if package:
        user_content = _format_evidence_package(
            claim_text, package, component_evidence_map,
        )
    else:
        user_content = f"İddiayı değerlendir: {claim_text}"
        user_content += _format_component_map_note(component_evidence_map)
    user_content += _escalate_user_notes(specificity_tier, epistemic_class)
    if json_retry:
        user_content += JSON_RETRY_USER_SUFFIX
    params: dict = {
        "model": MODEL,
        "max_tokens": ESCALATE_MAX_TOKENS,
        "system": _cached_system(FACTCHECK_ESCALATION_SYSTEM),
        "messages": [{"role": "user", "content": user_content}],
        "thinking": {"type": "disabled"},
    }
    if not force_package_only:
        params["tools"] = [WEB_SEARCH_TOOL]
    return params


def build_batch_request(
    claim_id: int | str,
    claim_text: str,
    evidence: list[dict] | None = None,
    *,
    force_package_only: bool = False,
    specificity_tier: str | None = None,
    epistemic_class: str | None = None,
    component_evidence_map: dict | None = None,
) -> dict:
    """Anthropic Message Batches öğesi: {custom_id, params}."""
    custom_id = str(claim_id)
    if not BATCH_CUSTOM_ID_RE.fullmatch(custom_id):
        raise ValueError(f"batch custom_id geçersiz: {custom_id!r}")
    return {
        "custom_id": custom_id,
        "params": build_escalate_params(
            claim_text, evidence, force_package_only=force_package_only,
            specificity_tier=specificity_tier, epistemic_class=epistemic_class,
            component_evidence_map=component_evidence_map,
        ),
    }


def _content_text(message) -> str:
    content = getattr(message, "content", None)
    if content is None and isinstance(message, dict):
        content = message.get("content") or []
    parts = []
    for block in content or []:
        text = getattr(block, "text", None)
        if text is None and isinstance(block, dict):
            text = block.get("text")
        if text:
            parts.append(text)
    return "\n".join(parts).strip()


def parse_escalate_response(resp, *, max_tokens: int | None = None) -> dict:
    """
    Messages API (senkron veya batch succeeded.message) → factcheck JSON.
    cite_source LLM'den okunmaz. Parse meta alanları debug log için eklenir.
    """
    full_text = _content_text(resp)
    stop_reason = getattr(resp, "stop_reason", None)
    parsed = _extract_json(full_text)
    category, err = classify_parse_failure(full_text, stop_reason, parsed)
    meta = {
        "stop_reason": stop_reason,
        "max_tokens": max_tokens,
        "raw_output_last_200": full_text[-200:] if full_text else "",
    }
    if category:
        meta["parse_failure_category"] = category
        meta["parse_error"] = err
        meta["raw_output_on_fail"] = full_text
        print(
            f"[claude] escalate_factcheck JSON parse hatası ({category}): {err}. "
            f"Ham: {full_text[:300]}"
        )
        return {
            "final_verdict": None,
            "confidence": None,
            "reasoning": "LLM çıktısı parse edilemedi — insan gözden geçirmeli",
            "source_url": "",
            "parse_failed": True,
            **meta,
        }
    parsed.pop("cite_source", None)
    return {**parsed, **meta}


def escalate_with_parse_retry(
    *,
    message=None,
    claim_text: str,
    evidence: list[dict] | None = None,
    force_package_only: bool = False,
    specificity_tier: str | None = None,
    epistemic_class: str | None = None,
    component_evidence_map: dict | None = None,
) -> tuple[dict, dict]:
    """
    İlk parse başarısızsa aynı kanıt paketiyle temperature=0 JSON-only retry.
    message verilmişse batch sonucu parse edilir; retry senkron API çağrısıdır.
    """
    kw = dict(
        claim_text=claim_text,
        evidence=evidence,
        force_package_only=force_package_only,
        specificity_tier=specificity_tier,
        epistemic_class=epistemic_class,
        component_evidence_map=component_evidence_map,
    )
    params = build_escalate_params(**kw)
    max_tokens = params["max_tokens"]
    if message is not None:
        resp = message
        usage = _usage_dict(getattr(message, "usage", None))
    else:
        resp = _call_with_retry(**params)
        usage = _usage_dict(getattr(resp, "usage", None))

    result = parse_escalate_response(resp, max_tokens=max_tokens)
    if not result.get("parse_failed"):
        return result, usage

    first_category = result.get("parse_failure_category")
    print(
        f"[claude] parse retry (category={first_category}, "
        f"package_only={force_package_only})"
    )
    retry_params = build_escalate_params(**kw, json_retry=True)
    retry_resp = _call_with_retry(**retry_params)
    usage = _merge_usage(usage, _usage_dict(getattr(retry_resp, "usage", None)))
    retry_result = parse_escalate_response(
        retry_resp, max_tokens=retry_params["max_tokens"]
    )
    retry_result["parse_retry"] = True
    retry_result["parse_retry_first_category"] = first_category
    retry_result["parse_retry_succeeded"] = not retry_result.get("parse_failed")
    return retry_result, usage


def escalate_factcheck(
    claim_text: str,
    evidence: list[dict] | None = None,
    *,
    force_package_only: bool = False,
    specificity_tier: str | None = None,
    epistemic_class: str | None = None,
    component_evidence_map: dict | None = None,
) -> dict:
    """
    NLI ilk filtresi 'belirsiz'/'düşük güven' dediğinde, ya da initial_risk=high
    olduğunda çağrılır. evidence verilmişse önce o pakete bakması istenir;
    web_search yetersiz/ilgisiz pakette ek kaynak içindir.

    force_package_only=True: bu çağrıda web_search aracı hiç eklenmez
    (yalnızca strong_match paketlerinde). Prompt dili değil, araç yokluğu kilidi.

    cite_source LLM JSON'undan okunmaz — çağıran calibrate_factcheck atar.
    """
    result, _ = escalate_with_parse_retry(
        message=None,
        claim_text=claim_text,
        evidence=evidence,
        force_package_only=force_package_only,
        specificity_tier=specificity_tier,
        epistemic_class=epistemic_class,
        component_evidence_map=component_evidence_map,
    )
    return result


def submit_message_batch(requests: list[dict]):
    """Message Batches API: requests = [{custom_id, params}, ...]."""
    return client.messages.batches.create(requests=requests)


def retrieve_message_batch(batch_id: str):
    return client.messages.batches.retrieve(batch_id)


def iter_batch_results(batch_id: str):
    return client.messages.batches.results(batch_id)
