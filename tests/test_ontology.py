"""knowledge/ontology.yaml 로더 테스트 — 파일 IO 만, LLM 불필요."""

from contentcompare.fact.concept_models import DIFFERS_BY, SAME_AS
from contentcompare.fact.ontology import Ontology, load_ontology

YAML = """\
same_as:
  - names: ["고객 표준 버전", "문서 기준 규격"]
    reason: "둘 다 SEC Req. ver.4.7 을 가리킨다"
differs_by:
  - names: ["1개월저장온도", "표준환경온도"]
    axis: "측정조건"
    reason: "저장 조건과 상시 환경 조건은 다르다"
  - names: ["평가환경온도", "평가환경습도"]
    axis: "물리량"
"""


def _write(tmp_path, text: str) -> str:
    p = tmp_path / "ontology.yaml"
    p.write_text(text, encoding="utf-8")
    return str(p)


def test_missing_file_yields_empty_ontology(tmp_path):
    """온톨로지가 없는 상태가 정상 시작 경로다."""
    onto = load_ontology(str(tmp_path / "없음.yaml"))
    assert len(onto) == 0
    assert onto.relation_for("가", "나") is None


def test_same_as_pair_is_found_in_both_directions(tmp_path):
    onto = load_ontology(_write(tmp_path, YAML))
    rel, axis, reason = onto.relation_for("고객 표준 버전", "문서 기준 규격")
    assert (rel, axis) == (SAME_AS, "")
    assert "SEC Req" in reason
    assert onto.relation_for("문서 기준 규격", "고객 표준 버전")[0] == SAME_AS


def test_name_matching_ignores_spacing_and_symbols(tmp_path):
    """문서마다 '고객표준버전'/'고객 표준 버전' 처럼 표기가 흔들린다."""
    onto = load_ontology(_write(tmp_path, YAML))
    assert onto.relation_for("고객표준버전", "문서기준규격")[0] == SAME_AS


def test_differs_by_carries_axis(tmp_path):
    onto = load_ontology(_write(tmp_path, YAML))
    rel, axis, _ = onto.relation_for("1개월저장온도", "표준환경온도")
    assert (rel, axis) == (DIFFERS_BY, "측정조건")


def test_unrelated_pair_returns_none(tmp_path):
    onto = load_ontology(_write(tmp_path, YAML))
    assert onto.relation_for("공칭용량", "정격용량") is None


def test_differs_by_wins_over_same_as(tmp_path):
    """차단이 연결을 이긴다(설계 §2.3 비대칭 권한)."""
    text = (
        'same_as:\n  - names: ["가", "나"]\n'
        'differs_by:\n  - names: ["가", "나"]\n    axis: "기간"\n'
    )
    onto = load_ontology(_write(tmp_path, text))
    assert onto.relation_for("가", "나")[0] == DIFFERS_BY


def test_three_names_expand_to_all_pairs(tmp_path):
    text = 'same_as:\n  - names: ["가", "나", "다"]\n'
    onto = load_ontology(_write(tmp_path, text))
    assert onto.relation_for("나", "다")[0] == SAME_AS


def test_malformed_entries_are_skipped(tmp_path):
    """사람이 손으로 쓰는 파일이므로 깨진 항목이 전체를 죽이면 안 된다."""
    text = 'same_as:\n  - names: ["혼자"]\n  - "문자열"\n  - names: ["가", "나"]\n'
    onto = load_ontology(_write(tmp_path, text))
    assert len(onto) == 1
    assert onto.relation_for("가", "나")[0] == SAME_AS


# --------------------------------------------------------------------- #
# aliases — 같은 개념의 여러 표기(다국어)
# --------------------------------------------------------------------- #
ALIAS_YAML = """\
aliases:
  - ["표준환경온도", "Standard ambient temperature"]
differs_by:
  - names: ["1개월저장온도", "표준환경온도"]
    axis: "측정조건"
    reason: "저장 조건과 상시 환경 조건은 다르다"
"""


def test_alias_members_are_same_as(tmp_path):
    onto = load_ontology(_write(tmp_path, ALIAS_YAML))
    assert onto.relation_for("표준환경온도", "Standard ambient temperature")[0] == SAME_AS


def test_relation_expands_across_alias_group(tmp_path):
    """번역어에도 차단이 걸려야 한다 — 이것이 aliases 를 만든 이유다.

    영어 대상 문서 실측(2026-08-05)에서 온톨로지 키가 한국어라 승격한 차단 3건이
    전부 적용되지 않았다.
    """
    onto = load_ontology(_write(tmp_path, ALIAS_YAML))
    rel, axis, _ = onto.relation_for("1개월저장온도", "Standard ambient temperature")
    assert (rel, axis) == (DIFFERS_BY, "측정조건")


def test_same_group_pair_in_differs_by_is_ignored(tmp_path):
    """같은 별칭 그룹끼리는 differs_by 로 뒤집히지 않는다.

    사람이 실수로 번역어 쌍을 differs_by 에 적으면(혹은 확장이 그룹 내부까지 훑으면)
    같은 개념이 영원히 비교되지 않는다. 별칭 선언이 이긴다.
    """
    text = (
        'aliases:\n  - ["표준환경온도", "Standard ambient temperature"]\n'
        'differs_by:\n  - names: ["표준환경온도", "Standard ambient temperature"]\n'
        '    axis: "언어"\n'
    )
    onto = load_ontology(_write(tmp_path, text))
    assert onto.relation_for("표준환경온도", "Standard ambient temperature")[0] == SAME_AS


def test_differs_by_still_wins_after_alias_expansion(tmp_path):
    """확장 후에도 '차단이 연결을 이긴다'(설계 §2.3)가 유지된다."""
    text = (
        'aliases:\n  - ["가", "ga"]\n'
        'same_as:\n  - names: ["가", "나"]\n'
        'differs_by:\n  - names: ["가", "나"]\n    axis: "기간"\n'
    )
    onto = load_ontology(_write(tmp_path, text))
    assert onto.relation_for("가", "나")[0] == DIFFERS_BY
    assert onto.relation_for("ga", "나")[0] == DIFFERS_BY


def test_alias_entry_accepts_bare_list_and_names_mapping(tmp_path):
    """손으로 쓰는 파일이라 두 표기를 모두 받는다."""
    text = 'aliases:\n  - ["가", "ga"]\n  - names: ["나", "na"]\n'
    onto = load_ontology(_write(tmp_path, text))
    assert onto.relation_for("가", "ga")[0] == SAME_AS
    assert onto.relation_for("나", "na")[0] == SAME_AS


def test_alias_summary_shows_human_spelling_not_normalized(tmp_path):
    """프롬프트에 정규화 이름이 새어 나가면 안 된다."""
    onto = load_ontology(_write(tmp_path, ALIAS_YAML))
    text = onto.summary()
    assert "Standard ambient temperature" in text
    assert "standardambienttemperature" not in text


def test_summary_lists_pairs_for_prompt(tmp_path):
    onto = load_ontology(_write(tmp_path, YAML))
    text = onto.summary()
    assert "고객 표준 버전" in text and "측정조건" in text


def test_empty_ontology_summary_is_empty_string():
    assert Ontology().summary() == ""


def test_non_mapping_yaml_list_yields_empty_ontology(tmp_path):
    """상위가 mapping 이 아닌 리스트면 빈 온톨로지를 반환한다."""
    text = "- foo\n- bar\n"
    onto = load_ontology(_write(tmp_path, text))
    assert len(onto) == 0
    assert onto.relation_for("가", "나") is None


def test_non_mapping_yaml_scalar_yields_empty_ontology(tmp_path):
    """상위가 bare scalar 면 빈 온톨로지를 반환한다."""
    text = "just a string"
    onto = load_ontology(_write(tmp_path, text))
    assert len(onto) == 0
    assert onto.relation_for("가", "나") is None
