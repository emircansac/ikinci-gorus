#!/usr/bin/env python3
"""DEMO_* kanal/video/iddia verisini SQLite'tan temizler."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from utils.db import get_conn

DEMO_CHANNEL = "channel_id LIKE 'DEMO_%'"
DEMO_VIDEO = "video_id LIKE 'DEMO_%'"
DEMO_CLAIM = f"channel_id LIKE 'DEMO_%' OR video_id LIKE 'DEMO_%'"


def main():
    conn = get_conn()
    cur = conn.cursor()

    claim_ids = [r[0] for r in cur.execute(
        f"SELECT claim_id FROM claims WHERE {DEMO_CLAIM}"
    ).fetchall()]
    video_ids = [r[0] for r in cur.execute(
        f"SELECT video_id FROM videos WHERE {DEMO_VIDEO} OR {DEMO_CHANNEL}"
    ).fetchall()]
    channel_ids = [r[0] for r in cur.execute(
        f"SELECT channel_id FROM channels WHERE {DEMO_CHANNEL}"
    ).fetchall()]

    if claim_ids:
        cur.executemany("DELETE FROM verdicts WHERE claim_id = ?", [(i,) for i in claim_ids])
    cur.execute(f"DELETE FROM claims WHERE {DEMO_CLAIM}")
    cur.execute(f"DELETE FROM comments WHERE {DEMO_VIDEO} OR {DEMO_CHANNEL}")
    cur.execute(f"DELETE FROM videos WHERE {DEMO_VIDEO} OR {DEMO_CHANNEL}")
    cur.execute(f"DELETE FROM channel_snapshots WHERE {DEMO_CHANNEL}")
    cur.execute(f"DELETE FROM channel_risk_scores WHERE {DEMO_CHANNEL}")
    cur.execute(f"DELETE FROM channels WHERE {DEMO_CHANNEL}")
    conn.commit()
    conn.close()

    print(f"✓ demo temizlendi: {len(channel_ids)} kanal, {len(video_ids)} video, {len(claim_ids)} iddia")


if __name__ == "__main__":
    main()
