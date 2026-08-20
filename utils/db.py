"""SQLite bağlantı ve yardımcı fonksiyonlar."""
import sqlite3
from pathlib import Path

from utils.evidence_topic_cache import ensure_topic_cache_table
from utils.review_outcomes import ensure_review_outcomes_table

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


def _load_debug_by_claim() -> dict[int, dict]:
    debug_path = Path(__file__).parent.parent / "data" / "factcheck_debug.jsonl"
    out: dict[int, dict] = {}
    if not debug_path.is_file():
        return out
    import json
    with debug_path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            cid = rec.get("claim_id")
            if cid is not None:
                out[int(cid)] = rec
    return out


def _reconcile_stale_auto_accepted(conn) -> list[int]:
    """
    Güvenlik kuralları sonradan eklendiğinde kalan auto_accepted=1 satırları düzelt.
    Aktif iddialara karşı çalışır; compute_needs_human + partial_caveat dahil güncel tetikleyiciler.
    (claim 709 tipi — drug_interaction kuralı öncesi fact-check edilmiş kayıtlar)
    """
    from utils.factcheck_review import stale_auto_accept_reasons

    debug_by = _load_debug_by_claim()
    rows = conn.execute("""
        SELECT c.claim_id, c.claim_text, c.category, c.initial_risk,
               v.final_verdict, v.confidence, v.source_url, v.reasoning,
               v.source_directness, v.evidence_stance, v.source_tier,
               v.calibration_flags, v.escalated, v.library_match,
               v.nli_evidence_snippet, v.human_reviewed, v.reviewer_note
        FROM claims c
        JOIN verdicts v ON v.claim_id = c.claim_id
        WHERE c.archived_at IS NULL
          AND v.auto_accepted = 1
          AND v.human_reviewed = 0
          AND (v.reviewer_note IS NULL OR TRIM(v.reviewer_note) = '')
    """).fetchall()
    fixed: list[int] = []
    for row in rows:
        r = dict(row)
        cid = int(r["claim_id"])
        dbg = debug_by.get(cid) or {}
        reasons = stale_auto_accept_reasons(
            category=r.get("category"),
            initial_risk=r.get("initial_risk"),
            claim_text=r.get("claim_text") or "",
            final_verdict=r.get("final_verdict"),
            confidence=r.get("confidence"),
            source_url=r.get("source_url"),
            reasoning=r.get("reasoning"),
            source_directness=r.get("source_directness"),
            evidence_stance=r.get("evidence_stance"),
            source_tier=r.get("source_tier"),
            calibration_flags=r.get("calibration_flags"),
            escalated=int(r.get("escalated") or 0),
            library_match=r.get("library_match"),
            nli_evidence_snippet=r.get("nli_evidence_snippet"),
            partial_caveat_matched_index=dbg.get("partial_caveat_matched_index"),
        )
        if reasons:
            conn.execute(
                "UPDATE verdicts SET auto_accepted = 0 WHERE claim_id = ?",
                (cid,),
            )
            fixed.append(cid)
    if fixed:
        conn.commit()
    return fixed


def _backfill_shadow_human_gates(conn):
    """Mevcut verdict satırlarına shadow gate kolonlarını doldur (davranışı etkilemez)."""
    from utils.factcheck_review import compute_needs_human
    from utils.reviewer_summary import compute_shadow_human_gates

    rows = conn.execute("""
        SELECT c.claim_id, c.claim_text, c.category, c.initial_risk,
               v.final_verdict, v.confidence, v.calibration_flags,
               v.source_directness, v.escalated, v.auto_accepted,
               v.library_match, v.reasoning
        FROM claims c
        JOIN verdicts v ON v.claim_id = c.claim_id
    """).fetchall()
    for row in rows:
        r = dict(row)
        flags = r.get("calibration_flags") or ""
        reasoning = r.get("reasoning") or ""
        parse_failed = "parse edilemedi" in reasoning.lower()
        esc = int(r.get("escalated") or 0)
        library_hit = None
        if "library_flag_review" in {
            f.strip() for f in flags.split(",") if f.strip()
        }:
            library_hit = {"raw": r.get("library_match")}
        needs_human = compute_needs_human(
            category=r.get("category"),
            initial_risk=r.get("initial_risk"),
            claim_text=r.get("claim_text") or "",
            parse_failed=parse_failed,
            final_verdict=r.get("final_verdict"),
            escalated_flag=esc,
            calibrated={
                "needs_human": esc == 1 and int(r.get("auto_accepted") or 0) == 0,
            },
            source_directness=r.get("source_directness"),
            library_review_hit=library_hit,
            calibration_flags=flags,
        )
        gates = compute_shadow_human_gates(
            final_verdict=r.get("final_verdict"),
            confidence=r.get("confidence"),
            calibration_flags=flags,
            needs_human=needs_human,
        )
        conn.execute(
            """
            UPDATE verdicts SET
                would_require_human_verdict_gate = ?,
                would_require_human_confidence_gate = ?,
                would_require_human_compound_gate = ?,
                would_auto_accept_after_all_gates = ?
            WHERE claim_id = ?
            """,
            (
                gates["would_require_human_verdict_gate"],
                gates["would_require_human_confidence_gate"],
                gates["would_require_human_compound_gate"],
                gates["would_auto_accept_after_all_gates"],
                int(r["claim_id"]),
            ),
        )
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
        ("verdicts", "would_require_human_verdict_gate", "INTEGER DEFAULT 0"),
        ("verdicts", "would_require_human_confidence_gate", "INTEGER DEFAULT 0"),
        ("verdicts", "would_require_human_compound_gate", "INTEGER DEFAULT 0"),
        ("verdicts", "would_auto_accept_after_all_gates", "INTEGER DEFAULT 0"),
    )
    for table, col, typedef in migrations:
        try:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typedef}")
        except sqlite3.OperationalError:
            pass
    conn.commit()
    _migrate_human_reviewed_semantics(conn)
    _reconcile_stale_auto_accepted(conn)
    _backfill_shadow_human_gates(conn)
    ensure_topic_cache_table(conn)
    ensure_review_outcomes_table(conn)
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
