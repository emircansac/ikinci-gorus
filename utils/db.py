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
    )
    for table, col, typedef in migrations:
        try:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typedef}")
        except sqlite3.OperationalError:
            pass
    conn.commit()
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
