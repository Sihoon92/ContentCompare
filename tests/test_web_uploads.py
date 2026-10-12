"""업로드 — 경로 조작 차단, 폴더 구조 보존, 이름 충돌 거절, 크기 제한."""

from __future__ import annotations

import io

import pytest

from contentcompare.web import uploads as U


@pytest.mark.parametrize("raw, expected", [
    ("기준.xlsx", "기준.xlsx"),
    ("자료\\하위\\규격서 v2.docx", "자료/하위/규격서 v2.docx"),
    ("C:\\Users\\x\\a.docx", "Users/x/a.docx"),
    ("/abs/./b.pptx", "abs/b.pptx"),
])
def test_safe_relpath_normalizes(raw, expected):
    assert U.safe_relpath(raw) == expected


@pytest.mark.parametrize("raw", [
    "", "   /", "../a.docx", "a/../../b.docx",
    "x/a:b.docx", "a/C:/b.docx", "//?/C:/x.docx", "a/.. /b.docx",
    "a/b.docx.", "a\x00b.docx", "CON.docx", "sub/nul.xlsx",
])
def test_safe_relpath_rejects(raw):
    with pytest.raises(U.UploadError):
        U.safe_relpath(raw)


def test_plan_keeps_folders_and_skips_lock_and_unsupported_files():
    plan = U.plan_upload("기준.xlsx", [
        "자료/규격서.docx", "자료/~$규격서.docx", "자료/메모.txt", "발표.pptx"])
    assert plan.reference == "기준.xlsx"
    assert plan.targets == ["자료/규격서.docx", "발표.pptx"]
    assert plan.skipped == ["자료/~$규격서.docx", "자료/메모.txt"]


def test_reference_must_be_excel():
    with pytest.raises(U.UploadError, match="Excel"):
        U.plan_upload("기준.docx", ["a.docx"])


def test_no_supported_target_is_an_error():
    with pytest.raises(U.UploadError, match="대상"):
        U.plan_upload("기준.xlsx", ["메모.txt"])


def test_same_basename_is_rejected_case_insensitive():
    """이름이 같으면 artifacts/<문서명>/ 이 겹쳐 결과가 조용히 섞인다."""
    with pytest.raises(U.UploadError, match="규격.docx"):
        U.plan_upload("기준.xlsx", ["a/규격.docx", "b/규격.DOCX"])
    with pytest.raises(U.UploadError, match="기준.xlsx"):
        U.plan_upload("기준.xlsx", ["사본/기준.xlsx"])


def test_resolve_under_normal_path(tmp_path):
    base = tmp_path / "uploads"
    result = U.resolve_under(base, "subfolder/doc.docx")
    assert result.is_relative_to(base.resolve())
    assert result.name == "doc.docx"


def test_resolve_under_rejects_escape_attempts(tmp_path):
    base = tmp_path / "uploads"
    with pytest.raises(U.UploadError):
        U.resolve_under(base, "../escape.docx")


def test_save_stream_writes_and_counts(tmp_path):
    dest = tmp_path / "inputs" / "targets" / "자료" / "a.docx"
    n = U.save_stream(io.BytesIO(b"abc" * 1000), dest, max_bytes=10_000, chunk_size=128)
    assert n == 3000 and dest.read_bytes() == b"abc" * 1000


def test_save_stream_over_limit_removes_partial_file(tmp_path):
    dest = tmp_path / "big.xlsx"
    with pytest.raises(U.UploadError, match="큽니다"):
        U.save_stream(io.BytesIO(b"x" * 5000), dest, max_bytes=4096, chunk_size=1024)
    assert not dest.exists()
