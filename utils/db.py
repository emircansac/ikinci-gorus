"""SQLite bağlantı ve yardımcı fonksiyonlar."""
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "data" / "monitor.db"
SCHEMA_PATH = Path(__file__).parent.parent / "db" / "schema.sql"


def get_conn():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    _migrate_columns(conn)
    return conn


def _migrate_human_reviewed_semantics(conn):
    """
    human_reviewed yalnızca gerçek insan onayını (reviewer_note izi) taşır.
    Otomasyonun 'incelemeye gerek yok' kararı auto_accepted'a taşınır.
    """
    conn.execute("""
        UPDATE verdicts
        SET human_reviewed = 0, auto_accepted = 1
        WHERE human_reviewed = 1
          AND (reviewer_note IS NULL OR TRIM(reviewer_note) = '')
    """)
    conn.execute("""
        UPDATE verdicts
        SET auto_accepted = 0
        WHERE human_reviewed = 1
          AND reviewer_note IS NOT NULL
          AND TRIM(reviewer_note) != ''
    """)
    # Indirect kanıt: otomasyon bypass sayılmaz (claim 673 tipi)
    conn.execute("""
        UPDATE verdicts
        SET auto_accepted = 0
        WHERE source_directness = 'indirect'
          AND human_reviewed = 0
          AND (reviewer_note IS NULL OR TRIM(reviewer_note) = '')
    """)
    conn.execute("""
        DELETE FROM verified_claim_library
        WHERE origin_claim_id IN (
            SELECT vcl.origin_claim_id
            FROM verified_claim_library vcl
            JOIN verdicts vr ON vr.claim_id = vcl.origin_claim_id
            WHERE vr.human_reviewed != 1
        )
    """)
    conn.commit()


def _migrate_columns(conn):
    """Mevcut DB'lere sonradan eklenen sütunlar — duplicate column hataları yutulur."""
    migrations = (
        ("videos", "watch_source", "TEXT DEFAULT 'channel'"),
        ("claims", "archived_at", "TEXT"),
        ("claims", "archive_reason", "TEXT"),
        ("claims", "extraction_version", "TEXT DEFAULT 'v1'"),
        ("videos", "active_extraction_version", "TEXT DEFAULT 'v1'"),
        ("verdicts", "reasoning", "TEXT"),
        ("verdicts", "source_directness", "TEXT"),
        ("verdicts", "evidence_stance", "TEXT"),
        ("verdicts", "source_tier", "TEXT"),
        ("verdicts", "calibration_flags", "TEXT"),
        ("verdicts", "library_match", "INTEGER DEFAULT 0"),
        ("verdicts", "auto_accepted", "INTEGER DEFAULT 0"),
        ("verdicts", "would_auto_accept_v1", "INTEGER DEFAULT 0"),
        ("verdicts", "would_auto_accept_reason", "TEXT"),
    )
    for table, col, typedef in migrations:
        try:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typedef}")
        except sqlite3.OperationalError:
            pass
    conn.commit()
    _migrate_human_reviewed_semantics(conn)
    conn.execute("""
        UPDATE claims SET extraction_version = 'v1'
        WHERE extraction_version IS NULL
    """)
    conn.execute("""
        UPDATE videos SET active_extraction_version = 'v1'
        WHERE active_extraction_version IS NULL
    """)
    conn.commit()


def init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    with open(SCHEMA_PATH, "r", encoding="utf-8") as f:
        conn.executescript(f.read())
    _migrate_columns(conn)
    conn.close()
    print(f"[db] Şema uygulandı -> {DB_PATH}")


if __name__ == "__main__":
    init_db()
