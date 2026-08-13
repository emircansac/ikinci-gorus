"""
Doğrulanmış iddia kütüphanesini DB'ye seed eder.

Kullanım:
    ./venv/bin/python pipeline/11_seed_claim_library.py
    ./venv/bin/python pipeline/11_seed_claim_library.py --video-id odZgEDFDmbE
    ./venv/bin/python pipeline/11_seed_claim_library.py --audit
"""
import argparse
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent))
from utils.db import get_conn
from utils.claim_library import seed_from_verdicts, library_stats, purge_ineligible_entries


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--video-id", default=None)
    ap.add_argument("--min-confidence", type=float, default=0.65)
    ap.add_argument("--audit", action="store_true",
                    help="blocklist + kısmi iddiaları sil, sonra yeniden seed et")
    args = ap.parse_args()

    conn = get_conn()
    if args.audit:
        purged = purge_ineligible_entries(conn)
        print(f"[claim_library] audit: cikarilan origin_ids={purged['removed_origin_ids']} "
              f"kalan={purged['remaining']}")

    stats = seed_from_verdicts(conn, video_id=args.video_id, min_confidence=args.min_confidence)
    lib = library_stats(conn)
    conn.close()

    print(f"[claim_library] eklendi={stats['added']} atlandi={stats['skipped']} "
          f"kismi_reddedildi={stats.get('rejected_partial', 0)} toplam={lib['total']}")
    print(f"[claim_library] dagilim: {lib['by_verdict']}")
    print(f"[claim_library] origin_ids: {lib.get('origin_ids', [])}")


if __name__ == "__main__":
    main()
