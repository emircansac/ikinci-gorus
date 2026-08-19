"""
AŞAMA 6: İddia indeksi + şüphe bazlı önceliklendirme.

İkili (doğrulanmış/yanlış) etiket yerine SÜREKLİ ŞÜPHE SKORU kullanır
(bkz. utils/suspicion.py) — "yanlışa ne kadar yakın" sürekli bir eksen, ikiye
bölünmüş bir kutu değil.

Üç çıktı üretir:
  1. claim_index.csv         — her iddia tek satır, şüphe skoruna göre sıralı
  2. narrative_clusters.csv  — aynı/benzer iddianın BİRDEN FAZLA KANALDA tekrar
                                ettiği kümeler (tek kanal değil, yayılan anlatı bazlı)
  3. videos.csv              — video bazlı özet (dashboard video grid için)

Kullanım:
    python pipeline/06_claim_index.py --export-dir data/
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent))
import pandas as pd
from utils.db import get_conn
from utils.suspicion import compute_suspicion, compute_priority
from utils.text_similarity import get_cluster_members, get_cluster_members_embedding
from utils.factcheck_calibrate import calibrate_factcheck
from utils.reviewer_summary import build_reviewer_summary

ROOT = Path(__file__).parent.parent
DEBUG_LOG = ROOT / "data" / "factcheck_debug.jsonl"

# Claim metinleri comment'lerden farklı — aynı fikri farklı cümle yapısıyla ifade
# edebilirler (LLM'in çıkardığı önerme, videodan videoya paslanmaz). Bu yüzden
# yorum kümeleme eşiğinden (0.85) daha düşük bir eşik kullanıyoruz. Bu HÂLÂ salt
# harf/kelime bazlı bir benzerlik (SequenceMatcher) — anlamca aynı ama tamamen
# farklı kelimelerle ifade edilmiş iki iddiayı ("X kanseri önler" vs "X tümör
# oluşumunu azaltır") YAKALAYAMAZ. Bunun için embedding tabanlı benzerlik (ör.
# sentence-transformers) gerekir — bu prototipte kapsam dışı, bkz. README.
CLAIM_CLUSTER_THRESHOLD = 0.55

PLACEHOLDER_THUMB = "https://placehold.co/480x270/171C16/9BA396?text=Video"


def _latest_debug_by_claim() -> dict[int, dict]:
    out: dict[int, dict] = {}
    if not DEBUG_LOG.is_file():
        return out
    with DEBUG_LOG.open(encoding="utf-8") as f:
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


def thumbnail_url(video_id: str) -> str:
    """Gerçek YouTube video ID'leri için ytimg; demo/sentetik ID'ler için placeholder."""
    if not video_id or video_id.startswith("DEMO_") or len(video_id) < 8:
        return PLACEHOLDER_THUMB
    return f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"


def build_flat_index(conn) -> pd.DataFrame:
    rows = conn.execute("""
        SELECT
            cl.claim_id, cl.claim_text, cl.category, cl.initial_risk, cl.timestamp_sec,
            cl.video_id, cl.channel_id, cl.extraction_version, cl.archived_at, cl.archive_reason,
            ch.name AS channel_name,
            v.title AS video_title, v.published_at AS video_published_at,
            vr.final_verdict, vr.confidence, vr.source_url, vr.human_reviewed, vr.reviewer_note, vr.escalated,
            vr.reasoning, vr.source_directness, vr.evidence_stance, vr.source_tier, vr.calibration_flags,
            vr.nli_label, vr.nli_confidence, vr.nli_evidence_snippet
        FROM claims cl
        LEFT JOIN verdicts vr ON vr.claim_id = cl.claim_id
        LEFT JOIN videos v ON v.video_id = cl.video_id
        LEFT JOIN channels ch ON ch.channel_id = cl.channel_id
    """).fetchall()

    records = []
    debug_by_claim = _latest_debug_by_claim()
    for r in rows:
        parse_failed = (r["final_verdict"] is None and r["escalated"] == 1)
        # Eski kayıtlarda reasoning/stance boş olabilir; URL tabanlı koruma yine çalışır
        # (ör. Wikipedia + conf=0.85 → tavan). DB'deki ham değer değişmez, CSV kalibre edilir.
        cal = calibrate_factcheck({
            "final_verdict": r["final_verdict"],
            "confidence": r["confidence"],
            "source_url": r["source_url"] or "",
            "reasoning": r["reasoning"] or "",
            "source_directness": r["source_directness"],
            "evidence_stance": r["evidence_stance"],
            "source_tier": r["source_tier"],
        })
        stored_flags = (r["calibration_flags"] or "").strip()
        export_flags = cal["calibration_flags"]
        if stored_flags and export_flags and stored_flags not in export_flags:
            export_flags = f"{stored_flags},{export_flags}"
        elif stored_flags and not export_flags:
            export_flags = stored_flags
        score, note = compute_suspicion(
            cal["final_verdict"], cal["confidence"], parse_failed=parse_failed)
        dbg = debug_by_claim.get(int(r["claim_id"])) or {}
        summary_input = {
            "final_verdict": cal["final_verdict"],
            "reasoning": cal["reasoning"] or r["reasoning"],
            "calibration_flags": export_flags,
            "category": r["category"],
            "initial_risk": r["initial_risk"],
            "claim_text": r["claim_text"],
            "evidence_stance": cal["evidence_stance"],
            "source_directness": cal["source_directness"],
            "cite_source": cal.get("cite_source"),
            "nli_label": r["nli_label"],
            "nli_confidence": r["nli_confidence"],
            "nli_evidence_snippet": r["nli_evidence_snippet"],
            "partial_caveat_matched_index": dbg.get("partial_caveat_matched_index"),
            "partial_caveat_matched_phrase": dbg.get("partial_caveat_matched_phrase"),
        }
        reviewer = build_reviewer_summary(summary_input)
        records.append({
            "claim_id": r["claim_id"],
            "claim_text": r["claim_text"],
            "category": r["category"],
            "initial_risk": r["initial_risk"],
            "video_id": r["video_id"],
            "channel_id": r["channel_id"],
            "channel_name": r["channel_name"],
            "video_title": r["video_title"],
            "timestamp_sec": r["timestamp_sec"],
            "extraction_version": r["extraction_version"],
            "final_verdict": cal["final_verdict"],
            "confidence": cal["confidence"],
            "suspicion_score": score,
            "suspicion_note": note,
            "human_reviewed": r["human_reviewed"],
            "reviewer_note": r["reviewer_note"],
            "archived_at": r["archived_at"],
            "archive_reason": r["archive_reason"],
            "source_url": cal["source_url"] or r["source_url"],
            "reasoning": cal["reasoning"] or r["reasoning"],
            "source_directness": cal["source_directness"],
            "evidence_stance": cal["evidence_stance"],
            "source_tier": cal["source_tier"],
            "calibration_flags": export_flags,
            "reviewer_check_point": reviewer["check_point"],
            "reviewer_risk_level": reviewer["risk_level"],
            "reviewer_source_note": reviewer["source_note"],
        })
    return pd.DataFrame(records)


def build_narrative_clusters(df: pd.DataFrame, method: str = "embedding") -> tuple[pd.DataFrame, str]:
    """
    Farklı kanallardaki BENZER iddiaları kümeler. Tek kanaldaki tek iddiadan çok,
    aynı yanlış anlatının kaç farklı kanalda tekrarlandığı asıl haber değeridir.

    Dönüş: (clusters_df, embedding_clustering_status) — status "ok" | "failed: <sebep>"
    (lexical fallback varsa `; lexical_fallback` eklenir).
    """
    scoreable = df[df["suspicion_score"].notna()].to_dict("records")
    clustering_status = "ok"
    if method == "embedding":
        clusters, emb_status = get_cluster_members_embedding(
            scoreable, id_key="claim_id", text_key="claim_text",
            threshold=0.80,
        )
        clustering_status = emb_status
        if not clusters:
            clusters = get_cluster_members(
                scoreable, id_key="claim_id", text_key="claim_text",
                threshold=CLAIM_CLUSTER_THRESHOLD,
            )
            if str(emb_status).startswith("failed"):
                clustering_status = f"{emb_status}; lexical_fallback"
    else:
        clusters = get_cluster_members(
            scoreable, id_key="claim_id", text_key="claim_text",
            threshold=CLAIM_CLUSTER_THRESHOLD,
        )
        clustering_status = "ok (sequence/lexical)"

    cluster_rows = []
    for members in clusters:
        distinct_channels = {m["channel_id"] for m in members}
        if len(distinct_channels) < 2:
            continue  # aynı kanalın kendi tekrarları değil, FARKLI kanallar arıyoruz
        avg_susp = sum(m["suspicion_score"] for m in members) / len(members)
        # önceliği en riskli kategoriyle hesapla (kümedeki en tehlikeli versiyon belirleyici olsun)
        worst_category = max(members, key=lambda m: compute_priority(m["suspicion_score"], m["category"], len(distinct_channels)))["category"]
        priority = compute_priority(avg_susp, worst_category, len(distinct_channels))
        cluster_rows.append({
            "representative_claim": members[0]["claim_text"],
            "channels_affected": len(distinct_channels),
            "channel_names": ", ".join(sorted({m["channel_name"] or m["channel_id"] for m in members})),
            "member_count": len(members),
            "avg_suspicion_score": round(avg_susp, 1),
            "priority_score": priority,
            "example_claim_ids": ",".join(str(m["claim_id"]) for m in members[:5]),
        })

    cols = ["representative_claim", "channels_affected", "channel_names", "member_count",
            "avg_suspicion_score", "priority_score", "example_claim_ids"]
    df_out = (
        pd.DataFrame(cluster_rows, columns=cols).sort_values("priority_score", ascending=False)
        if cluster_rows else pd.DataFrame(columns=cols)
    )
    return df_out, clustering_status


def build_video_index(conn, claims_df: pd.DataFrame) -> pd.DataFrame:
    """Video bazlı özet: iddialardan aggregate edilmiş dashboard satırları."""
    videos = conn.execute("""
        SELECT v.video_id, v.title, v.published_at, v.channel_id, ch.name AS channel_name
        FROM videos v
        LEFT JOIN channels ch ON ch.channel_id = v.channel_id
    """).fetchall()

    claim_groups = {}
    if not claims_df.empty and "video_id" in claims_df.columns:
        for vid, grp in claims_df.groupby("video_id", dropna=True):
            if pd.isna(vid):
                continue
            scored = grp[grp["suspicion_score"].notna()]
            pending = grp[grp["human_reviewed"].fillna(0).astype(float) == 0]
            top_row = scored.loc[scored["suspicion_score"].idxmax()] if not scored.empty else None
            claim_groups[vid] = {
                "claim_count": len(grp),
                "max_suspicion_score": float(scored["suspicion_score"].max()) if not scored.empty else None,
                "top_verdict": top_row["final_verdict"] if top_row is not None else None,
                "pending_review_count": int(pending.shape[0]),
            }

    rows = []
    seen = set()
    for v in videos:
        vid = v["video_id"]
        seen.add(vid)
        stats = claim_groups.get(vid, {})
        rows.append({
            "video_id": vid,
            "title": v["title"],
            "published_at": v["published_at"],
            "channel_id": v["channel_id"],
            "channel_name": v["channel_name"],
            "claim_count": stats.get("claim_count", 0),
            "max_suspicion_score": stats.get("max_suspicion_score"),
            "top_verdict": stats.get("top_verdict"),
            "pending_review_count": stats.get("pending_review_count", 0),
            "youtube_url": f"https://www.youtube.com/watch?v={vid}",
            "thumbnail_url": thumbnail_url(vid),
        })

    # İddiası olan ama videos tablosunda olmayan edge case (olmamalı ama güvenli)
    for vid, stats in claim_groups.items():
        if vid in seen:
            continue
        sample = claims_df[claims_df["video_id"] == vid].iloc[0]
        rows.append({
            "video_id": vid,
            "title": sample.get("video_title"),
            "published_at": None,
            "channel_id": sample.get("channel_id"),
            "channel_name": sample.get("channel_name"),
            "claim_count": stats.get("claim_count", 0),
            "max_suspicion_score": stats.get("max_suspicion_score"),
            "top_verdict": stats.get("top_verdict"),
            "pending_review_count": stats.get("pending_review_count", 0),
            "youtube_url": f"https://www.youtube.com/watch?v={vid}",
            "thumbnail_url": thumbnail_url(vid),
        })

    cols = ["video_id", "title", "published_at", "channel_id", "channel_name",
            "claim_count", "max_suspicion_score", "top_verdict", "pending_review_count",
            "youtube_url", "thumbnail_url"]
    if not rows:
        return pd.DataFrame(columns=cols)
    out = pd.DataFrame(rows)
    return out.sort_values("max_suspicion_score", ascending=False, na_position="last")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--export-dir", default="data/")
    ap.add_argument("--cluster-method", choices=("embedding", "sequence"), default="embedding")
    args = ap.parse_args()

    conn = get_conn()
    df = build_flat_index(conn)

    if df.empty:
        conn.close()
        print("[claim_index] Henüz iddia yok — önce 01-03. aşamaları çalıştırın.")
        return

    # Öncelik skoru: sadece hesaplanabilen (needs_more_data olmayan) satırlar için
    df["priority_score"] = df.apply(
        lambda r: compute_priority(r["suspicion_score"], r["category"], 1) if pd.notna(r["suspicion_score"]) else None,
        axis=1
    )

    # needs_more_data (suspicion_score=None) satırları ayrı bir bölüme, sıralamanın SONUNA
    # koyuyoruz — bunlar "az şüpheli" DEĞİL, "henüz bilinmiyor" demek; karıştırmak yanlış olur.
    scored = df[df["suspicion_score"].notna()].sort_values("suspicion_score", ascending=False)
    unscored = df[df["suspicion_score"].isna()]
    df_sorted = pd.concat([scored, unscored], ignore_index=True)

    active = df_sorted[df_sorted["archived_at"].isna()]
    archived = df_sorted[df_sorted["archived_at"].notna()]

    export_dir = Path(args.export_dir)
    export_dir.mkdir(parents=True, exist_ok=True)
    index_path = export_dir / "claim_index.csv"
    archive_path = export_dir / "claim_archive.csv"
    active.to_csv(index_path, index=False)
    archived.to_csv(archive_path, index=False)

    clusters_df, clustering_status = build_narrative_clusters(df, method=args.cluster_method)
    clusters_path = export_dir / "narrative_clusters.csv"
    clusters_df.to_csv(clusters_path, index=False)

    sidecar = Path(__file__).parent.parent / "data" / "ops_reports" / "embedding_clustering_status.txt"
    sidecar.parent.mkdir(parents=True, exist_ok=True)
    sidecar.write_text(clustering_status + "\n", encoding="utf-8")

    videos_df = build_video_index(conn, df)
    conn.close()
    videos_path = export_dir / "videos.csv"
    videos_df.to_csv(videos_path, index=False)

    print(f"[claim_index] {len(active)} aktif iddia -> {index_path}")
    print(f"[claim_index] {len(archived)} arşiv iddia -> {archive_path}")
    print(f"[claim_index] {len(unscored)} veri_eksik/henüz işlenmemiş (toplam {len(df)})")
    print(f"[claim_index] {len(clusters_df)} çapraz-kanal anlatı kümesi bulundu -> {clusters_path}")
    print(f"[claim_index] embedding_clustering_status={clustering_status} -> {sidecar}")
    print(f"[claim_index] {len(videos_df)} video indekslendi -> {videos_path}")

    if not scored.empty:
        print("\n--- En şüpheli 5 iddia ---")
        print(scored.head(5)[["claim_text", "category", "final_verdict", "confidence",
                               "suspicion_score", "channel_name"]].to_string(index=False))

    if not clusters_df.empty:
        print("\n--- En yaygın 3 anlatı kümesi ---")
        print(clusters_df.head(3)[["representative_claim", "channels_affected",
                                    "avg_suspicion_score", "priority_score"]].to_string(index=False))


if __name__ == "__main__":
    main()
