"""``--out`` 상위 폴더 준비 테스트.

`out/` 은 .gitignore 대상이라 새로 clone/pull 한 환경에는 존재하지 않는다.
쓰기 시점에 폴더를 만들면 수 분짜리 실행이 끝난 뒤 FileNotFoundError 로 결과를
통째로 잃으므로, 실행 전에 만들어 둔다.
"""

import os

from contentcompare.cli import _ensure_out_dir


def test_creates_missing_parent_dir(tmp_path):
    out = tmp_path / "out" / "fact_report.md"
    _ensure_out_dir(str(out))
    assert out.parent.is_dir()
    out.write_text("ok", encoding="utf-8")  # 실제로 쓸 수 있어야 의미가 있다


def test_creates_nested_dirs(tmp_path):
    out = tmp_path / "a" / "b" / "c" / "report.md"
    _ensure_out_dir(str(out))
    assert out.parent.is_dir()


def test_existing_dir_is_kept(tmp_path):
    (tmp_path / "out").mkdir()
    keep = tmp_path / "out" / "keep.md"
    keep.write_text("keep", encoding="utf-8")
    _ensure_out_dir(str(tmp_path / "out" / "report.md"))
    assert keep.read_text(encoding="utf-8") == "keep"


def test_bare_filename_uses_cwd(tmp_path, monkeypatch):
    """폴더 없는 기본값(``report.md``)이어도 예외가 나지 않아야 한다."""
    monkeypatch.chdir(tmp_path)
    _ensure_out_dir("report.md")
    assert os.getcwd() == str(tmp_path)
