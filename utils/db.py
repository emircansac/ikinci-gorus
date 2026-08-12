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
    return conn


def init_db():
    conn = get_conn()
    with open(SCHEMA_PATH, "r", encoding="utf-8") as f:
        conn.executescript(f.read())
    try:
        conn.execute("ALTER TABLE videos ADD COLUMN watch_source TEXT DEFAULT 'channel'")
    except sqlite3.OperationalError:
        pass
    for col, typedef in (
        ("archived_at", "TEXT"),
        ("archive_reason", "TEXT"),
    ):
        try:
            conn.execute(f"ALTER TABLE claims ADD COLUMN {col} {typedef}")
        except sqlite3.OperationalError:
            pass
    conn.commit()
    conn.close()
    print(f"[db] Şema uygulandı -> {DB_PATH}")


if __name__ == "__main__":
    init_db()
