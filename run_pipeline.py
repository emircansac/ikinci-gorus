"""
Tüm pipeline'ı sırayla çalıştırır: Topla -> İddia çıkar -> Fact-check -> Skorla

Kullanım:
    python run_pipeline.py --channels data/channels.csv --max-videos 15

Not: Her aşama idempotent'tir (zaten işlenmiş kayıtları atlar), bu yüzden
cron/GitHub Actions ile periyodik olarak tekrar tekrar çalıştırabilirsiniz.
"""
import argparse
import subprocess
import sys
from pathlib import Path

CORE_STEPS = [
    ("01_collect.py", ["--channels", "{channels}", "--max-videos", "{max_videos}"]),
    ("02_extract_claims.py", []),
    ("03_factcheck.py", []),
]
SCORE_STEP = ("04_score_suspects.py", ["--export", "{export}"])
COMMENT_STEP = ("05_comment_authenticity.py", [])
INDEX_STEP = ("06_claim_index.py", ["--export-dir", "{export_dir}"])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--channels", default="data/channels.csv")
    ap.add_argument("--watchlist", action="store_true",
                    help="01_collect için data/watchlist.json kullan (kanal abonelikleri + tekil videolar)")
    ap.add_argument("--max-videos", default="15")
    ap.add_argument("--export", default="data/suspects.csv")
    ap.add_argument("--skip-collect", action="store_true", help="sadece mevcut DB üzerinde 2-4. aşamaları çalıştır")
    ap.add_argument("--skip-nli", action="store_true",
                    help="03_factcheck için HF NLI atlanır (torch/transformers kurulu değilse)")
    ap.add_argument("--with-comments", action="store_true",
                    help="Aşama 5'i (yorum bot analizi) de çalıştır — en çok kota tüketen aşama, "
                         "günlük değil haftalık önerilir")
    args = ap.parse_args()

    # Sıra: [01-03] -> (varsa yorum analizi, bot_comment_ratio'yu skora yansıtabilsin diye
    # 04'TEN ÖNCE) -> 04 (skorlama) -> 06 (iddia indeksi, sadece verdicts'i okur, ekstra API
    # çağrısı yok, her zaman çalışır)
    steps = list(CORE_STEPS) if not args.skip_collect else list(CORE_STEPS[1:])
    if args.with_comments:
        steps.append(COMMENT_STEP)
    steps.append(SCORE_STEP)
    steps.append(INDEX_STEP)

    for script, arg_template in steps:
        cmd = [sys.executable, f"pipeline/{script}"] + [
            a.format(channels=args.channels, max_videos=args.max_videos, export=args.export,
                     export_dir=str(Path(args.export).parent))
            for a in arg_template
        ]
        if script == "01_collect.py" and args.watchlist:
            cmd.append("--watchlist")
        if script == "03_factcheck.py" and args.skip_nli:
            cmd.append("--skip-nli")
        print(f"\n{'='*60}\n▶ {' '.join(cmd)}\n{'='*60}")
        result = subprocess.run(cmd)
        if result.returncode != 0:
            print(f"!! {script} hata verdi, pipeline durduruldu.")
            sys.exit(1)

    print("\n✅ Pipeline tamamlandı. data/suspects.csv dosyasını inceleyin.")


if __name__ == "__main__":
    main()
