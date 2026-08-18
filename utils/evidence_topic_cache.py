"""
Topic evidence cache — kanıt (verdict değil) önbelleği.

Yalnızca category=mekanizma + pilot böbrek/potasyum varlıkları.
Cache hit'ler normal aday havuzuna girer; assess_evidence_sufficiency atlanmaz.
"""
from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING

from utils.evidence_retrieval import ENTITY_SYNONYMS

if TYPE_CHECKING:
    import sqlite3

# Pilot kapsamı — odZg tekrar eden varlıklar
PILOT_TOPIC_ENTITIES = (
    "ıspanak",
    "pancar",
    "kabak",
    "domates",
    "lahana",
    "salatalık",
    "gfr",
    "potasyum",
    "fosfor",
    "oksalat",
    "homosistein",
)

PILOT_CATEGORY = "mekanizma"

# ENTITY_SYNONYMS üzerinden ters indeks → kanonik pilot anahtar
_TOKEN_RE = re.compile(r"[A-Za-zçğıöşüÇĞİÖŞÜ0-9]{3,}")


def _build_synonym_to_canonical() -> dict[str, str]:
    mapping: dict[str, str] = {}
    for canonical in PILOT_TOPIC_ENTITIES:
        mapping[canonical.lower()] = canonical
        for syn in ENTITY_SYNONYMS.get(canonical, ()):
            mapping[syn.lower()] = canonical
        for syn in ENTITY_SYNONYMS.get(canonical.lower(), ()):
            mapping[syn.lower()] = canonical
    # ENTITY_SYNONYMS'teki İngilizce girdiler (spinach → ıspanak vb.)
    for key, syns in ENTITY_SYNONYMS.items():
        key_low = key.lower()
        target = None
        if key_low in {c.lower() for c in PILOT_TOPIC_ENTITIES}:
            target = next(c for c in PILOT_TOPIC_ENTITIES if c.lower() == key_low)
        else:
            for syn in syns:
                sl = syn.lower()
                if sl in {c.lower() for c in PILOT_TOPIC_ENTITIES}:
                    target = next(c for c in PILOT_TOPIC_ENTITIES if c.lower() == sl)
                    break
        if not target:
            continue
        mapping[key_low] = target
        for syn in syns:
            mapping[syn.lower()] = target
    return mapping


_SYNONYM_TO_CANONICAL = _build_synonym_to_canonical()


def pilot_entities_in_text(*texts: str | None) -> list[str]:
    """Metinde geçen pilot varlıklar (kanonik, sıralı benzersiz)."""
    found: set[str] = set()
    for text in texts:
        if not text:
            continue
        for tok in _TOKEN_RE.findall(text):
            canonical = _SYNONYM_TO_CANONICAL.get(tok.lower())
            if canonical:
                found.add(canonical)
    return sorted(found)


def topic_key_for_claim(
    claim_text: str,
    category: str | None,
    *,
    search_query_en: str | None = None,
) -> str | None:
    """
    Pilot kapsamında topic_key (virgülle sıralı kanonik varlıklar).
    category != mekanizma veya pilot varlık yoksa None.
    """
    if (category or "").strip() != PILOT_CATEGORY:
        return None
    entities = pilot_entities_in_text(claim_text, search_query_en)
    if not entities:
        return None
    return ",".join(entities)


def _entity_overlap(claim_key: str, cached_key: str) -> bool:
    a = set(claim_key.split(","))
    b = set(cached_key.split(","))
    return bool(a & b)


def lookup_topic_cache(conn, topic_key: str | None) -> list[dict]:
    """topic_key ile kesişen cache satırlarını evidence dict olarak döndür."""
    if not topic_key:
        return []
    rows = conn.execute(
        """
        SELECT topic_key, source_url, title, abstract, source_tier,
               retrieval_tier, publication_types, origin_claim_id
        FROM evidence_topic_cache
        ORDER BY fetched_at DESC
        """
    ).fetchall()
    out: list[dict] = []
    seen_urls: set[str] = set()
    for row in rows:
        r = dict(row)
        if not _entity_overlap(topic_key, r["topic_key"]):
            continue
        url = (r.get("source_url") or "").strip()
        if not url or url in seen_urls:
            continue
        seen_urls.add(url)
        pub_types = r.get("publication_types")
        if isinstance(pub_types, str) and pub_types.startswith("["):
            try:
                pub_types = json.loads(pub_types)
            except json.JSONDecodeError:
                pass
        out.append({
            "url": url,
            "title": r.get("title") or "",
            "abstract": r.get("abstract") or "",
            "source_tier": r.get("source_tier"),
            "retrieval_tier": r.get("retrieval_tier") or "native",
            "publication_types": pub_types,
            "evidence_source": "cache",
            "cache_topic_key": r.get("topic_key"),
            "cache_origin_claim_id": r.get("origin_claim_id"),
        })
    return out


def store_topic_cache(
    conn,
    topic_key: str,
    evidence_items: list[dict],
    origin_claim_id: int | None,
) -> int:
    """Canlı retrieval sonuçlarını cache'e yazar. Yeni satır sayısı."""
    if not topic_key:
        return 0
    inserted = 0
    for item in evidence_items:
        if item.get("evidence_source") == "cache":
            continue
        url = (item.get("url") or "").strip()
        if not url:
            continue
        pub_types = item.get("publication_types")
        pub_json = (
            json.dumps(pub_types, ensure_ascii=False)
            if isinstance(pub_types, (list, tuple))
            else (pub_types if pub_types else None)
        )
        cur = conn.execute(
            """
            INSERT INTO evidence_topic_cache (
                topic_key, source_url, title, abstract, source_tier,
                retrieval_tier, publication_types, origin_claim_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(topic_key, source_url) DO UPDATE SET
                title = excluded.title,
                abstract = excluded.abstract,
                source_tier = excluded.source_tier,
                retrieval_tier = excluded.retrieval_tier,
                publication_types = excluded.publication_types,
                fetched_at = datetime('now'),
                origin_claim_id = excluded.origin_claim_id
            """,
            (
                topic_key,
                url,
                item.get("title") or "",
                item.get("abstract") or "",
                item.get("source_tier"),
                item.get("retrieval_tier") or "native",
                pub_json,
                origin_claim_id,
            ),
        )
        if cur.rowcount:
            inserted += 1
    conn.commit()
    return inserted


def seed_cache_from_evidence(
    conn,
    *,
    topic_key: str,
    evidence_items: list[dict],
    origin_claim_id: int,
) -> int:
    """Offline seed — kayıtlı kanıt paketinden cache doldur (evidence_source yok sayılır)."""
    tagged = [{**item, "evidence_source": "live"} for item in evidence_items]
    return store_topic_cache(conn, topic_key, tagged, origin_claim_id)


def ensure_topic_cache_table(conn) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS evidence_topic_cache (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            topic_key           TEXT NOT NULL,
            source_url          TEXT NOT NULL,
            title               TEXT,
            abstract            TEXT,
            source_tier         TEXT,
            retrieval_tier      TEXT,
            publication_types   TEXT,
            fetched_at          TEXT DEFAULT (datetime('now')),
            origin_claim_id     INTEGER,
            UNIQUE(topic_key, source_url)
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_topic_cache_key ON evidence_topic_cache(topic_key)"
    )
    conn.commit()
