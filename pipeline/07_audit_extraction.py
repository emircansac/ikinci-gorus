"""
AŞAMA 2 DENETİMİ: İddia çıkarma kalitesi — yapısal kontroller (API'siz, ücretsiz).
"""
import argparse
import re
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent))
from utils.db import get_conn
from utils.text_similarity import get_cluster_members, normalize
from utils.claude_client import _is_recap_chunk, _split_transcript_chunks
from utils.claim_dedup import is_duplicate_pair, embed_texts

TIMESTAMP_TOLERANCE_SEC = 8
SHORT_CLAIM_WORDS = 4
LONG_CLAIM_WORDS = 40
OTHER_CATEGORY_WARN_RATIO = 0.35
RECALL_DENSITY_THRESHOLDS = {
    "odZgEDFDmbE": 2.5,
    "P4m9F9mykQ8": 1.5,
    "default": 2.0,
}
TOPIC_REPEAT_KEYWORDS = {
    "homosistein": ["homosistein", "homosiste"],
    "potasyum": ["potasyum", "hiperkalemi"],
    "gfr": ["gfr", "glomerüler filtrasyon", "glomeruler filtrasyon"],
    "b12": ["b12", "vitamin b12", "kobalamin"],
    "parkinson": ["parkinson", "dopamin"],
}

CLAIM_TRIGGER_PATTERNS = [
    r"\d+\s*(mg|gram|gr|ml|litre|kalori|%|kat)",
    r"(azaltır|artırır|önler|tedavi eder|iyileştirir|yol açar|neden olur|tetikler|destekler|güçlendirir)",
]


def extract_transcript_timestamps(transcript: str) -> list[int]:
    return [int(m) for m in re.findall(r"\[(\d+)s\]", transcript or "")]


def nearest_distance(ts: int, tag_list: list[int]):
    if not tag_list:
        return None
    return min(abs(ts - t) for t in tag_list)


def audit_video(conn, video_id: str) -> dict:
    video = conn.execute("SELECT transcript, title FROM videos WHERE video_id=?", (video_id,)).fetchone()
    claims = conn.execute("""
        SELECT claim_id, claim_text, category, timestamp_sec FROM claims
        WHERE video_id=? AND archived_at IS NULL
        ORDER BY claim_id
    """, (video_id,)).fetchall()

    if not claims:
        return {"video_id": video_id, "issues": [], "claim_count": 0}

    issues = []
    transcript = video["transcript"] or ""
    tags = extract_transcript_timestamps(transcript)

    items = [{"claim_id": c["claim_id"], "text": c["claim_text"]} for c in claims]
    dup_clusters = get_cluster_members(items, id_key="claim_id", text_key="text", threshold=0.85)
    for cluster in dup_clusters:
        ids = [m["claim_id"] for m in cluster]
        issues.append({"type": "video_ici_tekrar",
                        "detail": f"claim_id {ids} birbirine çok benzer — chunk sınırında iki kez çıkarılmış olabilir",
                        "severity": "orta"})

    for c in claims:
        ts = c["timestamp_sec"]
        if ts is None:
            continue
        if not tags:
            issues.append({"type": "zaman_damgasi_dogrulanamadi",
                            "detail": f"claim_id {c['claim_id']}: transkriptte hiç [Ns] etiketi yok, timestamp_sec={ts} doğrulanamıyor",
                            "severity": "düşük"})
            continue
        dist = nearest_distance(ts, tags)
        if dist is not None and dist > TIMESTAMP_TOLERANCE_SEC:
            issues.append({"type": "zaman_damgasi_supheli",
                            "detail": f"claim_id {c['claim_id']}: timestamp_sec={ts}, en yakın gerçek etiketten {dist}s uzakta (>{TIMESTAMP_TOLERANCE_SEC}s tolerans) — UYDURULMUŞ olabilir",
                            "severity": "yüksek"})

    for c in claims:
        n_words = len((c["claim_text"] or "").split())
        if n_words < SHORT_CLAIM_WORDS:
            issues.append({"type": "cok_kisa_iddia",
                            "detail": f"claim_id {c['claim_id']}: {n_words} kelime — atomik bir iddia mı, yoksa anlamsız bir parça mı: {c['claim_text']!r}",
                            "severity": "orta"})
        elif n_words > LONG_CLAIM_WORDS:
            issues.append({"type": "cok_uzun_iddia",
                            "detail": f"claim_id {c['claim_id']}: {n_words} kelime — birden fazla iddia birleşmiş olabilir: {c['claim_text'][:100]!r}...",
                            "severity": "orta"})

    n_other = sum(1 for c in claims if c["category"] == "diğer")
    if len(claims) >= 5 and n_other / len(claims) > OTHER_CATEGORY_WARN_RATIO:
        issues.append({"type": "diger_asiri_kullanim",
                        "detail": f"{n_other}/{len(claims)} iddia 'diğer' kategoride (%{100*n_other/len(claims):.0f}) — taksonomi bu videonun içeriğini ayırt edemiyor olabilir",
                        "severity": "düşük"})

    trigger_count = sum(len(re.findall(p, transcript.lower())) for p in CLAIM_TRIGGER_PATTERNS)
    if transcript and trigger_count > 0:
        ratio = len(claims) / trigger_count
        if ratio < 0.3:
            issues.append({"type": "recall_supheli",
                            "detail": f"transkriptte {trigger_count} 'iddia tetikleyici' kalıp var ama sadece {len(claims)} iddia çıkarılmış — bazı iddialar kaçırılmış olabilir (YAKLAŞIK bir sinyal, kesin değil)",
                            "severity": "düşük"})

    if tags:
        duration_min = max(tags) / 60.0
        if duration_min > 0:
            density = len(claims) / duration_min
            thresh = RECALL_DENSITY_THRESHOLDS.get(video_id, RECALL_DENSITY_THRESHOLDS["default"])
            if density > thresh:
                issues.append({
                    "type": "recall_asiri",
                    "detail": f"{len(claims)} iddia / {duration_min:.1f} dk = {density:.1f} iddia/dk (eşik ~{thresh}) — over-extraction olabilir",
                    "severity": "orta",
                })

    for topic, kws in TOPIC_REPEAT_KEYWORDS.items():
        hits = [
            c["claim_id"] for c in claims
            if any(kw in normalize(c["claim_text"] or "") for kw in kws)
        ]
        if len(hits) >= 3:
            issues.append({
                "type": "konu_tekrari",
                "detail": f"'{topic}' konusunda {len(hits)} iddia (claim_id ör: {hits[:5]}) — paraphrase tekrarı olabilir",
                "severity": "orta",
            })

    chunks = _split_transcript_chunks(transcript)
    if len(chunks) > 1 and _is_recap_chunk(chunks[-1], is_last=True):
        recap_ts = [int(m) for m in re.findall(r"\[(\d+)s\]", chunks[-1])]
        recap_min = min(recap_ts) if recap_ts else None
        prior = [c for c in claims if recap_min is None or (c["timestamp_sec"] or 0) < recap_min]
        late = [c for c in claims if recap_min is not None and (c["timestamp_sec"] or 0) >= recap_min]
        if prior and late:
            prior_texts = [normalize(c["claim_text"]) for c in prior]
            prior_embs = embed_texts(prior_texts)
            for c in late:
                lt = normalize(c["claim_text"] or "")
                le = embed_texts([lt])[0]
                for pt, pe in zip(prior_texts, prior_embs):
                    if is_duplicate_pair(lt, pt, le, pe):
                        issues.append({
                            "type": "recap_duplicate",
                            "detail": f"claim_id {c['claim_id']}: kapanış bölümünde önceki iddianın tekrarı olabilir",
                            "severity": "orta",
                        })
                        break

    return {"video_id": video_id, "title": video["title"], "issues": issues, "claim_count": len(claims)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--video-id", default=None)
    args = ap.parse_args()

    conn = get_conn()
    video_ids = [args.video_id] if args.video_id else \
        [r["video_id"] for r in conn.execute(
            "SELECT DISTINCT video_id FROM claims WHERE archived_at IS NULL"
        ).fetchall()]

    total_issues = 0
    for vid in video_ids:
        result = audit_video(conn, vid)
        if not result["issues"]:
            continue
        print(f"\n{'='*70}\n{vid} — {result.get('title', '')[:60]} ({result['claim_count']} iddia)\n{'='*70}")
        for issue in sorted(result["issues"], key=lambda i: {"yüksek": 0, "orta": 1, "düşük": 2}[i["severity"]]):
            mark = {"yüksek": "🔴", "orta": "🟡", "düşük": "⚪"}[issue["severity"]]
            print(f"  {mark} [{issue['type']}] {issue['detail']}")
            total_issues += 1

    conn.close()
    print(f"\n[audit] {len(video_ids)} video tarandı, {total_issues} yapısal uyarı bulundu.")


if __name__ == "__main__":
    main()
