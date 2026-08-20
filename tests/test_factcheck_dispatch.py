"""Fact-check senkron/batch eşik seçimi ve kullanıcı mesajları."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from utils.factcheck_dispatch import (
    BATCH_ID_PENDING,
    BATCH_RETRIEVE_CMD,
    choose_factcheck_method,
    build_factcheck_dispatch,
    format_factcheck_user_message,
)


def test_choose_sync_when_both_under_threshold():
    assert choose_factcheck_method(120, 3) == "sync"
    assert choose_factcheck_method(200, 5) == "sync"


def test_choose_batch_when_claims_over():
    assert choose_factcheck_method(201, 5) == "batch"


def test_choose_batch_when_videos_over():
    assert choose_factcheck_method(100, 6) == "batch"


def test_boundary_5v_200c_sync_message(capsys):
    info = build_factcheck_dispatch(n_claims=200, n_videos=5)
    assert info["method"] == "sync"
    assert info["estimated_minutes"] == 67
    print(info["user_message"])
    out = capsys.readouterr().out.strip()
    assert out == (
        "200 iddia işleniyor, tahmini süre: ~67 dakika. Sonuç bu ekranda "
        "görünecek, lütfen bekleyin."
    )


def test_boundary_5v_201c_batch_message(capsys):
    info = build_factcheck_dispatch(n_claims=201, n_videos=5, batch_id="msg_abc")
    assert info["method"] == "batch"
    print(info["user_message"])
    out = capsys.readouterr().out.strip()
    assert out.startswith("201 iddia toplu işleme kuyruğuna alındı.")
    assert BATCH_RETRIEVE_CMD in out
    assert "msg_abc" in out


def test_boundary_6v_100c_batch_message(capsys):
    info = build_factcheck_dispatch(n_claims=100, n_videos=6, batch_id="msg_abc")
    assert info["method"] == "batch"
    print(info["user_message"])
    out = capsys.readouterr().out.strip()
    assert out.startswith("100 iddia toplu işleme kuyruğuna alındı.")
    assert BATCH_RETRIEVE_CMD in out
    assert "msg_abc" in out


def test_choose_batch_large_job():
    assert choose_factcheck_method(500, 10) == "batch"


def test_choose_without_video_count_uses_claims_only():
    assert choose_factcheck_method(200, None) == "sync"
    assert choose_factcheck_method(201, None) == "batch"


def test_sync_message_verbatim(capsys):
    info = build_factcheck_dispatch(n_claims=120, n_videos=3)
    assert info["method"] == "sync"
    assert info["estimated_minutes"] == 40
    print(info["user_message"])
    out = capsys.readouterr().out.strip()
    assert out == (
        "120 iddia işleniyor, tahmini süre: ~40 dakika. Sonuç bu ekranda "
        "görünecek, lütfen bekleyin."
    )


def test_batch_message_verbatim_with_id(capsys):
    info = build_factcheck_dispatch(n_claims=500, n_videos=10, batch_id="msg_abc")
    assert info["method"] == "batch"
    assert info["estimated_minutes"] is None
    print(info["user_message"])
    out = capsys.readouterr().out.strip()
    expected = (
        "500 iddia toplu işleme kuyruğuna alındı. Anthropic'in işleme süresi "
        "garantisi yok — genelde birkaç dakika-birkaç saat içinde biter, ama "
        "teoride 24 saate kadar sürebilir. Otomatik bildirim GELMEZ, kontrol "
        f"etmeniz gerekir:\n  {BATCH_RETRIEVE_CMD}\n"
        "Gece gönderdiyseniz, ertesi gün kontrol etmenizi öneririz. Batch ID:\n"
        "msg_abc (bu ID'yi kaybetmeyin, durumu bununla sorgulayabilirsiniz)."
    )
    assert out == expected


def test_batch_message_placeholder_id():
    msg = format_factcheck_user_message("batch", 500)
    assert BATCH_ID_PENDING in msg
    assert BATCH_RETRIEVE_CMD in msg
