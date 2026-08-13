"""
AŞAMA 3'ün UCUZ İLK FİLTRESİ: Hugging Face NLI modeli.

Neden bu katman var:
  - LLM (Claude) her iddia için web_search + reasoning yapmak pahalı ve yavaş.
  - Binlerce iddia biriktiğinde önce ucuz/yerel bir modelle triyaj yapıp,
    sadece belirsiz/yüksek riskli olanları LLM'e (escalate_factcheck) göndermek gerekir.

Model seçimi: MoritzLaurer/mDeBERTa-v3-base-mnli-xnli
  -> Çok dilli NLI (Türkçe dahil XNLI ile eğitilmiş), sağlık-özel değil ama
     dil bariyerini aşıyor. FEVER/PubHealth tabanlı modeller (Dzeniks/roberta-fact-check,
     Amanpradhan1/health-fact-check-model) yalnızca İngilizce olduğu için
     Türkçe iddialarda önce çeviri gerektirir; bu yüzden varsayılan olarak
     çok dilli modeli öneriyoruz.

NOT: Kanıt getirme (PubMed sorgu + özet çekme + dense rerank) buradan
utils/evidence_retrieval.py'ye taşındı — bu dosya artık sadece sınıflandırma
mantığını içeriyor (tek sorumluluk: NLI burada, kanıt toplama orada).

pip install transformers torch sentencepiece
"""
import os
from functools import lru_cache

from utils.reasoning_patterns import evidence_has_partial_caveat

NLI_MODEL_NAME = os.environ.get("NLI_MODEL", "MoritzLaurer/mDeBERTa-v3-base-mnli-xnli")

# zero-shot-classification pipeline'ının altındaki model genelde ~512 token
# sınırına sahip. Bir PubMed özeti + başlık bunu kolayca aşabilir (özellikle
# birden fazla kanıt birleştirilmişse) — aşarsa sessizce kırpılır ve sonuç
# güvenilmez hale gelir, bu yüzden burada açıkça kırpıyoruz.
MAX_EVIDENCE_CHARS = 1800  # kabaca ~450-500 token


@lru_cache(maxsize=1)
def _get_pipeline():
    from transformers import pipeline
    return pipeline("zero-shot-classification", model=NLI_MODEL_NAME)


def nli_check(claim_text: str, evidence_text: str) -> dict:
    """
    claim_text'in evidence_text tarafından desteklenip desteklenmediğini kabaca kontrol eder.
    Zero-shot-classification'ı entailment ölçümü olarak kullanıyoruz:
    hypothesis = iddia, premise olarak kanıtı candidate_labels ile karşılaştırıyoruz.
    """
    clf = _get_pipeline()
    evidence_text = evidence_text[:MAX_EVIDENCE_CHARS]
    hypothesis_template = "Bu kanıt şu iddiayı {}: " + claim_text
    labels = ["destekliyor", "çürütüyor", "yorum yapmıyor"]
    result = clf(evidence_text, candidate_labels=labels, hypothesis_template=hypothesis_template)
    top_label, top_score = result["labels"][0], result["scores"][0]

    label_map = {
        "destekliyor": "SUPPORTS",
        "çürütüyor": "REFUTES",
        "yorum yapmıyor": "NOT_ENOUGH_INFO",
    }
    return {
        "nli_label": label_map[top_label],
        "nli_confidence": round(float(top_score), 3),
        "raw": result,
    }


def should_escalate(
    nli_result: dict,
    initial_risk: str,
    evidence_text: str | None = None,
    confidence_threshold: float = 0.75,
) -> bool:
    """
    Escalation kuralı (Claude+web_search'e gönderme kararı):
      - initial_risk == "high"  -> HER ZAMAN escalate et (ucuz filtreye güvenme)
      - NLI güveni eşiğin altında -> escalate et
      - NLI 'NOT_ENOUGH_INFO' dediyse -> escalate et
      - SUPPORTS/REFUTES + yüksek güven olsa bile kanıtta kısmi/bileşik uyarı -> escalate et
    """
    if initial_risk == "high":
        return True
    if nli_result["nli_label"] == "NOT_ENOUGH_INFO":
        return True
    if nli_result["nli_confidence"] < confidence_threshold:
        return True
    if (
        evidence_text
        and nli_result["nli_label"] in ("SUPPORTS", "REFUTES")
        and nli_result["nli_confidence"] >= confidence_threshold
        and evidence_has_partial_caveat(evidence_text)
    ):
        return True
    return False
