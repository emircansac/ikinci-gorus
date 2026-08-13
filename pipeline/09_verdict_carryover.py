#!/usr/bin/env python3
"""
Arşivlenmiş v1 iddialardaki verdict'leri aktif v2 iddialara taşır.

Kullanım:
    ./venv/bin/python pipeline/09_verdict_carryover.py --video-id P4m9F9mykQ8
"""
import argparse
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent))
from utils.db import get_conn
from utils.verdict_carryover import carryover_verdicts


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--video-id", action="append", dest="video_ids", required=True)
    args = ap.parse_args()

    conn = get_conn()
    for vid in args.video_ids:
        result = carryover_verdicts(conn, vid)
        print(f"{vid}: {result}")
    conn.close()


if __name__ == "__main__":
    main()
