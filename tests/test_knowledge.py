"""도메인 지식(human-in-the-loop) 로딩/저장/주입 테스트(요청 5번)."""

from __future__ import annotations

from contentcompare import knowledge as kb


def test_save_and_list_and_read(tmp_path):
    base = str(tmp_path / "knowledge")
    path = kb.save_knowledge_file("domain", "formation = 화성 공정", base=base)
    assert path.endswith("domain.md")  # .md 자동 부여
    files = kb.list_knowledge_files(base)
    assert len(files) == 1
    assert kb.read_knowledge_file(files[0]) == "formation = 화성 공정"


def test_load_knowledge_merges_files_with_headers(tmp_path):
    base = str(tmp_path / "k")
    kb.save_knowledge_file("a.md", "용어 A", base=base)
    kb.save_knowledge_file("b.md", "용어 B", base=base)
    merged = kb.load_knowledge(base)
    assert "[a.md]" in merged and "[b.md]" in merged
    assert "용어 A" in merged and "용어 B" in merged


def test_load_knowledge_empty_dir_returns_blank(tmp_path):
    assert kb.load_knowledge(str(tmp_path / "none")) == ""


def test_load_knowledge_truncates(tmp_path):
    base = str(tmp_path / "k")
    kb.save_knowledge_file("big.md", "가" * 5000, base=base)
    merged = kb.load_knowledge(base, max_chars=100)
    assert len(merged) <= 130  # 100 + 생략 표시
    assert "생략" in merged


def test_prompt_block_wraps_non_empty():
    block = kb.knowledge_prompt_block("formation = 화성 공정")
    assert "도메인 지식" in block
    assert "formation = 화성 공정" in block


def test_prompt_block_empty_when_blank():
    assert kb.knowledge_prompt_block("   ") == ""
