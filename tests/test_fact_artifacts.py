"""ArtifactStore 단위테스트 — 저장/로드/슬러그/캐싱. 디스크는 tmp_path 만 사용."""

from __future__ import annotations

import json

from contentcompare.fact.artifacts import ArtifactStore


def test_save_creates_file_utf8(tmp_path):
    store = ArtifactStore(str(tmp_path), "기준.xlsx")
    p = store.save("physical_raw", {"doc_type": "excel", "title": "충전환경온도"})
    assert p is not None and p.exists()
    assert p == tmp_path / "기준_xlsx" / "physical_raw.json"
    assert "충전환경온도" in p.read_text(encoding="utf-8")  # 한글 보존(ensure_ascii=False)


def test_load_roundtrip(tmp_path):
    store = ArtifactStore(str(tmp_path), "a.docx")
    data = {"a": 1, "b": ["x", "y"]}
    store.save("compact_raw", data)
    assert store.load("compact_raw") == data


def test_load_missing_returns_none(tmp_path):
    store = ArtifactStore(str(tmp_path), "a.docx")
    assert store.load("nope") is None
    assert store.exists("nope") is False


def test_disabled_store_no_write(tmp_path):
    store = ArtifactStore(str(tmp_path), "a.docx", enabled=False)
    assert store.save("physical_raw", {"x": 1}) is None
    assert not store.exists("physical_raw")
    assert not (tmp_path / "a_docx").exists()


def test_slug_sanitizes():
    assert ArtifactStore.slug("요약.pptx") == "요약_pptx"
    assert ArtifactStore.slug("기준 문서.xlsx") == "기준_문서_xlsx"
    # 전체 경로를 줘도 basename 만 슬러그화.
    assert ArtifactStore.slug("C:/dir/sub/deck.pptx") == "deck_pptx"


def test_cached_or_compute_skips_recompute(tmp_path):
    store = ArtifactStore(str(tmp_path), "a.xlsx")
    calls = {"n": 0}

    def compute():
        calls["n"] += 1
        return {"v": 42}

    first = store.cached_or_compute("profile", compute)
    second = store.cached_or_compute("profile", compute)
    assert first == second == {"v": 42}
    assert calls["n"] == 1  # 2회차는 디스크 캐시 히트 → compute 미호출


def test_cached_or_compute_fingerprint_mismatch_recomputes(tmp_path):
    store = ArtifactStore(str(tmp_path), "a.xlsx")
    calls = {"n": 0}

    def compute():
        calls["n"] += 1
        return {"n": calls["n"]}

    store.cached_or_compute("profile", compute, fingerprint="v1")
    # 같은 지문 → 캐시 히트(미호출).
    store.cached_or_compute("profile", compute, fingerprint="v1")
    assert calls["n"] == 1
    # 지문 변경 → 재계산.
    out = store.cached_or_compute("profile", compute, fingerprint="v2")
    assert calls["n"] == 2 and out == {"n": 2}


def test_cache_off_always_computes(tmp_path):
    store = ArtifactStore(str(tmp_path), "a.xlsx", cache=False)
    calls = {"n": 0}

    def compute():
        calls["n"] += 1
        return {"v": calls["n"]}

    store.cached_or_compute("profile", compute)
    store.cached_or_compute("profile", compute)
    assert calls["n"] == 2  # 캐시 off → 매번 compute


def test_saved_artifact_is_clean_json(tmp_path):
    # 산출물 파일에는 캐시 메타가 섞이지 않는다(사이드카로 분리).
    store = ArtifactStore(str(tmp_path), "a.xlsx")
    store.cached_or_compute("profile", lambda: {"doc_type": "excel"}, fingerprint="fp")
    raw = json.loads(store.path("profile").read_text(encoding="utf-8"))
    assert raw == {"doc_type": "excel"}
    assert "_fingerprint" not in raw
    assert (store.dir / "profile.fingerprint").read_text(encoding="utf-8") == "fp"
