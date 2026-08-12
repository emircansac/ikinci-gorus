import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from utils.watchlist import MIN_VIDEOS_FOR_CHANNEL_SCORE


def test_min_videos_constant():
    assert MIN_VIDEOS_FOR_CHANNEL_SCORE == 5


def test_parse_video_url():
    from utils.watchlist import parse_video_input
    assert parse_video_input("https://www.youtube.com/watch?v=dQw4w9WgXcQ") == "dQw4w9WgXcQ"
    assert parse_video_input("dQw4w9WgXcQ") == "dQw4w9WgXcQ"


def test_parse_channel_id():
    from utils.watchlist import parse_channel_input
    assert parse_channel_input("UCBR8-60-B28hp2BmDPdntcQ") == "UCBR8-60-B28hp2BmDPdntcQ"
