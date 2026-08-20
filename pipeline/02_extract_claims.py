"""
AŞAMA 2: Transkriptlerden atomik iddiaları çıkarma (Claude API).

Kullanım:
    python pipeline/02_extract_claims.py [--limit 50]
    python pipeline/02_extract_claims.py --channel-id UCxxx [--limit 10000]

Sadece henüz iddia çıkarılmamış (claims tablosunda karşılığı olmayan) videoları işler,
bu yüzden script'i tekrar tekrar çalıştırmak güvenlidir (idempotent).
"""
import argparse
import subprocess
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent))
ROOT = Path(__file__).parent.parent
from utils.db import get_conn
from utils.claude_client import extract_claims
from utils.extraction_store import DEFAULT_EXTRACTION_VERSION, insert_claims_batch


def _refresh_exports():
    """Dashboard CSV'lerini güncelle — iddialar DB'de olsa bile UI claim_index/videos okur."""
    subprocess.run(
        [sys.executable, str(ROOT / "pipeline" / "06_claim_index.py"), "--export-dir", "data"],
        check=False,
    )


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=100)
    ap.add_argument("--channel-id", default=None,
                    help="yalnızca bu kanalın extract edilmemiş videoları")
    ap.add_argument("--retry-empty", action="store_true",
                    help="0 iddia çıkarılmış ama işaretlenmiş videoları tekrar dene "
                         "(JSON parse hatası sonrası sıkışmış kayıtlar için)")
    args = ap.parse_args(argv)

    conn = get_conn()
    if args.retry_empty:
        extra = " AND channel_id = ?" if args.channel_id else ""
        retry_params: list = []
        if args.channel_id:
            retry_params.append(args.channel_id)
        n = conn.execute(f"""
            UPDATE videos SET claims_extracted_at = NULL
            WHERE claims_extracted_at IS NOT NULL
              AND transcript IS NOT NULL
              AND video_id NOT IN (SELECT DISTINCT video_id FROM claims)
              {extra}
        """, retry_params).rowcount
        conn.commit()
        print(f"[claims] {n} video yeniden deneme için sıfırlandı (0 iddia)")
    # claims_extracted_at IS NULL kullanıyoruz (claim sayısına değil) — bir video
    # gerçekten 0 iddia içerse bile burası set edilir, yoksa o video her
    # çalıştırmada tekrar tekrar (ve tekrar ücretli) işlenir.
    params: list = []
    channel_clause = ""
    if args.channel_id:
        channel_clause = " AND channel_id = ?"
        params.append(args.channel_id)
    params.append(args.limit)
    rows = conn.execute(f"""
        SELECT video_id, channel_id, transcript
        FROM videos
        WHERE transcript IS NOT NULL
          AND claims_extracted_at IS NULL
          {channel_clause}
        LIMIT ?
    """, params).fetchall()

    print(f"[claims] işlenecek video sayısı: {len(rows)}")
    ok, failed = 0, 0
    for row in rows:
        print(f"  -> {row['video_id']}")
        try:
            claims, success = extract_claims(row["transcript"], video_id=row["video_id"])
        except Exception as e:
            # Tek videonun API hatası (rate limit, ağ) tüm batch'i durdurmasın.
            # claims_extracted_at İŞARETLENMEZ, bir sonraki çalıştırmada tekrar denenir.
            print(f"     !! hata, bu video atlandı (bir sonraki çalıştırmada tekrar denenecek): {e}")
            failed += 1
            continue

        if not success:
            print("     !! JSON parse başarısız — video işaretlenmedi, sonra tekrar denenecek")
            failed += 1
            continue

        insert_claims_batch(conn, row["video_id"], row["channel_id"], claims, DEFAULT_EXTRACTION_VERSION)
        print(f"     {len(claims)} iddia çıkarıldı")
        ok += 1

    conn.close()
    print(f"[claims] tamamlandı. {ok} video işlendi, {failed} video hata verdi (tekrar denenecek).")
    if ok > 0:
        _refresh_exports()


if __name__ == "__main__":
    main()
