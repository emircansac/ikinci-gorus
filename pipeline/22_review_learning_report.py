"""
İnceleme öğrenme özeti — review_outcomes SQL agregasyonu.

Davranış veya eşik değiştirmez. Yalnızca biriken approve/reject kayıtlarını okur.

Kullanım:
    python pipeline/22_review_learning_report.py
    python pipeline/22_review_learning_report.py --out data/ops_reports/review-learning.md
"""
from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent))

from utils.db import get_conn

ROOT = Path(__file__).parent.parent
OUT_DIR = ROOT / "data" / "ops_reports"

CONF_BAND_SQL = """
CASE
    WHEN ai_confidence IS NULL THEN '(yok)'
    WHEN ai_confidence < 0.50 THEN '<0.50'
    WHEN ai_confidence < 0.60 THEN '[0.50,0.60)'
    WHEN ai_confidence < 0.70 THEN '[0.60,0.70)'
    WHEN ai_confidence < 0.80 THEN '[0.70,0.80)'
    WHEN ai_confidence < 0.90 THEN '[0.80,0.90)'
    ELSE '≥0.90'
END
"""


def _rate(disagreed: int, n: int) -> str:
    if n <= 0:
        return "—"
    return f"{100.0 * disagreed / n:.1f}%"


def fetch_stats(conn) -> dict:
    exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='review_outcomes'"
    ).fetchone()
    if not exists:
        return {"n": 0, "n_agreed": 0, "n_disagreed": 0, "by_category": [],
                "by_tier": [], "by_conf": [], "patterns": []}

    totals = conn.execute("""
        SELECT COUNT(*) AS n,
               COALESCE(SUM(agreed), 0) AS n_agreed,
               COALESCE(SUM(CASE WHEN agreed = 0 THEN 1 ELSE 0 END), 0) AS n_disagreed
        FROM review_outcomes
    """).fetchone()
    n = int(totals["n"] or 0)
    n_agreed = int(totals["n_agreed"] or 0)
    n_disagreed = int(totals["n_disagreed"] or 0)

    def group(sql: str) -> list[dict]:
        return [dict(r) for r in conn.execute(sql).fetchall()]

    by_category = group("""
        SELECT COALESCE(reviewer_check_point_category, '(yok)') AS key,
               COUNT(*) AS n,
               SUM(CASE WHEN agreed = 0 THEN 1 ELSE 0 END) AS n_disagreed
        FROM review_outcomes
        GROUP BY 1
        ORDER BY n_disagreed DESC, n DESC
    """)
    by_tier = group("""
        SELECT COALESCE(specificity_tier_at_review, '(yok)') AS key,
               COUNT(*) AS n,
               SUM(CASE WHEN agreed = 0 THEN 1 ELSE 0 END) AS n_disagreed
        FROM review_outcomes
        GROUP BY 1
        ORDER BY n_disagreed DESC, n DESC
    """)
    by_conf = group(f"""
        SELECT {CONF_BAND_SQL} AS key,
               COUNT(*) AS n,
               SUM(CASE WHEN agreed = 0 THEN 1 ELSE 0 END) AS n_disagreed
        FROM review_outcomes
        GROUP BY 1
        ORDER BY n_disagreed DESC, n DESC
    """)
    patterns = group("""
        SELECT ai_verdict || '→' || human_verdict AS key,
               COUNT(*) AS n,
               SUM(CASE WHEN agreed = 0 THEN 1 ELSE 0 END) AS n_disagreed
        FROM review_outcomes
        WHERE ai_verdict IS NOT NULL AND human_verdict IS NOT NULL
        GROUP BY 1
        ORDER BY n_disagreed DESC, n DESC
    """)
    return {
        "n": n,
        "n_agreed": n_agreed,
        "n_disagreed": n_disagreed,
        "by_category": by_category,
        "by_tier": by_tier,
        "by_conf": by_conf,
        "patterns": patterns,
    }


def render_report(stats: dict) -> str:
    n = stats["n"]
    n_agreed = stats["n_agreed"]
    n_disagreed = stats["n_disagreed"]
    lines = [
        "# İnceleme öğrenme raporu",
        "",
        f"Tarih: {date.today().isoformat()}. Kaynak: `review_outcomes`. "
        "Eşik/model değişikliği yok — yalnızca agregasyon.",
        "",
        "## Özet",
        "",
        f"- Toplam review: **{n}**",
        f"- Agreed: **{n_agreed}** ({_rate(n_agreed, n)})",
        f"- Disagreed: **{n_disagreed}** ({_rate(n_disagreed, n)})",
        "",
    ]
    if n == 0:
        lines += [
            "Henüz kayıt yok. Satırlar yalnızca `review_claim(approve|reject)` "
            "sonrası yazılır (arşivle ve geçmiş doldurma yok).",
            "",
        ]
        return "\n".join(lines)

    def section(title: str, rows: list[dict], key_header: str) -> None:
        lines.append(f"## {title}")
        lines.append("")
        if not rows:
            lines.append("Kayıt yok.")
            lines.append("")
            return
        lines.append(f"| {key_header} | n | disagreed | oran |")
        lines.append("|---|---:|---:|---:|")
        for r in rows:
            nd = int(r["n_disagreed"] or 0)
            nn = int(r["n"] or 0)
            lines.append(f"| {r['key']} | {nn} | {nd} | {_rate(nd, nn)} |")
        lines.append("")

    section("check_point kategorisine göre disagreement", stats["by_category"], "kategori")
    section("specificity_tier'a göre disagreement", stats["by_tier"], "tier")
    section("confidence bandına göre disagreement", stats["by_conf"], "bant")

    lines.append("## AI → insan hüküm kalıpları")
    lines.append("")
    lines.append("Yalnızca her iki hüküm de dolu olan satırlar (`NULL→NULL` yok).")
    lines.append("")
    pats = stats["patterns"]
    if not pats:
        lines.append("Kalıp yok.")
        lines.append("")
    else:
        lines.append("| kalıp | n | disagreed |")
        lines.append("|---|---:|---:|")
        for r in pats:
            lines.append(
                f"| {r['key']} | {int(r['n'])} | {int(r['n_disagreed'] or 0)} |"
            )
        lines.append("")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="", help="Markdown çıktı yolu (boş = stdout)")
    args = ap.parse_args()

    conn = get_conn()
    body = render_report(fetch_stats(conn))
    conn.close()

    if args.out:
        out = Path(args.out)
        if not out.is_absolute():
            out = ROOT / out
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(body, encoding="utf-8")
        print(f"[review_learning] yazıldı -> {out}")
    print(body)


if __name__ == "__main__":
    main()
