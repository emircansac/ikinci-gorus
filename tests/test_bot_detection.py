import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from utils.bot_detection import (
    _is_generic_praise,
    find_burst_windows,
    find_duplicate_clusters,
    score_comments,
)


def test_generic_praise_detected():
    assert _is_generic_praise("Çok faydalı bilgi teşekkürler doktor") is True
    assert _is_generic_praise("Perine bölgesinde pudendal sinir sıkışması anlatımı") is False


def test_duplicate_cluster_detection():
    comments = [
        {"comment_id": "1", "text": "çok faydalı bilgi teşekkürler", "published_at": "2026-01-01T10:00:00Z", "author_channel_id": "a"},
        {"comment_id": "2", "text": "çok faydalı bilgi teşekkürler", "published_at": "2026-01-01T10:01:00Z", "author_channel_id": "b"},
    ]
    clusters = find_duplicate_clusters(comments)
    assert clusters["1"] == 2
    assert clusters["2"] == 2


def test_burst_only_flags_duplicate_cluster_members():
    now = datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)
    comments = []
    for i in range(6):
        comments.append({
            "comment_id": f"dup-{i}",
            "text": "harika bir video teşekkürler",
            "published_at": (now + timedelta(seconds=i * 30)).isoformat().replace("+00:00", "Z"),
            "author_channel_id": f"user-{i}",
        })
    comments.append({
        "comment_id": "unique-1",
        "text": "bu videodaki pudendal sinir açıklaması çok netti",
        "published_at": (now + timedelta(seconds=30)).isoformat().replace("+00:00", "Z"),
        "author_channel_id": "real-user",
    })
    dup = find_duplicate_clusters(comments)
    burst_ids = find_burst_windows(comments, dup)
    assert "unique-1" not in burst_ids
    assert len(burst_ids) >= 5


def test_score_comments_assigns_flags():
    comments = [
        {"comment_id": "1", "text": "teşekkürler doktor", "published_at": "2026-01-01T10:00:00Z", "author_channel_id": "a"},
    ]
    scored = score_comments(comments, {})
    assert scored[0]["bot_score"] >= 15
    assert "generic" in scored[0]["bot_flags"]
