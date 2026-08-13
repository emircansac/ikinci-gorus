"""Reasoning metni kalıp denetimleri — hafif modül (numpy yok)."""
import re

# "kısmı" (its part) ≠ "kısmi" (partial): IGNORECASE ı/i katlaması 696'yı yanlış elemişti.
PARTIAL_REASONING_RE = re.compile(
    r"(?-i:[kK]ısmi|[kK]ismi|[kK]ısmen|[kK]ismen)\b|"
    r"bir kısmı (kanıtlan|doğru değil|yanlış)|"
    r"bir kismi (kanitlan|dogru degil|yanlis)|"
    r"kanıtlanmıyor|kanitlanmiyor|kanıtlanmadı|kanitlanmadi|"
    r"abartılı|abartili|genellem|spekülatif|spekulatif|"
    r"ancak iddia.{0,90}(kanıtlanm|kanitlanm|yanlış|yanlis|abart|desteklenmiyor)|"
    r"fakat iddia.{0,90}(kanıtlanm|kanitlanm|yanlış|yanlis|abart|desteklenmiyor)|"
    r"tam örtüş|tam ortus|"
    r"yalnızca .{0,40} doğru|sadece .{0,40} doğru|"
    r"evreye bağlı|porsiyon kontrol|insufficient|mixed stance",
    re.IGNORECASE,
)

# PubMed / Europe PMC özetlerinde kısmi veya bileşik destek ipuçları (İngilizce).
PARTIAL_EVIDENCE_RE_EN = re.compile(
    r"\bhowever\b|"
    r"\balthough\b|"
    r"\bthough\b|"
    r"\b(partial|partially|mixed results|mixed findings|inconclusive)\b|"
    r"\b(insufficient evidence|limited evidence|not enough evidence|lack of evidence|"
    r"no clear evidence|remains unclear|uncertain whether)\b|"
    r"\bhowever.{0,100}(not|no|cannot|limited|unclear|insufficient|contradict|fail)\b|"
    r"\b(does not|do not|did not) (support|confirm|establish|demonstrate|prove)\b|"
    r"\bnot (directly )?(support|confirm|establish|demonstrate|address|evaluate)\b|"
    r"\b(pilot study|pilot trial|preliminary (study|data|results))\b|"
    r"\b(further|additional|more) (research|studies|investigation) (is )?(needed|required|warranted)\b",
    re.IGNORECASE,
)


def evidence_has_partial_caveat(text: str | None) -> bool:
    """Kanıt metninde kısmi/bileşik destek veya yeterlilik uyarısı var mı?"""
    if not text or len(text.strip()) < 10:
        return False
    return bool(PARTIAL_REASONING_RE.search(text) or PARTIAL_EVIDENCE_RE_EN.search(text))
