"""Video extraction/dedup pipeline durumu — chunk artifact varlığına göre."""
import json
from pathlib import Path

ROOT = Path(__file__).parent.parent
CHUNK_DIR = ROOT / "data" / "extraction_chunks"


def offline_dedup_path(video_id: str) -> Path | None:
    p = ROOT / "data" / f"smoke_{video_id}" / "offline_dedup.json"
    return p if p.is_file() else None


def has_full_dedup_pipeline(video_id: str) -> bool:
    """
    Tam extraction+dedup turu koşulmuş mu?
    Kısmi measurement örneklemi (birkaç claim_id) bu sayılmaz —
    chunk artifact veya smoke offline_dedup gerekir.
    """
    if offline_dedup_path(video_id):
        return True
    chunk_path = CHUNK_DIR / f"{video_id}.json"
    if not chunk_path.is_file():
        return False
    data = json.loads(chunk_path.read_text(encoding="utf-8"))
    chunks = data.get("chunks") or []
    return bool(chunks) and any("raw_count" in c for c in chunks)
