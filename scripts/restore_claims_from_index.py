"""claim_index.csv'den silinmiş iddiaları DB'ye geri yükler (API hatası sonrası)."""
import csv
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent))
from utils.db import get_conn

VIDEO_ID = "odZgEDFDmbE"


def main():
    conn = get_conn()
    rows = [r for r in csv.DictReader(open("data/claim_index.csv", encoding="utf-8"))
            if r["video_id"] == VIDEO_ID]
    if not rows:
        print(f"[restore] {VIDEO_ID} claim_index'te bulunamadı")
        return

    existing = conn.execute("SELECT COUNT(*) FROM claims WHERE video_id=?", (VIDEO_ID,)).fetchone()[0]
    if existing:
        print(f"[restore] {VIDEO_ID} zaten {existing} iddia var — atlandı")
        conn.close()
        return

    ch = rows[0]["channel_id"]
    for r in rows:
        conn.execute("""
            INSERT INTO claims (video_id, channel_id, timestamp_sec, claim_text, category, initial_risk)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (VIDEO_ID, ch, r.get("timestamp_sec") or None, r["claim_text"],
              r["category"], r["initial_risk"]))
    conn.execute(
        "UPDATE videos SET claims_extracted_at = datetime('now') WHERE video_id = ?", (VIDEO_ID,))
    conn.commit()
    conn.close()
    print(f"[restore] {VIDEO_ID}: {len(rows)} iddia geri yüklendi")


if __name__ == "__main__":
    main()
