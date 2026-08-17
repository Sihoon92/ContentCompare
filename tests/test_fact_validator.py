"""F4a Rule Validator 테스트 — 전부 순수 코드(LLM/COM/네트워크 불필요)."""

from __future__ import annotations

from contentcompare.fact.fact_models import Fact, FactSet
from contentcompare.fact.record_models import Attribute
from contentcompare.fact.schema_models import ColumnSchema, ColumnSpec
from contentcompare.fact.validator import ERROR, WARN, validate_facts

_EXCEL_COMPACT = {
    "doc_type": "excel",
    "sheets": [{
        "sheet_name": "데이터",
        "rows": [
            {"r": 17, "cells": {"E": "충전환경온도", "F": -5, "G": 35, "H": 85}},
            {"r": 18, "cells": {"E": "방전환경온도", "F": -30, "G": 30, "H": 90}},
        ],
    }],
}

_WORD_COMPACT = {
    "doc_type": "word",
    "blocks": [{"id": "w_b005", "type": "paragraph", "text": "정격 충전 전압은 4.55V 이다."}],
}

_PPT_COMPACT = {
    "doc_type": "ppt",
    "slides": [{
        "slide_no": 2,
        "shapes": [{"id": "p002_s002", "type": "text", "text": "충전환경온도 -5 35 80 ℃"}],
        "notes": "",
    }],
}


def _excel_fact(**over) -> Fact:
    base = dict(
        fact_id="fact-row-17",
        entity_name="충전환경온도",
        attributes={
            "lower_limit": Attribute(-5, "℃"),
            "target_value": Attribute(35, "℃"),
            "upper_limit": Attribute(85, "℃"),
        },
        source={"doc_type": "excel", "sheet": "데이터", "row": 17, "cell_range": "E17:H17"},
        evidence_text="충전환경온도, -5, 35, 85",
    )
    base.update(over)
    return Fact(**base)


def _checks(report, name):
    return [c for c in report.checks if c.check == name]


# --------------------------------------------------------------------------- #
# 정상 fact 는 아무 지적도 받지 않는다
# --------------------------------------------------------------------------- #
def test_clean_fact_has_no_findings():
    report = validate_facts(FactSet(facts=[_excel_fact()]), _EXCEL_COMPACT)
    assert report.checks == []
    assert report.low_confidence_ids == set()
    assert report.to_dict()["overall"] == {
        "facts": 1, "error": 0, "warn": 0, "low_confidence": 0,
    }


# --------------------------------------------------------------------------- #
# quant_bounds
# --------------------------------------------------------------------------- #
def test_bounds_violation_is_error():
    fact = _excel_fact(attributes={
        "lower_limit": Attribute(85, "℃"),   # 하한 > 상한
        "upper_limit": Attribute(-5, "℃"),
    })
    report = validate_facts(FactSet(facts=[fact]), _EXCEL_COMPACT)
    bounds = _checks(report, "quant_bounds")
    assert len(bounds) == 1 and bounds[0].severity == ERROR
    assert "lower_limit(85.0) > upper_limit(-5.0)" in bounds[0].reason
    assert fact.fact_id in report.low_confidence_ids


def test_bounds_ignores_non_numeric():
    fact = _excel_fact(attributes={
        "lower_limit": Attribute("해당없음", ""), "upper_limit": Attribute(55, "℃"),
    })
    assert _checks(validate_facts(FactSet(facts=[fact]), _EXCEL_COMPACT), "quant_bounds") == []


# --------------------------------------------------------------------------- #
# unit_missing — 기준 문서 전건이 걸리므로 warn 이어야 한다
# --------------------------------------------------------------------------- #
def test_missing_unit_is_warn_not_error():
    fact = _excel_fact(attributes={"upper_limit": Attribute(85, "")})
    report = validate_facts(FactSet(facts=[fact]), _EXCEL_COMPACT)
    units = _checks(report, "unit_missing")
    assert len(units) == 1 and units[0].severity == WARN
    # warn 은 low_confidence 로 승격되지 않는다 — 전건이 여기 걸리기 때문.
    assert report.low_confidence_ids == set()


def test_text_attribute_without_unit_is_not_flagged():
    fact = _excel_fact(
        attributes={"정성규격": Attribute("SEC Req. ver.4.7", "")},
        evidence_text="충전환경온도",
    )
    assert _checks(validate_facts(FactSet(facts=[fact]), _EXCEL_COMPACT), "unit_missing") == []


def test_no_attributes_is_warn():
    fact = _excel_fact(attributes={})
    report = validate_facts(FactSet(facts=[fact]), _EXCEL_COMPACT)
    assert [c.severity for c in _checks(report, "no_attributes")] == [WARN]


# --------------------------------------------------------------------------- #
# evidence_missing — 토큰 포함률 기준(공백/구분자 차이에 강건)
# --------------------------------------------------------------------------- #
def test_evidence_with_different_separators_passes():
    """LLM 이 셀 구분자를 바꿔 옮겨도 실재하는 근거는 통과해야 한다."""
    fact = _excel_fact(evidence_text="충전환경온도 -5 35 85")
    assert _checks(validate_facts(FactSet(facts=[fact]), _EXCEL_COMPACT), "evidence_missing") == []


def test_fabricated_evidence_is_error():
    fact = _excel_fact(evidence_text="충전환경온도는 섭씨 200도까지 허용된다")
    report = validate_facts(FactSet(facts=[fact]), _EXCEL_COMPACT)
    ev = _checks(report, "evidence_missing")
    assert len(ev) == 1 and ev[0].severity == ERROR
    assert fact.fact_id in report.low_confidence_ids


def test_empty_evidence_is_error_when_fact_claims_values():
    fact = _excel_fact(evidence_text="")
    assert [c.severity for c in _checks(
        validate_facts(FactSet(facts=[fact]), _EXCEL_COMPACT), "evidence_missing"
    )] == [ERROR]


def test_empty_row_without_attributes_is_not_evidence_error():
    """원본이 항목명만 있는 빈 행이면(실측: deltaOCV) 근거가 없어도 정상이다.

    이걸 error 로 두면 low_confidence 가 붙어 F5 가 missing 대신 unknown 으로
    판정하게 되는데, 이 행들의 정답은 실제로 missing 이다.
    """
    fact = _excel_fact(attributes={}, evidence_text="")
    report = validate_facts(FactSet(facts=[fact]), _EXCEL_COMPACT)
    assert _checks(report, "evidence_missing") == []
    assert [c.check for c in report.checks] == ["no_attributes"]
    assert report.low_confidence_ids == set()


# --------------------------------------------------------------------------- #
# source_unresolvable — 좌표 할루시네이션
# --------------------------------------------------------------------------- #
def test_excel_row_not_in_sheet_is_error():
    fact = _excel_fact(source={"doc_type": "excel", "sheet": "데이터", "row": 999})
    report = validate_facts(FactSet(facts=[fact]), _EXCEL_COMPACT)
    src = _checks(report, "source_unresolvable")
    assert len(src) == 1 and src[0].severity == ERROR and "999" in src[0].reason


def test_word_block_id_must_exist():
    ok = Fact(fact_id="f1", entity_name="정격 충전 전압",
              attributes={"target_value": Attribute(4.55, "V")},
              source={"doc_type": "word", "block_ids": ["w_b005"]},
              evidence_text="정격 충전 전압은 4.55V 이다.")
    ghost = Fact(fact_id="f2", entity_name="x",
                 attributes={"target_value": Attribute(4.55, "V")},
                 source={"doc_type": "word", "block_ids": ["w_b999"]},
                 evidence_text="정격 충전 전압은 4.55V 이다.")
    report = validate_facts(FactSet(facts=[ok, ghost]), _WORD_COMPACT)
    assert [c.fact_id for c in _checks(report, "source_unresolvable")] == ["f2"]


def test_ppt_slide_and_shape_checked():
    fact = Fact(fact_id="f1", entity_name="충전환경온도",
                attributes={"upper_limit": Attribute(80, "℃")},
                source={"doc_type": "ppt", "slide_no": 9, "shape_ids": ["p002_s002"]},
                evidence_text="충전환경온도 -5 35 80 ℃")
    report = validate_facts(FactSet(facts=[fact]), _PPT_COMPACT)
    assert "슬라이드" in _checks(report, "source_unresolvable")[0].reason


# --------------------------------------------------------------------------- #
# role_duplicated (Excel 스키마)
# --------------------------------------------------------------------------- #
def test_duplicated_semantic_role_is_warn():
    schema = ColumnSchema(columns=[
        ColumnSpec(column="F", field_name="하한치", semantic_role="quantitative_lower_bound"),
        ColumnSpec(column="I", field_name="SPEC", semantic_role="quantitative_lower_bound"),
        ColumnSpec(column="M", field_name="비고", semantic_role="qualitative_spec"),
        ColumnSpec(column="N", field_name="Method", semantic_role="qualitative_spec"),
    ])
    report = validate_facts(FactSet(facts=[]), _EXCEL_COMPACT, column_schema=schema)
    dups = _checks(report, "role_duplicated")
    # 정량 역할 중복만 지적하고, 여러 열에 흔한 qualitative_spec 은 지적하지 않는다.
    assert len(dups) == 1 and dups[0].severity == WARN
    assert "F, I" in dups[0].reason


# --------------------------------------------------------------------------- #
# 리포트 집계
# --------------------------------------------------------------------------- #
def test_report_aggregates_by_check_and_severity():
    bad_bounds = _excel_fact(fact_id="a", attributes={
        "lower_limit": Attribute(85, ""), "upper_limit": Attribute(-5, ""),
    })
    no_unit = _excel_fact(fact_id="b", attributes={"upper_limit": Attribute(85, "")})
    report = validate_facts(FactSet(location="sheet=데이터", facts=[bad_bounds, no_unit]),
                            _EXCEL_COMPACT)
    d = report.to_dict()
    assert d["location"] == "sheet=데이터"
    assert d["overall"]["facts"] == 2
    assert d["overall"]["error"] == 1 and d["overall"]["low_confidence"] == 1
    assert d["by_check"]["quant_bounds"] == 1 and d["by_check"]["unit_missing"] == 2


# --------------------------------------------------------------------------- #
# numeric_coverage — 조건 뭉치기 감지
# --------------------------------------------------------------------------- #
def _fact(attrs, evidence):
    from contentcompare.fact.fact_models import Fact
    from contentcompare.fact.record_models import parse_attributes

    return Fact(fact_id="f1", entity_name="x",
                attributes=parse_attributes(attrs), evidence_text=evidence)


def test_numeric_coverage_flags_string_lump():
    """근거에 숫자가 많은데 속성에 수치가 없으면 축약을 의심한다."""
    from contentcompare.fact.validator import _check_numeric_coverage

    fact = _fact(
        {"temp_range_standard_cycle": {"value": "구간별 상이", "unit": ""}},
        "-5~5도씨, 0.1C(4.55V), 5~12도씨, 0.3C(4.55V) 12~15도씨, 0.8C(4.55V)",
    )
    checks = _check_numeric_coverage(fact)

    assert [c.check for c in checks] == ["numeric_coverage"]
    assert checks[0].severity == "warn"


def test_numeric_coverage_ignores_short_evidence():
    """숫자 한둘은 서술문에서 자연스럽다 — 켜지지 않아야 한다."""
    from contentcompare.fact.validator import _check_numeric_coverage

    fact = _fact({"note": {"value": "해당 없음", "unit": ""}},
                 "본 규격은 SEC Req. ver.4.7 을 따른다")

    assert _check_numeric_coverage(fact) == []


def test_numeric_coverage_passes_when_attributes_hold_numbers():
    """조건별로 나눠 담았으면 통과한다."""
    from contentcompare.fact.validator import _check_numeric_coverage

    fact = _fact(
        {"charge_temp_range_1": {"value": "-5~5", "unit": "℃"},
         "charge_rate_1": {"value": "0.1C", "unit": ""},
         "charge_temp_range_2": {"value": "5~12", "unit": "℃"},
         "charge_rate_2": {"value": "0.3C", "unit": ""}},
        "-5~5도씨, 0.1C(4.55V), 5~12도씨, 0.3C(4.55V)",
    )

    assert _check_numeric_coverage(fact) == []


def test_numeric_coverage_wired_into_report():
    """validate_facts 가 이 검사를 실제로 돌린다."""
    from contentcompare.fact.fact_models import FactSet
    from contentcompare.fact.validator import validate_facts

    fact = _fact(
        {"lump": {"value": "구간별 상이", "unit": ""}},
        "-5~5도씨, 0.1C(4.55V), 5~12도씨, 0.3C(4.55V) 12~15도씨, 0.8C(4.55V)",
    )
    report = validate_facts(FactSet(facts=[fact]), {"doc_type": "word", "blocks": []})

    assert any(c.check == "numeric_coverage" for c in report.checks)
