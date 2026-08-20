"""Fact-check senkron/batch otomatik seçimi ve kullanıcı bekleme mesajları."""
from __future__ import annotations

# Eşik: senkron yalnızca ikisi birden sağlanınca (AND). Kolayca değişir.
FACTCHECK_SYNC_MAX_VIDEOS = 5
FACTCHECK_SYNC_MAX_CLAIMS = 200
FACTCHECK_AVG_SECONDS_PER_CLAIM = 20

BATCH_ID_PENDING = "(gönderim sonrası yazılacak)"
BATCH_RETRIEVE_CMD = "python pipeline/03_factcheck.py --batch-retrieve"


def estimated_minutes_for_claims(n_claims: int) -> int:
    n = max(0, int(n_claims))
    return max(1, round(n * FACTCHECK_AVG_SECONDS_PER_CLAIM / 60))


def choose_factcheck_method(n_claims: int, n_videos: int | None = None) -> str:
    """Senkron: n_videos<=5 VE n_claims<=200. n_videos yoksa yalnız iddia eşiği."""
    n = int(n_claims)
    if n_videos is None:
        return "sync" if n <= FACTCHECK_SYNC_MAX_CLAIMS else "batch"
    if int(n_videos) <= FACTCHECK_SYNC_MAX_VIDEOS and n <= FACTCHECK_SYNC_MAX_CLAIMS:
        return "sync"
    return "batch"


def format_factcheck_user_message(
    method: str,
    n_claims: int,
    *,
    estimated_minutes: int | None = None,
    batch_id: str | None = None,
) -> str:
    n = int(n_claims)
    if method == "sync":
        m = estimated_minutes if estimated_minutes is not None else estimated_minutes_for_claims(n)
        return (
            f"{n} iddia işleniyor, tahmini süre: ~{m} dakika. Sonuç bu ekranda "
            "görünecek, lütfen bekleyin."
        )
    bid = batch_id if batch_id else BATCH_ID_PENDING
    return (
        f"{n} iddia toplu işleme kuyruğuna alındı. Anthropic'in işleme süresi "
        "garantisi yok — genelde birkaç dakika-birkaç saat içinde biter, ama "
        "teoride 24 saate kadar sürebilir. Otomatik bildirim GELMEZ, kontrol "
        f"etmeniz gerekir:\n  {BATCH_RETRIEVE_CMD}\n"
        "Gece gönderdiyseniz, ertesi gün kontrol etmenizi öneririz. Batch ID:\n"
        f"{bid} (bu ID'yi kaybetmeyin, durumu bununla sorgulayabilirsiniz)."
    )


def build_factcheck_dispatch(
    *,
    n_claims: int,
    n_videos: int | None = None,
    batch_id: str | None = None,
    method: str | None = None,
) -> dict:
    n = int(n_claims)
    chosen = method or choose_factcheck_method(n, n_videos)
    estimated = estimated_minutes_for_claims(n) if chosen == "sync" else None
    return {
        "method": chosen,
        "n_claims": n,
        "n_videos": n_videos,
        "threshold_videos": FACTCHECK_SYNC_MAX_VIDEOS,
        "threshold_claims": FACTCHECK_SYNC_MAX_CLAIMS,
        "estimated_minutes": estimated,
        "batch_id": batch_id,
        "user_message": format_factcheck_user_message(
            chosen,
            n,
            estimated_minutes=estimated,
            batch_id=batch_id,
        ),
    }
