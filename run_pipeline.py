"""
Tüm pipeline'ı sırayla çalıştırır: Topla -> İddia çıkar -> Fact-check -> Skorla

Kullanım:
    python run_pipeline.py --channels data/channels.csv --max-videos 15

Not: Her aşama idempotent'tir (zaten işlenmiş kayıtları atlar), bu yüzden
periyodik olarak tekrar tekrar çalıştırabilirsiniz (yerel cron, ya da
app.py içindeki in-process zamanlayıcı).

20_subscribe_channel.py / 21_pre_research_channel.py burada YOKTUR (input()
bekler). Zamanlayıcı / Render yalnız bu dosyayı çağırmalı.
"""
import argparse
import subprocess
import sys
from pathlib import Path

# Yeni iş göndermeden önce: bekleyen batch varsa uygula, yoksa no-op.
# --wait yok — bir tur saatlerce asılı kalmasın; bitmemişse sonraki tur tekrar dener.
RETRIEVE_STEP = ("03_factcheck.py", ["--batch-retrieve"])
CORE_STEPS = [
    ("01_collect.py", ["--channels", "{channels}", "--max-videos", "{max_videos}"]),
    ("02_extract_claims.py", []),
    ("03_factcheck.py", ["--auto-method"]),
]
SCORE_STEP = ("04_score_suspects.py", ["--export", "{export}"])
COMMENT_STEP = ("05_comment_authenticity.py", [])
INDEX_STEP = ("06_claim_index.py", ["--export-dir", "{export_dir}"])


class PipelineStepError(Exception):
    """Bir pipeline adımı sıfır olmayan çıkış kodu verdi. CLI sys.exit(1) yapar;
    zamanlayıcı bunu yakalayıp Flask sürecini ayakta tutar."""


def build_step_plan(skip_collect=False, with_comments=False):
    """retrieve → collect → extract → auto-method → (yorum) → score → index.

    20/21 (interaktif onboarding) bilinçli olarak yok.
    """
    plan = [RETRIEVE_STEP]
    core = list(CORE_STEPS) if not skip_collect else list(CORE_STEPS[1:])
    plan.extend(core)
    if with_comments:
        plan.append(COMMENT_STEP)
    plan.append(SCORE_STEP)
    plan.append(INDEX_STEP)
    return plan


def _run_step(script, arg_template, args):
    cmd = [sys.executable, f"pipeline/{script}"] + [
        a.format(channels=args.channels, max_videos=args.max_videos, export=args.export,
                 export_dir=str(Path(args.export).parent))
        for a in arg_template
    ]
    if script == "01_collect.py" and args.watchlist:
        cmd.append("--watchlist")
    if script == "03_factcheck.py" and args.skip_nli and "--batch-retrieve" not in arg_template:
        cmd.append("--skip-nli")
    print(f"\n{'='*60}\n▶ {' '.join(cmd)}\n{'='*60}")
    result = subprocess.run(cmd)
    if result.returncode != 0:
        raise PipelineStepError(f"{script} hata verdi (exit {result.returncode})")


def run_pipeline(channels="data/channels.csv", max_videos="15", export="data/suspects.csv",
                 skip_collect=False, skip_nli=False, with_comments=False, watchlist=False):
    """retrieve → collect → extract → auto-method zincirini (ve score/index)
    sırayla çalıştır. Adım hatasında PipelineStepError fırlatır; sys.exit YOK —
    Flask zamanlayıcı thread'inden güvenle çağrılabilir."""
    args = argparse.Namespace(
        channels=channels,
        max_videos=str(max_videos),
        export=export,
        skip_collect=skip_collect,
        skip_nli=skip_nli,
        with_comments=with_comments,
        watchlist=watchlist,
    )
    for script, arg_template in build_step_plan(
        skip_collect=skip_collect, with_comments=with_comments
    ):
        _run_step(script, arg_template, args)
    print("\n✅ Pipeline tamamlandı. data/suspects.csv dosyasını inceleyin.")


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

    try:
        run_pipeline(
            channels=args.channels,
            max_videos=args.max_videos,
            export=args.export,
            skip_collect=args.skip_collect,
            skip_nli=args.skip_nli,
            with_comments=args.with_comments,
            watchlist=args.watchlist,
        )
    except PipelineStepError as e:
        print(f"!! {e}, pipeline durduruldu.")
        sys.exit(1)


if __name__ == "__main__":
    main()
