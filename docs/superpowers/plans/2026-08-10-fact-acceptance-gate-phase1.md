# Fact Acceptance Gate (Phase 1) Implementation Plan

> **상태: 구현 완료 (2026-08-10, 커밋 `43e2afc`·`6dffd54`·`228ea7e`·`f20f0d2`).**
> 아래 체크박스는 구현 당시 갱신되지 않았다. 실측과 `enforce` 전환 결정은 문서 끝의
> "실측 절차" 절에 있다 — **결론은 켜지 않는 것이다(2026-08-13).**

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `FactComparator.compare()` 를 LLM 을 안 부르는 `compare_code()` 와 `finalize()` 로 쪼개고 그 사이에 Acceptance Gate 를 넣어, 코드가 확정한 `match` 를 믿어도 되는지 채점하고 `unsafe_match_rate` 를 실측한다.

**Architecture:** `compare_code()` 가 `ComparisonProbe`(코드 판정까지만 끝난 중간 상태)를 만들고, `AcceptanceGate.evaluate()` 가 그것을 채점해 `review_triggers` 를 돌려주며, `finalize()` 가 최종 `FactComparison` 을 만든다. 기존 `compare()` 는 두 메서드를 잇는 래퍼로 남아 시그니처와 동작이 보존된다. 기본값은 shadow(`enforce=false`)라 채점만 남고 분기는 오늘 그대로다.

**Tech Stack:** Python 3.10+ · dataclasses · pytest. 새 의존성 없음.

## Global Constraints

- **shadow 가 기본값이다.** `fast_path.enforce = False` 에서 판정 결과와 LLM 호출 수가 게이트 도입 전과 **완전히 같아야** 한다. 이것이 깨지면 태스크 실패다.
- **F7 을 건드리지 않는다.** `fact_matcher.py` · `concept_builder.py` · `concept_models.py` · `concept_assembler.py` · `ontology.py` 는 수정 대상이 아니다.
- **`_decide_by_code()` / `_decide_by_llm()` / `_fallback()` 의 내부 로직은 수정하지 않는다.** 호출 순서만 두 메서드로 나눈다.
- **artifact 필드는 추가만 한다.** 기존 키를 바꾸거나 지우면 `missing_trace.py` · `artifact_reader.py` · `ui/micro_world.py` · `scripts/why_missing.py` 가 깨진다.
- **테스트는 Office·네트워크 없이 돈다.** 가짜 chat/embedder 주입 패턴(`tests/test_fact_pipeline_concept.py`)을 따른다.
- **코드/주석/문서는 한국어, 식별자는 영어.** 기존 파일의 주석 밀도와 어투를 맞춘다 — 이 코드베이스의 주석은 "무엇을"이 아니라 "왜 이 선택인가"를 적는다.
- 상위 스펙: `docs/superpowers/specs/2026-08-10-fact-acceptance-gate-phase1-design.md`

## File Structure

| 파일 | 책임 | 상태 |
|---|---|---|
| `contentcompare/config.py` | `FastPathConfig` 정의 + `AppConfig.from_dict` 중첩 파싱 | 수정 |
| `config/config.example.yaml` | `fact.fast_path` 노출과 주석 | 수정 |
| `contentcompare/fact/fact_comparator.py` | `attribute_coverage()` · `ComparisonProbe` · `compare_code()` / `finalize()` · `FactComparison` 필드 | 수정 |
| `contentcompare/fact/review_router.py` | 사유 상수 · `AcceptanceGate` · `gate_stats()` | **신규** |
| `contentcompare/fact/pipeline.py` | probe → gate → finalize 배선, 통계 병합 | 수정 |
| `tests/test_config_fast_path.py` | 설정 로딩 | **신규** |
| `tests/test_fact_comparator.py` | coverage · probe · finalize 회귀 | 수정 |
| `tests/test_review_router.py` | 게이트 규칙 · 통계 | **신규** |
| `tests/test_fact_gate_pipeline.py` | 배선 · shadow 무변경 · enforce | **신규** |

`attribute_coverage()` 와 `ComparisonProbe` 를 `fact_comparator.py` 에 두는 이유: `review_router` 가 `fact_comparator` 를 import 하므로 반대 방향 import 가 생기면 순환이 된다.

---

### Task 1: `FastPathConfig` 설정

**Files:**
- Modify: `contentcompare/config.py` (FactConfig 정의 앞에 dataclass 추가, `FactConfig` 에 필드 추가, `AppConfig.from_dict` L440-454)
- Modify: `config/config.example.yaml:148` 뒤
- Test: `tests/test_config_fast_path.py` (신규)

**Interfaces:**
- Produces: `contentcompare.config.FastPathConfig(enabled: bool = True, enforce: bool = False)`, `AppConfig.fact.fast_path: FastPathConfig`

- [ ] **Step 1: Write the failing test**

`tests/test_config_fast_path.py` 를 새로 만든다.

```python
"""fast_path 설정 로딩 — 중첩 dict 가 dataclass 로 파싱되는지.

``FactConfig(**data)`` 로만 두면 ``fast_path`` 가 **dict 인 채로** 들어가
``cfg.fact.fast_path.enforce`` 가 AttributeError 를 낸다. llm 의 ollama/internal
과 같은 중첩 파싱이 필요하다.
"""

from contentcompare.config import AppConfig, FastPathConfig


def test_defaults_to_shadow():
    """기본값이 shadow 인 것은 안전장치다 — enforce 는 LLM 호출을 늘린다."""
    cfg = AppConfig.from_dict({})
    assert cfg.fact.fast_path.enabled is True
    assert cfg.fact.fast_path.enforce is False


def test_nested_dict_is_parsed_into_dataclass():
    cfg = AppConfig.from_dict(
        {"fact": {"fast_path": {"enforce": True}, "match_top_k": 5}}
    )
    assert isinstance(cfg.fact.fast_path, FastPathConfig)
    assert cfg.fact.fast_path.enforce is True
    assert cfg.fact.fast_path.enabled is True   # 미지정 키는 기본값 유지
    assert cfg.fact.match_top_k == 5            # 형제 키가 소실되지 않는다


def test_missing_section_uses_defaults():
    cfg = AppConfig.from_dict({"fact": {"match_top_k": 7}})
    assert isinstance(cfg.fact.fast_path, FastPathConfig)
    assert cfg.fact.fast_path.enabled is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_config_fast_path.py -v`
Expected: FAIL — `ImportError: cannot import name 'FastPathConfig'`

- [ ] **Step 3: Add the dataclass**

`contentcompare/config.py` 에서 `class FactConfig:` 정의 **바로 앞**에 추가한다.

```python
@dataclass
class FastPathConfig:
    """F5 Acceptance Gate — 코드 판정 ``match`` 를 믿어도 되는지 채점한다.

    기본값이 shadow(``enforce=False``)인 것은 게이트가 LLM 호출을 **늘리기**
    때문이다. 오늘도 코드 ``match`` 는 LLM 을 안 부르므로(`fact_comparator`
    ``compare()``), 게이트의 실제 효과는 지금까지 조용히 확정되던 unsafe match 를
    LLM 으로 보내는 것이다. 얼마나 늘어날지는 ``unsafe_match_rate`` 를 실측하기
    전에는 알 수 없고, 모르는 채로 켜면 비용 회귀를 게이트 탓으로 돌리지 못한다.
    """

    enabled: bool = True
    """게이트 채점과 계측. False 면 게이트를 실행하지 않는다(도입 전과 동일)."""

    enforce: bool = False
    """True 면 게이트가 거부한 code ``match`` 를 LLM 판정으로 강등한다."""
```

- [ ] **Step 4: Wire it into `FactConfig`**

`FactConfig` 의 `max_llm_calls_per_concept` 필드 뒤에 추가한다.

```python
    # --- Fast Path 게이트(Phase 1) ---
    fast_path: FastPathConfig = field(default_factory=FastPathConfig)
```

- [ ] **Step 5: Add nested parsing to `AppConfig.from_dict`**

`contentcompare/config.py` L440-454. `fact=FactConfig(**data.get("fact", {}) or {})` 한 줄을 다음으로 바꾼다 — `llm` 의 `ollama`/`internal` 파싱과 같은 방식이다.

```python
    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AppConfig":
        llm_raw = dict(data.get("llm", {}))
        ollama = OllamaConfig(**llm_raw.pop("ollama", {}) or {})
        internal = InternalConfig(**llm_raw.pop("internal", {}) or {})
        langfuse = LangfuseConfig(**llm_raw.pop("langfuse", {}) or {})
        llm = LLMConfig(ollama=ollama, internal=internal, langfuse=langfuse, **llm_raw)
        # fact 도 중첩 섹션을 갖는다 — pop 하지 않으면 dict 인 채로 필드에 박힌다.
        fact_raw = dict(data.get("fact", {}) or {})
        fast_path = FastPathConfig(**fact_raw.pop("fast_path", {}) or {})
        return cls(
            llm=llm,
            excel=ExcelConfig(**data.get("excel", {}) or {}),
            similarity=SimilarityConfig(**data.get("similarity", {}) or {}),
            report=ReportConfig(**data.get("report", {}) or {}),
            knowledge=KnowledgeConfig(**data.get("knowledge", {}) or {}),
            fact=FactConfig(fast_path=fast_path, **fact_raw),
            logging=LoggingConfig(**data.get("logging", {}) or {}),
        )
```

- [ ] **Step 6: Run test to verify it passes**

Run: `pytest tests/test_config_fast_path.py -v`
Expected: 3 passed

- [ ] **Step 7: Document it in the example config**

`config/config.example.yaml` 의 `max_llm_calls_per_compare: 100` (L148) 줄 **뒤**, `# --- 개념 그래프(F7) ---` **앞**에 삽입한다.

```yaml
  # --- Fast Path 게이트(F5) ---
  fast_path:
    enabled: true                  # 코드 match 의 신뢰도 채점 + 계측
    enforce: false                 # true 면 게이트가 거부한 code match 를 LLM 에 넘긴다
                                   # 기본 false(shadow) — 켜면 LLM 호출이 늘어난다.
                                   # 얼마나 늘지는 리포트의 unsafe_match_rate 가 알려준다.
```

- [ ] **Step 8: Run the full suite to check nothing broke**

Run: `pytest -q`
Expected: 도입 전과 같은 결과(모두 통과)

- [ ] **Step 9: Commit**

```bash
git add contentcompare/config.py config/config.example.yaml tests/test_config_fast_path.py
git commit -m "feat(fact): fast_path 게이트 설정 추가(기본 shadow)"
```

---

### Task 2: `attribute_coverage()` 헬퍼

**Files:**
- Modify: `contentcompare/fact/fact_comparator.py` (`_compare_single_attributes` 정의 앞, 값·단위 비교 섹션 시작부)
- Test: `tests/test_fact_comparator.py` (파일 끝에 추가)

**Interfaces:**
- Consumes: `Fact`(`fact_models`), Task 1 없음
- Produces: `attribute_coverage(ref: Fact, target: Optional[Fact], *, confirmed_link: bool) -> float`

- [ ] **Step 1: Write the failing test**

`tests/test_fact_comparator.py` 파일 **끝**에 추가한다. 파일 상단 import 에 `attribute_coverage` 를 더한다.

```python
# --------------------------------------------------------------------------- #
# attribute_coverage — _decide_by_code 가 공통 속성만 보는 구멍을 수치화한다
# --------------------------------------------------------------------------- #
def test_coverage_is_partial_when_target_has_fewer_attributes():
    """기준 3속성 · 대상 1속성 → 그 하나가 같으면 match 지만 커버리지는 1/3."""
    ref = _fact("r", "전지", nominal=("3.85", "V"), upper=("4.55", "V"), lower=("3.0", "V"))
    tgt = _fact("t", "전지", nominal=("3.85", "V"))
    assert attribute_coverage(ref, tgt, confirmed_link=True) == pytest.approx(1 / 3)


def test_coverage_is_full_when_all_reference_attributes_are_covered():
    ref = _fact("r", "전지", nominal=("3.85", "V"))
    tgt = _fact("t", "전지", nominal=("3.85", "V"), extra=("1", ""))
    assert attribute_coverage(ref, tgt, confirmed_link=True) == 1.0


def test_coverage_without_reference_attributes_is_full():
    """비교할 속성이 없으면 커버리지 논의 자체가 무의미하다 — 게이트가 헛돌면 안 된다."""
    ref = _fact("r", "전지")
    tgt = _fact("t", "전지", nominal=("3.85", "V"))
    assert attribute_coverage(ref, tgt, confirmed_link=True) == 1.0


def test_coverage_without_target_is_zero():
    ref = _fact("r", "전지", nominal=("3.85", "V"))
    assert attribute_coverage(ref, None, confirmed_link=True) == 0.0


def test_single_attribute_pair_counts_as_full_when_link_is_confirmed():
    """양쪽 속성이 하나씩이면 키 이름이 달라도 같은 항목의 단일 값이다.

    실측 근거는 _compare_single_attributes 와 같다 — 기준이 공칭용량을 '하한치'
    열에 적어 lower_limit 이 됐고 대상은 target_value 였다.
    """
    ref = _fact("r", "용량", lower_limit=("1150", "mAh"))
    tgt = _fact("t", "용량", target_value=("1150", "mAh"))
    assert attribute_coverage(ref, tgt, confirmed_link=True) == 1.0


def test_single_attribute_exception_needs_a_confirmed_link():
    """LLM 이 만든 연결 위에서는 예외를 적용하지 않는다."""
    ref = _fact("r", "용량", lower_limit=("1150", "mAh"))
    tgt = _fact("t", "용량", target_value=("1150", "mAh"))
    assert attribute_coverage(ref, tgt, confirmed_link=False) == 0.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_fact_comparator.py -k coverage -v`
Expected: FAIL — `ImportError: cannot import name 'attribute_coverage'`

- [ ] **Step 3: Write the implementation**

`contentcompare/fact/fact_comparator.py` 의 `_OPPOSITE = frozenset(...)` 정의 **뒤**, `_compare_single_attributes` **앞**에 추가한다.

```python
def attribute_coverage(
    ref: Fact, target: Optional[Fact], *, confirmed_link: bool
) -> float:
    """기준 fact 의 속성 중 대상에서 대응된 비율.

    :meth:`FactComparator._decide_by_code` 는 **공통 속성만** 보기 때문에 기준에
    세 속성이 있고 대상에 하나뿐이어도 그 하나가 같으면 ``match`` 가 된다. 이
    함수가 그 구멍을 수치로 드러내고, Acceptance Gate 가 그것으로 라우팅한다.

    분모는 기준 fact 의 **전체** 속성이다. 값이 빈 속성을 빼는 느슨한 정의는
    placeholder 판별 규칙이 하나 더 필요해져 채택하지 않았다.
    """
    if target is None:
        return 0.0
    if not ref.attributes:
        return 1.0  # 비교할 속성이 없으면 커버리지 논의가 무의미하다
    if confirmed_link and len(ref.attributes) == 1 and len(target.attributes) == 1:
        # 근거는 _compare_single_attributes 와 같다 — 속성이 하나뿐인 fact 의 키
        # 이름은 원본 표의 **열 위치**에서 온 것이라 의미 구분이 아닌 경우가 많다.
        # 단 LLM 이 만든 연결 위에서는 적용하지 않는다(그 경우 low_confidence 가 붙는다).
        return 1.0
    covered = sum(1 for k in ref.attributes if k in target.attributes)
    return covered / len(ref.attributes)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_fact_comparator.py -k coverage -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add contentcompare/fact/fact_comparator.py tests/test_fact_comparator.py
git commit -m "feat(fact): attribute_coverage — 공통 속성만 보는 구멍을 수치화"
```

---

### Task 3: `ComparisonProbe` 와 `compare_code()` / `finalize()` 분리

**Files:**
- Modify: `contentcompare/fact/fact_comparator.py` (`FactComparison` 뒤에 dataclass 추가, `compare()` L128-170 재구성)
- Test: `tests/test_fact_comparator.py`

**Interfaces:**
- Consumes: Task 2 의 `attribute_coverage()`
- Produces:
  - `ComparisonProbe(reference_fact, target_doc, candidates, code_result, mismatch_attributes, code_reason, attribute_coverage, uncertain, missing_reason)` + `.best` property
  - `FactComparator.compare_code(ref, candidates, target, *, ref_low_confidence=False, missing_reason="") -> ComparisonProbe`
  - `FactComparator.finalize(probe, *, force_llm=False) -> FactComparison`
  - `FactComparator.compare(...)` 는 시그니처·동작 불변

- [ ] **Step 1: Write the failing test**

`tests/test_fact_comparator.py` 끝에 추가한다. import 에 `ComparisonProbe` 를 더한다.

```python
# --------------------------------------------------------------------------- #
# compare_code / finalize 분리 — probe 단계는 LLM 을 절대 부르지 않는다
# --------------------------------------------------------------------------- #
def test_compare_code_never_calls_llm_even_when_uncertain():
    """분리의 핵심 계약. 이게 깨지면 게이트가 채점하기 전에 비용이 나간다."""
    cmp_, chat = _comparator()
    ref = _fact("r", "전지", nominal=("3.85", "V"))
    tgt = _fact("t", "전지", nominal=("3.85", "무슨단위"))   # 단위 미상 → 코드 포기
    probe = cmp_.compare_code(ref, _cand(tgt, review=True), _target(tgt))
    assert chat.calls == 0
    assert probe.code_result is None          # 코드가 판단을 포기한 상태
    assert probe.uncertain is True


def test_compare_code_without_candidates_carries_missing_reason():
    cmp_, chat = _comparator()
    probe = cmp_.compare_code(
        _fact("r", "전지"), [], _target(), missing_reason="개념 연결이 없습니다."
    )
    assert chat.calls == 0
    assert probe.code_result == MISSING
    assert probe.code_reason == "개념 연결이 없습니다."
    assert probe.best is None


def test_compare_code_records_coverage_and_candidates():
    cmp_, _ = _comparator()
    ref = _fact("r", "전지", nominal=("3.85", "V"), upper=("4.55", "V"))
    tgt = _fact("t", "전지", nominal=("3.85", "V"))
    probe = cmp_.compare_code(ref, _cand(tgt), _target(tgt))
    assert probe.code_result == MATCH          # 공통 속성만 보면 일치
    assert probe.attribute_coverage == pytest.approx(0.5)
    assert probe.best is not None and probe.best.fact is tgt


def test_finalize_reproduces_compare():
    """compare() 는 compare_code() + finalize() 의 래퍼여야 한다."""
    cmp_, _ = _comparator()
    ref = _fact("r", "전지", nominal=("3.85", "V"))
    tgt = _fact("t", "전지", nominal=("3.85", "V"))
    direct = cmp_.compare(ref, _cand(tgt), _target(tgt))
    staged = cmp_.finalize(cmp_.compare_code(ref, _cand(tgt), _target(tgt)))
    assert (direct.result, direct.decided_by, direct.reason) == (
        staged.result, staged.decided_by, staged.reason
    )


def test_force_llm_demotes_a_code_match():
    """게이트가 거부한 match 를 LLM 으로 강등하는 경로(enforce 모드)."""
    cmp_, chat = _comparator({"result": "mismatch", "reason": "조건이 다릅니다"})
    ref = _fact("r", "전지", nominal=("3.85", "V"))
    tgt = _fact("t", "전지", nominal=("3.85", "V"))
    probe = cmp_.compare_code(ref, _cand(tgt), _target(tgt))
    assert probe.code_result == MATCH
    out = cmp_.finalize(probe, force_llm=True)
    assert chat.calls == 1
    assert out.result == MISMATCH and out.decided_by == BY_LLM


def test_force_llm_falls_back_to_code_when_llm_is_off():
    """LLM 을 못 쓰면 강등해도 코드 판정을 버리지 않는다(§6.2 보류 원칙)."""
    cmp_ = FactComparator(runner=None, use_llm=False)
    ref = _fact("r", "전지", nominal=("3.85", "V"))
    tgt = _fact("t", "전지", nominal=("3.85", "V"))
    probe = cmp_.compare_code(ref, _cand(tgt), _target(tgt))
    out = cmp_.finalize(probe, force_llm=True)
    assert out.result == MATCH and out.decided_by == BY_CODE
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_fact_comparator.py -k "compare_code or finalize or force_llm" -v`
Expected: FAIL — `ImportError: cannot import name 'ComparisonProbe'`

- [ ] **Step 3: Add the `ComparisonProbe` dataclass**

`contentcompare/fact/fact_comparator.py` 의 `_side_dict()` 함수 **뒤**, `class FactComparator` **앞**에 추가한다.

```python
@dataclass
class ComparisonProbe:
    """코드 판정까지만 끝낸 중간 상태 — **LLM 을 거치지 않았다**.

    ``compare()`` 안에 묻혀 있던 "코드 판정"과 "확정"을 갈라, 그 사이에
    Acceptance Gate 가 들어올 자리를 만든다. 게이트를 사후(판정이 끝난 뒤)에
    채점하지 않는 이유는 :meth:`FactComparator._decide_by_llm` 이 후보를 교체할 수
    있어, 사후 채점은 **코드 판정 시점과 다른 후보**를 채점하게 되기 때문이다.

    ``code_result`` 의 ``None`` 과 ``"unknown"`` 은 다르다 — ``None`` 은 코드가
    판단을 **포기**했다(단위 등가 미상 등)는 뜻이고, ``unknown`` 은 최종 판정
    라벨이다. 둘을 합치면 "코드가 못 정한 것"과 "사람이 봐야 하는 것"이 섞인다.
    """

    reference_fact: Fact
    target_doc: str
    candidates: list[MatchCandidate] = field(default_factory=list)
    code_result: Optional[str] = None
    mismatch_attributes: list[str] = field(default_factory=list)
    code_reason: str = ""
    attribute_coverage: float = 0.0
    uncertain: bool = False
    """후보 점수 경계·LLM 이 만든 연결·F4a 저신뢰 중 하나라도 해당."""
    missing_reason: str = ""

    @property
    def best(self) -> Optional[MatchCandidate]:
        return self.candidates[0] if self.candidates else None
```

- [ ] **Step 4: Replace `compare()` with the split**

`contentcompare/fact/fact_comparator.py` L128-170 의 `compare()` 메서드 **전체**를 다음 세 메서드로 교체한다. `_decide_by_code` 이하는 손대지 않는다.

```python
    # ------------------------------------------------------------------ #
    def compare(
        self,
        ref: Fact,
        candidates: list[MatchCandidate],
        target: DocFacts,
        *,
        ref_low_confidence: bool = False,
        missing_reason: str = "",
    ) -> FactComparison:
        """기준 fact 1건을 대상 문서 1개와 대조한다(코드 판정 → 확정을 한 번에).

        ``missing_reason`` 은 후보가 하나도 없을 때의 사유를 **호출자가 주입**하는
        자리다. 후보가 왜 없는지는 매칭 전략마다 다르다 — 유사도 경로는 임계 미달,
        F7 개념 경로는 "개념이 ``same_as`` 로 이어지지 않음"이다. 비우면 유사도
        경로의 기본 문구를 쓴다(롤백 경로 ``use_concept_graph: false`` 가 그대로 사용).

        Acceptance Gate 를 끼우려면 :meth:`compare_code` 와 :meth:`finalize` 를
        직접 부른다. 이 메서드는 그 둘을 잇는 호환 래퍼다.
        """
        return self.finalize(self.compare_code(
            ref, candidates, target,
            ref_low_confidence=ref_low_confidence,
            missing_reason=missing_reason,
        ))

    def compare_code(
        self,
        ref: Fact,
        candidates: list[MatchCandidate],
        target: DocFacts,
        *,
        ref_low_confidence: bool = False,
        missing_reason: str = "",
    ) -> ComparisonProbe:
        """코드 판정까지만 한다 — **LLM 을 절대 호출하지 않는다.**

        이 계약이 깨지면 게이트가 채점하기도 전에 비용이 나가므로, 테스트로 고정한다.
        """
        if not candidates:
            return ComparisonProbe(
                reference_fact=ref,
                target_doc=target.doc_name,
                code_result=MISSING,
                code_reason=missing_reason.strip() or MISSING_BY_SIMILARITY,
                missing_reason=missing_reason,
            )

        best = candidates[0]
        probe = ComparisonProbe(
            reference_fact=ref,
            target_doc=target.doc_name,
            candidates=candidates,
            attribute_coverage=attribute_coverage(
                ref, best.fact, confirmed_link=not best.needs_review
            ),
            uncertain=(
                best.needs_review
                or ref_low_confidence
                or target.is_low_confidence(best.fact)
            ),
            missing_reason=missing_reason,
        )
        verdict = self._decide_by_code(ref, best.fact)
        if verdict is not None:
            probe.code_result, probe.mismatch_attributes, probe.code_reason = verdict
        return probe

    def finalize(
        self, probe: ComparisonProbe, *, force_llm: bool = False
    ) -> FactComparison:
        """probe 를 최종 판정으로 만든다. 필요할 때만(그리고 그때만) LLM 을 부른다.

        ``force_llm`` 은 Acceptance Gate 가 거부한 코드 ``match`` 를 강등하는
        자리다(``fast_path.enforce``). 기본 False 에서는 분리 이전과 동작이 같다.
        """
        if not probe.candidates:
            return FactComparison(
                reference_fact=probe.reference_fact,
                target_doc=probe.target_doc,
                result=MISSING,
                reason=probe.code_reason,
            )

        best = probe.candidates[0]
        out = FactComparison(
            reference_fact=probe.reference_fact,
            target_doc=probe.target_doc,
            target_fact=best.fact,
            match_score=best.score,
            match_method=best.method,
        )
        verdict = (
            None if probe.code_result is None
            else (probe.code_result, probe.mismatch_attributes, probe.code_reason)
        )
        if verdict is not None and not probe.uncertain and not force_llm:
            out.result, out.mismatch_attributes, out.reason = verdict
            return out

        # 코드가 단정하지 못했거나 근거가 불안정하거나 게이트가 거부 → LLM(없으면 보류).
        return self._decide_by_llm(
            out, probe.reference_fact, probe.candidates, verdict, probe.uncertain
        )
```

- [ ] **Step 5: Run the new tests**

Run: `pytest tests/test_fact_comparator.py -k "compare_code or finalize or force_llm" -v`
Expected: 6 passed

- [ ] **Step 6: Run the whole comparator suite for regressions**

Run: `pytest tests/test_fact_comparator.py -v`
Expected: 전부 통과 — 기존 테스트가 하나라도 깨지면 분리가 동작을 바꾼 것이다. 되돌려 원인을 찾는다.

- [ ] **Step 7: Run the full suite**

Run: `pytest -q`
Expected: 도입 전과 같은 결과

- [ ] **Step 8: Commit**

```bash
git add contentcompare/fact/fact_comparator.py tests/test_fact_comparator.py
git commit -m "refactor(fact): compare() 를 compare_code()+finalize() 로 분리"
```

---

### Task 4: `FactComparison` 판정 이력 필드

**Files:**
- Modify: `contentcompare/fact/fact_comparator.py` (`FactComparison` dataclass L70-97)
- Test: `tests/test_fact_comparator.py`

**Interfaces:**
- Produces: `FactComparison.initial_result: str`, `.review_triggers: list[str]`, `.attribute_coverage: float`, `.result_changed: bool` (property), `.safe_to_finalize: bool` (property), `to_dict()` 에 같은 5키

- [ ] **Step 1: Write the failing test**

`tests/test_fact_comparator.py` 끝에 추가한다.

```python
# --------------------------------------------------------------------------- #
# 판정 이력 — 2차 검사가 1차를 조용히 덮지 않게 하는 기반
# --------------------------------------------------------------------------- #
def test_to_dict_adds_history_fields_and_keeps_existing_keys():
    cmp_, _ = _comparator()
    ref = _fact("r", "전지", nominal=("3.85", "V"))
    tgt = _fact("t", "전지", nominal=("3.85", "V"))
    out = cmp_.compare(ref, _cand(tgt), _target(tgt))
    out.initial_result = MATCH
    out.review_triggers = ["duplicate_entity_facts"]
    out.attribute_coverage = 0.5

    d = out.to_dict()
    # 새 키
    assert d["initial_result"] == MATCH
    assert d["review_triggers"] == ["duplicate_entity_facts"]
    assert d["attribute_coverage"] == 0.5
    assert d["safe_to_finalize"] is False
    assert d["result_changed"] is False
    # 기존 소비자(missing_trace/artifact_reader/why_missing)가 쓰는 키는 그대로
    for key in ("entity_name", "target_doc", "result", "decided_by", "reason",
                "mismatch_attributes", "match_score", "match_method",
                "reference", "target"):
        assert key in d


def test_result_changed_detects_llm_overriding_code():
    cmp_, _ = _comparator()
    out = cmp_.compare(_fact("r", "전지"), [], _target())
    out.initial_result = MATCH          # 코드는 match 라 했는데
    out.result = MISMATCH               # 최종은 mismatch
    assert out.result_changed is True


def test_result_changed_is_false_without_initial_result():
    """initial_result 를 안 채운 호출부(롤백 경로)에서 오탐이 나면 안 된다."""
    cmp_, _ = _comparator()
    out = cmp_.compare(_fact("r", "전지"), [], _target())
    assert out.initial_result == ""
    assert out.result_changed is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_fact_comparator.py -k "history or result_changed" -v`
Expected: FAIL — `AttributeError: 'FactComparison' object has no attribute 'initial_result'`

- [ ] **Step 3: Add the fields**

`FactComparison` 의 `reason: str = ""` 필드 **뒤**에 추가한다.

```python
    # --- 판정 이력(Phase 1) — 2차 검사가 1차를 조용히 덮지 않게 하는 기반 --- #
    initial_result: str = ""
    """코드 판정. ``result`` 는 최종 판정이다. 비어 있으면 게이트를 안 거친 호출부."""
    review_triggers: list[str] = field(default_factory=list)
    """Acceptance Gate 가 붙인 검토 사유. 비면 안전하게 확정 가능."""
    attribute_coverage: float = 1.0

    @property
    def result_changed(self) -> bool:
        """코드 판정을 뒤엎었는가. 개선인지 새 오판인지 진단하려면 둘 다 있어야 한다."""
        return bool(self.initial_result) and self.initial_result != self.result

    @property
    def safe_to_finalize(self) -> bool:
        return not self.review_triggers
```

- [ ] **Step 4: Extend `to_dict()`**

`FactComparison.to_dict()` 의 반환 dict 에서 `"decided_by": self.decided_by,` 줄 **뒤**에 추가한다(기존 키는 건드리지 않는다).

```python
            "initial_result": self.initial_result,
            "review_triggers": list(self.review_triggers),
            "attribute_coverage": round(self.attribute_coverage, 4),
            "result_changed": self.result_changed,
            "safe_to_finalize": self.safe_to_finalize,
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/test_fact_comparator.py -k "history or result_changed" -v`
Expected: 3 passed

- [ ] **Step 6: Verify the existing artifact consumers still pass**

Run: `pytest tests/test_missing_trace.py tests/test_fact_artifact_reader.py tests/test_ui_micro_world.py tests/test_fact_report.py -q`
Expected: 전부 통과 — 필드를 **추가만** 했으므로 깨지면 안 된다.

- [ ] **Step 7: Commit**

```bash
git add contentcompare/fact/fact_comparator.py tests/test_fact_comparator.py
git commit -m "feat(fact): FactComparison 에 판정 이력 필드 추가"
```

---

### Task 5: `review_router.py` — Acceptance Gate 와 통계

**Files:**
- Create: `contentcompare/fact/review_router.py`
- Test: `tests/test_review_router.py` (신규)

**Interfaces:**
- Consumes: Task 3 의 `ComparisonProbe`, Task 4 의 `FactComparison` 이력 필드, Task 1 의 `FastPathConfig`
- Produces:
  - 사유 상수 `CODE_MISSING` · `CODE_MISMATCH` · `CODE_UNKNOWN` · `LOW_CONFIDENCE` · `PARTIAL_ATTRIBUTE_COVERAGE` · `DUPLICATE_ENTITY_FACTS` · `INVALID_EVIDENCE`, 순서 튜플 `REASON_ORDER`
  - `AcceptanceGate(cfg: FastPathConfig)` — `.enforce: bool`, `.evaluate(probe) -> list[str]`
  - `gate_stats(comparisons: list[FactComparison]) -> dict`

- [ ] **Step 1: Write the failing test**

`tests/test_review_router.py` 를 새로 만든다.

```python
"""F5 Acceptance Gate — 코드 match 를 믿어도 되는지 채점한다.

게이트는 **탐지가 아니라 라우팅**이다. "후보가 2건 이상이다" 같은 셀 수 있는
사실만 확인하고, "어느 후보가 맞는가"는 판단하지 않는다. LLM 도 Office 도 필요 없다.
"""

from __future__ import annotations

import pytest

from contentcompare.config import FastPathConfig
from contentcompare.fact.fact_comparator import (
    MATCH,
    MISMATCH,
    MISSING,
    ComparisonProbe,
    FactComparison,
)
from contentcompare.fact.fact_matcher import CONCEPT, MatchCandidate
from contentcompare.fact.fact_models import Fact
from contentcompare.fact.record_models import Attribute
from contentcompare.fact.review_router import (
    CODE_MISMATCH,
    CODE_MISSING,
    CODE_UNKNOWN,
    DUPLICATE_ENTITY_FACTS,
    INVALID_EVIDENCE,
    LOW_CONFIDENCE,
    PARTIAL_ATTRIBUTE_COVERAGE,
    REASON_ORDER,
    AcceptanceGate,
    gate_stats,
)


def _fact(fid="t", *, evidence="근거", source=None, **attrs) -> Fact:
    return Fact(
        fact_id=fid,
        entity_name="전지",
        attributes={k: Attribute(v, "V") for k, v in attrs.items()},
        evidence_text=evidence,
        source=source if source is not None else {"block_id": "w_b01"},
    )


def _cand(fact: Fact, review: bool = False) -> MatchCandidate:
    return MatchCandidate(fact, 0.9, CONCEPT, needs_review=review)


def _probe(*facts: Fact, code_result=MATCH, coverage=1.0, uncertain=False) -> ComparisonProbe:
    cands = [_cand(f) for f in facts]
    return ComparisonProbe(
        reference_fact=_fact("r", nominal="3.85"),
        target_doc="규격서.docx",
        candidates=cands,
        code_result=code_result,
        attribute_coverage=coverage,
        uncertain=uncertain,
    )


def _gate(**kw) -> AcceptanceGate:
    return AcceptanceGate(FastPathConfig(**kw))


# --------------------------------------------------------------------------- #
# 통과 조건
# --------------------------------------------------------------------------- #
def test_clean_single_candidate_match_passes():
    assert _gate().evaluate(_probe(_fact())) == []


def test_disabled_gate_never_reports_anything():
    """enabled=False 는 도입 전과 동일한 동작을 보장하는 탈출구다."""
    probe = _probe(_fact(evidence=""), code_result=MISMATCH, coverage=0.2)
    assert _gate(enabled=False).evaluate(probe) == []


# --------------------------------------------------------------------------- #
# 후보가 없을 때 — 사유는 하나뿐이어야 한다
# --------------------------------------------------------------------------- #
def test_no_candidate_yields_only_code_missing():
    """coverage 0.0 과 '대상 fact 없음'이 함께 붙으면 사유 통계가 무의미해진다."""
    probe = ComparisonProbe(
        reference_fact=_fact("r", nominal="3.85"),
        target_doc="규격서.docx",
        code_result=MISSING,
        attribute_coverage=0.0,
    )
    assert _gate().evaluate(probe) == [CODE_MISSING]


# --------------------------------------------------------------------------- #
# 개별 규칙
# --------------------------------------------------------------------------- #
def test_partial_coverage_is_flagged():
    assert _gate().evaluate(_probe(_fact(), coverage=0.5)) == [PARTIAL_ATTRIBUTE_COVERAGE]


def test_multiple_candidates_are_flagged():
    """같은 개념에 대상 fact 가 여럿이면 '어느 것이 맞는가'는 코드의 몫이 아니다."""
    assert _gate().evaluate(_probe(_fact("a"), _fact("b"))) == [DUPLICATE_ENTITY_FACTS]


def test_empty_evidence_text_is_flagged():
    """사람이 원문 대조로 검수할 수 없는 판정은 안전하다고 볼 수 없다."""
    assert _gate().evaluate(_probe(_fact(evidence="  "))) == [INVALID_EVIDENCE]


def test_empty_source_is_flagged():
    assert _gate().evaluate(_probe(_fact(source={}))) == [INVALID_EVIDENCE]


def test_code_mismatch_is_flagged():
    assert _gate().evaluate(_probe(_fact(), code_result=MISMATCH)) == [CODE_MISMATCH]


def test_undecided_code_is_flagged():
    assert _gate().evaluate(_probe(_fact(), code_result=None)) == [CODE_UNKNOWN]


def test_uncertain_probe_is_flagged():
    assert _gate().evaluate(_probe(_fact(), uncertain=True)) == [LOW_CONFIDENCE]


# --------------------------------------------------------------------------- #
# 누적과 순서
# --------------------------------------------------------------------------- #
def test_reasons_accumulate_in_fixed_order():
    """순서가 흔들리면 리포트·통계가 실행마다 달라 보인다."""
    probe = _probe(
        _fact("a", evidence=""), _fact("b"),
        code_result=MISMATCH, coverage=0.5, uncertain=True,
    )
    reasons = _gate().evaluate(probe)
    assert reasons == [
        CODE_MISMATCH, LOW_CONFIDENCE, PARTIAL_ATTRIBUTE_COVERAGE,
        DUPLICATE_ENTITY_FACTS, INVALID_EVIDENCE,
    ]
    assert reasons == [r for r in REASON_ORDER if r in reasons]


def test_enforce_flag_is_exposed():
    assert _gate().enforce is False
    assert _gate(enforce=True).enforce is True


# --------------------------------------------------------------------------- #
# 통계 — enforce 전환 비용을 켜기 전에 알려주는 것이 목적이다
# --------------------------------------------------------------------------- #
def _comparison(initial, final, triggers, coverage=1.0) -> FactComparison:
    return FactComparison(
        reference_fact=_fact("r"), target_doc="규격서.docx", result=final,
        initial_result=initial, review_triggers=list(triggers),
        attribute_coverage=coverage,
    )


def test_gate_stats_reports_rates():
    rows = [
        _comparison(MATCH, MATCH, []),                                   # 안전
        _comparison(MATCH, MATCH, [PARTIAL_ATTRIBUTE_COVERAGE], 0.5),    # unsafe match
        _comparison(MISMATCH, MISMATCH, [CODE_MISMATCH]),
        _comparison(MISSING, MISSING, [CODE_MISSING], 0.0),
    ]
    s = gate_stats(rows)
    assert s["fast_path_rate"] == pytest.approx(0.25)
    assert s["secondary_review_rate"] == pytest.approx(0.75)
    assert s["unsafe_match_rate"] == pytest.approx(0.5)   # code match 2건 중 1건
    assert s["review_reasons"] == {
        CODE_MISSING: 1, CODE_MISMATCH: 1, PARTIAL_ATTRIBUTE_COVERAGE: 1,
    }
    assert s["mean_attribute_coverage"] == pytest.approx(0.8333, abs=1e-4)  # missing 제외
    assert s["result_changed_count"] == 0


def test_gate_stats_counts_overridden_code_verdicts():
    rows = [_comparison(MATCH, MISMATCH, [PARTIAL_ATTRIBUTE_COVERAGE], 0.5)]
    assert gate_stats(rows)["result_changed_count"] == 1


def test_gate_stats_without_code_match_has_zero_unsafe_rate():
    """분모가 0일 때 나눗셈으로 죽지 않아야 한다."""
    rows = [_comparison(MISSING, MISSING, [CODE_MISSING], 0.0)]
    s = gate_stats(rows)
    assert s["unsafe_match_rate"] == 0.0
    assert s["mean_attribute_coverage"] == 0.0


def test_gate_stats_of_empty_input_is_empty():
    assert gate_stats([]) == {}


def test_reason_order_has_no_duplicates():
    assert len(REASON_ORDER) == len(set(REASON_ORDER)) == 7
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_review_router.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'contentcompare.fact.review_router'`

- [ ] **Step 3: Write the module**

`contentcompare/fact/review_router.py` 를 새로 만든다.

```python
"""F5 Acceptance Gate — 코드가 확정한 ``match`` 를 믿어도 되는지 채점한다.

**게이트는 탐지가 아니라 라우팅이다.** 코드가 하는 일은 "후보가 2건 이상이다",
"커버리지가 1.0 미만이다" 같은 **셀 수 있는 사실**의 확인이고, "이 후보들 중 어느
것이 맞는가"는 판단하지 않는다 — 그것은 2차 Evidence 검사가 원문을 보고 할 일이다.
이 경계를 흐리면 게이트가 또 하나의 축약된 판정기가 되어, 지금 고치려는 문제를
그대로 재생산한다.

**이 단계는 비용을 줄이지 않는다.** 오늘도 코드 ``match`` 는 LLM 을 안 부르므로
(:meth:`FactComparator.finalize`), 게이트의 실제 효과는 지금까지 조용히 확정되던
unsafe match 를 LLM 으로 보내는 것이다. 그래서 기본값이 shadow 이고,
:func:`gate_stats` 의 ``unsafe_match_rate`` 가 "켜면 얼마나 늘어나는가"를 미리
알려주는 것이 이 모듈의 존재 이유다.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .fact_comparator import MATCH, MISMATCH, MISSING, ComparisonProbe, FactComparison

if TYPE_CHECKING:  # 런타임 import 를 피해 config ↔ fact 결합을 늘리지 않는다
    from ..config import FastPathConfig

CODE_MISSING = "code_missing"
CODE_MISMATCH = "code_mismatch"
CODE_UNKNOWN = "code_unknown"
LOW_CONFIDENCE = "low_confidence"
PARTIAL_ATTRIBUTE_COVERAGE = "partial_attribute_coverage"
DUPLICATE_ENTITY_FACTS = "duplicate_entity_facts"
INVALID_EVIDENCE = "invalid_evidence"

REASON_ORDER = (
    CODE_MISSING,
    CODE_MISMATCH,
    CODE_UNKNOWN,
    LOW_CONFIDENCE,
    PARTIAL_ATTRIBUTE_COVERAGE,
    DUPLICATE_ENTITY_FACTS,
    INVALID_EVIDENCE,
)
"""사유 순서 고정 — 리포트·통계가 실행마다 같은 순서로 재현되게 한다."""


class AcceptanceGate:
    """코드 판정 결과(:class:`ComparisonProbe`)를 채점해 검토 사유를 돌려준다."""

    def __init__(self, cfg: "FastPathConfig") -> None:
        self.enabled = bool(cfg.enabled)
        self.enforce = bool(cfg.enforce)

    def evaluate(self, probe: ComparisonProbe) -> list[str]:
        """검토가 필요한 사유들. 빈 리스트면 안전하게 확정할 수 있다."""
        if not self.enabled:
            return []
        if not probe.candidates:
            # 후보가 없으면 사유는 하나뿐이다. 나머지 규칙을 함께 평가하면 모든
            # missing 에 coverage 0.0 과 '대상 fact 없음'이 따라붙어, 사유 통계가
            # "거의 전부 partial_coverage" 로 보이면서 진짜 원인을 덮는다.
            return [CODE_MISSING]

        reasons: list[str] = []
        if probe.code_result == MISMATCH:
            reasons.append(CODE_MISMATCH)
        if probe.code_result is None:
            reasons.append(CODE_UNKNOWN)
        if probe.uncertain:
            reasons.append(LOW_CONFIDENCE)
        if probe.attribute_coverage < 1.0:
            reasons.append(PARTIAL_ATTRIBUTE_COVERAGE)
        if len(probe.candidates) > 1:
            reasons.append(DUPLICATE_ENTITY_FACTS)
        if _evidence_invalid(probe):
            reasons.append(INVALID_EVIDENCE)
        return reasons


def _evidence_invalid(probe: ComparisonProbe) -> bool:
    """사람이 원문 대조로 검수할 수 없는 판정은 안전하다고 볼 수 없다(설계 §6.2)."""
    best = probe.best
    if best is None:
        return True
    return not best.fact.evidence_text.strip() or not best.fact.source


def gate_stats(comparisons: list[FactComparison]) -> dict:
    """게이트 계측 — ``enforce`` 전환 비용을 켜기 전에 알려준다.

    ``unsafe_match_rate`` 가 핵심이다. 코드 ``match`` 중 게이트가 거부한 비율이
    그대로 "enforce 를 켜면 늘어날 LLM 호출 비율"이다.
    """
    total = len(comparisons)
    if not total:
        return {}

    safe = sum(1 for c in comparisons if c.safe_to_finalize)
    code_match = [c for c in comparisons if c.initial_result == MATCH]
    unsafe = sum(1 for c in code_match if not c.safe_to_finalize)

    reasons: dict[str, int] = {}
    for c in comparisons:
        for r in c.review_triggers:
            reasons[r] = reasons.get(r, 0) + 1

    # 후보가 없는 비교의 coverage 0.0 은 "커버리지가 낮다"가 아니라 "잴 대상이
    # 없다"이므로 평균에서 뺀다. 섞으면 missing 이 많은 문서에서 평균이 함께
    # 내려가 게이트가 과하게 조인 것처럼 보인다.
    measured = [c for c in comparisons if c.initial_result != MISSING]

    return {
        "fast_path_rate": round(safe / total, 4),
        "secondary_review_rate": round(1 - safe / total, 4),
        "unsafe_match_rate": round(unsafe / len(code_match), 4) if code_match else 0.0,
        "review_reasons": {k: reasons[k] for k in REASON_ORDER if k in reasons},
        "mean_attribute_coverage": (
            round(sum(c.attribute_coverage for c in measured) / len(measured), 4)
            if measured else 0.0
        ),
        "result_changed_count": sum(1 for c in comparisons if c.result_changed),
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_review_router.py -v`
Expected: 18 passed

- [ ] **Step 5: Commit**

```bash
git add contentcompare/fact/review_router.py tests/test_review_router.py
git commit -m "feat(fact): review_router — Acceptance Gate 와 게이트 계측"
```

---

### Task 6: 파이프라인 배선

**Files:**
- Modify: `contentcompare/fact/pipeline.py` (import 추가, `_compare_from_store()` L182-205)
- Test: `tests/test_fact_gate_pipeline.py` (신규)

**Interfaces:**
- Consumes: Task 1 `FastPathConfig` · Task 3 `compare_code`/`finalize` · Task 4 이력 필드 · Task 5 `AcceptanceGate`/`gate_stats`
- Produces: `comparison_result.json` 의 각 비교에 이력 5키, `compare_stats` 에 게이트 6키

- [ ] **Step 1: Write the failing test**

`tests/test_fact_gate_pipeline.py` 를 새로 만든다. 주입 패턴은 `tests/test_fact_pipeline_concept.py` 를 따른다.

```python
"""Acceptance Gate 파이프라인 배선 — 가짜 chat/임베더(COM·네트워크 불필요).

가장 중요한 계약은 **shadow 기본값에서 아무것도 변하지 않는다**는 것이다.
판정도, LLM 호출 수도 게이트 도입 전과 같아야 한다.
"""

from __future__ import annotations

import json

import pytest

from contentcompare.config import AppConfig, FactConfig, FastPathConfig
from contentcompare.fact.fact_models import Fact, FactSet
from contentcompare.fact.fact_store import DocFacts, FactStore
from contentcompare.fact.pipeline import FactPipeline
from contentcompare.fact.record_models import Attribute
from contentcompare.fact.review_router import PARTIAL_ATTRIBUTE_COVERAGE


class _ScriptedChat:
    def __init__(self, responses=()):
        self.responses = list(responses)
        self.calls = 0

    def complete(self, system, user, *, temperature=0.0):
        self.calls += 1
        return self.responses.pop(0) if self.responses else "{}"


class _FakeEmbedder:
    def embed(self, texts, kind="passage"):
        return [[1.0, 0.0] for _ in texts]


def _fact(fact_id: str, name: str, **attrs) -> Fact:
    return Fact(
        fact_id=fact_id,
        entity_name=name,
        search_text=name,
        evidence_text=f"{name} 근거",
        source={"block_id": "b01"},
        attributes={k: Attribute(v, "V") for k, v in attrs.items()},
    )


def _store() -> FactStore:
    """기준 2속성 · 대상 1속성 → 코드는 match, 커버리지는 0.5(= unsafe match)."""
    store = FactStore()
    store.add(DocFacts(doc_name="기준.xlsx", facts=FactSet(facts=[
        _fact("fact-row-1", "공칭전압", nominal="3.85", upper="4.55")])), is_reference=True)
    store.add(DocFacts(doc_name="규격서.docx", facts=FactSet(facts=[
        _fact("fact-word-1", "공칭전압", nominal="3.85")])))
    return store


def _config(tmp_path, **fact_kw) -> AppConfig:
    """양쪽 entity_name 이 같으므로 concept_builder 가 LLM 없이 same_as 를 만든다.

    `concept_builder.py` 의 exact 경로가 ``decided_by=BY_CODE`` 로 확정하고,
    ``ConceptMatcher`` 는 그것을 ``needs_review=False`` 로 본다 — 그래서 이
    테스트의 unsafe 사유는 커버리지 하나뿐이다. 온톨로지는 실행 환경의
    ``knowledge/ontology.yaml`` 이 끼어들지 않도록 없는 경로를 준다
    (`tests/test_fact_pipeline_concept.py` 와 같은 방식).
    """
    cfg = AppConfig()
    cfg.fact = FactConfig(
        artifacts_dir=str(tmp_path / "artifacts"),
        ontology_path=str(tmp_path / "없음.yaml"),
        **fact_kw,
    )
    return cfg


def _run(tmp_path, chat, **fact_kw):
    pipe = FactPipeline(_config(tmp_path, **fact_kw), chat=chat, embedder=_FakeEmbedder())
    return pipe._compare_from_store(_store(), "기준.xlsx", ["규격서.docx"])


# --------------------------------------------------------------------------- #
# shadow — 도입 전과 동일해야 한다
# --------------------------------------------------------------------------- #
def test_shadow_keeps_the_code_verdict_and_calls_no_extra_llm(tmp_path):
    chat = _ScriptedChat()
    result = _run(tmp_path, chat)
    (c,) = result.comparisons
    assert c.result == "match"          # 판정 무변경
    assert c.decided_by == "code"       # LLM 으로 강등되지 않았다
    assert chat.calls == 0


def test_shadow_still_records_the_gate_verdict(tmp_path):
    """분기는 안 바꾸되 사유는 남긴다 — 그래야 enforce 비용을 예측할 수 있다."""
    result = _run(tmp_path, _ScriptedChat())
    (c,) = result.comparisons
    assert c.initial_result == "match"
    assert c.review_triggers == [PARTIAL_ATTRIBUTE_COVERAGE]
    assert c.safe_to_finalize is False
    assert c.attribute_coverage == pytest.approx(0.5)


def test_stats_expose_unsafe_match_rate(tmp_path):
    result = _run(tmp_path, _ScriptedChat())
    assert result.compare_stats["unsafe_match_rate"] == pytest.approx(1.0)
    assert result.compare_stats["fast_path_rate"] == pytest.approx(0.0)
    assert result.compare_stats["review_reasons"] == {PARTIAL_ATTRIBUTE_COVERAGE: 1}
    # 기존 키가 사라지면 리포트가 깨진다
    for key in ("comparisons", "decided_by_llm", "llm_calls", "llm_failures", "concept"):
        assert key in result.compare_stats


def test_artifact_carries_history_fields(tmp_path):
    _run(tmp_path, _ScriptedChat())
    saved = json.loads(
        (tmp_path / "artifacts" / "기준_xlsx" / "comparison_result.json")
        .read_text(encoding="utf-8")
    )
    row = saved["comparisons"][0]
    assert row["initial_result"] == "match"
    assert row["review_triggers"] == [PARTIAL_ATTRIBUTE_COVERAGE]
    assert row["safe_to_finalize"] is False
    assert row["result"] == "match"          # 기존 키 보존


# --------------------------------------------------------------------------- #
# disabled / enforce
# --------------------------------------------------------------------------- #
def test_disabled_gate_omits_stats(tmp_path):
    """0 으로 채우면 '게이트가 아무것도 안 잡았다'로 오독된다 — 아예 넣지 않는다."""
    result = _run(tmp_path, _ScriptedChat(), fast_path=FastPathConfig(enabled=False))
    (c,) = result.comparisons
    assert c.review_triggers == []
    assert "unsafe_match_rate" not in result.compare_stats


def test_enforce_demotes_the_unsafe_match_to_llm(tmp_path):
    chat = _ScriptedChat([json.dumps(
        {"result": "mismatch", "reason": "상한값이 대상에 없습니다"}, ensure_ascii=False
    )])
    result = _run(tmp_path, chat, fast_path=FastPathConfig(enforce=True))
    (c,) = result.comparisons
    assert chat.calls == 1
    assert c.initial_result == "match" and c.result == "mismatch"
    assert c.result_changed is True
    assert result.compare_stats["result_changed_count"] == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_fact_gate_pipeline.py -v`
Expected: FAIL — `AttributeError` 또는 `KeyError: 'unsafe_match_rate'` (배선 전)

- [ ] **Step 3: Add imports**

`contentcompare/fact/pipeline.py` 의 `from .fact_comparator import FactComparator` 를 다음으로 바꾸고, 그 아래에 한 줄 더 추가한다.

```python
from .fact_comparator import MATCH, UNKNOWN, FactComparator
from .review_router import AcceptanceGate, gate_stats
```

- [ ] **Step 4: Rewire the comparison loop**

`contentcompare/fact/pipeline.py` L182-197 의 `for target in store.targets:` 블록을 다음으로 교체한다.

```python
        gate = AcceptanceGate(self.fact.fast_path)
        for target in store.targets:
            matcher = self._matcher_for(graph, ref_doc, target)
            # 후보가 없을 때의 사유는 매칭 전략이 안다(개념 경로 = '연결 없음').
            explain = getattr(matcher, "explain_missing", None)
            with stage(f"F5 값 대조 · {target.doc_name}"):
                for ref_fact in ref_doc.facts.facts:
                    candidates = matcher.search(ref_fact)
                    probe = comparator.compare_code(
                        ref_fact,
                        candidates,
                        target,
                        ref_low_confidence=ref_doc.is_low_confidence(ref_fact),
                        missing_reason=(
                            "" if candidates or explain is None else explain(ref_fact)
                        ),
                    )
                    reasons = gate.evaluate(probe)
                    # 게이트가 거부한 코드 match 만 강등한다. mismatch/unknown 은
                    # finalize 가 이미 LLM 으로 보내므로 여기서 또 밀 필요가 없다.
                    unsafe_match = bool(reasons) and probe.code_result == MATCH
                    comparison = comparator.finalize(
                        probe, force_llm=gate.enforce and unsafe_match
                    )
                    comparison.initial_result = probe.code_result or UNKNOWN
                    comparison.review_triggers = reasons
                    comparison.attribute_coverage = probe.attribute_coverage
                    result.comparisons.append(comparison)
```

- [ ] **Step 5: Merge the gate stats**

같은 파일 L199-205 의 `result.compare_stats = {...}` 를 다음으로 교체한다.

```python
        result.compare_stats = {
            "comparisons": len(result.comparisons),
            "decided_by_llm": sum(1 for c in result.comparisons if c.decided_by == "llm"),
            "llm_calls": comparator.llm_calls,
            "llm_failures": comparator.llm_failures,
            "concept": dict(graph.stats) if graph is not None else {},
            # 게이트가 꺼져 있으면 키를 아예 넣지 않는다 — 0 으로 채우면
            # "게이트가 아무것도 안 잡았다"로 오독된다.
            **(gate_stats(result.comparisons) if self.fact.fast_path.enabled else {}),
        }
```

스펙 §9 는 "통계 집계 실패가 비교를 죽이지 않게 한다"고 적었으나, `gate_stats()` 는 I/O 도
파싱도 없는 순수 계산이고 나눗셈 분모는 이미 가드되어 있다. `pipeline.py` L256-259 의
`try/except OSError` 는 **파일 저장**에 대한 방어이므로 여기에는 해당하지 않는다.
과잉 방어를 넣지 말 것 — 계산 예외를 삼키면 잘못된 지표가 조용히 남는다.

- [ ] **Step 6: Run the new tests**

Run: `pytest tests/test_fact_gate_pipeline.py -v`
Expected: 6 passed

- [ ] **Step 7: Run the fact pipeline regression suite**

Run: `pytest tests/test_fact_pipeline_smoke.py tests/test_fact_pipeline_concept.py tests/test_fact_engine.py tests/test_concept_regression.py tests/test_concept_regression_en.py -v`
Expected: 전부 통과, **무수정**으로. 하나라도 손대야 한다면 shadow 무변경 계약이 깨진 것이므로 배선을 되돌려 원인을 찾는다.

- [ ] **Step 8: Run the full suite**

Run: `pytest -q`
Expected: 전부 통과

- [ ] **Step 9: Commit**

```bash
git add contentcompare/fact/pipeline.py tests/test_fact_gate_pipeline.py
git commit -m "feat(fact): 비교 루프에 Acceptance Gate 배선 + 게이트 계측 저장"
```

---

### Task 7: CLAUDE.md 갱신

**Files:**
- Modify: `CLAUDE.md` ("fact 엔진" 섹션의 F5 설명 뒤)

**Interfaces:**
- Consumes: Task 1-6 전부
- Produces: 없음(문서)

- [ ] **Step 1: Add the section**

`CLAUDE.md` 의 fact 엔진 설명에서 "2. **F5 값 대조**(...)" 문단 **뒤**에 다음을 넣는다.

```markdown
**F5 Fast Path 게이트(`fact/review_router.py`)** — 코드가 확정한 `match` 를 믿어도 되는지
채점한다. `FactComparator.compare_code()`(LLM 0회)가 만든 `ComparisonProbe` 를
`AcceptanceGate` 가 보고 `review_triggers` 를 붙이며, `finalize()` 가 최종 판정을 만든다.
`compare()` 는 이 둘을 잇는 호환 래퍼라 롤백 경로는 무변경이다.

**게이트는 탐지가 아니라 라우팅이다** — "후보가 2건 이상이다" 같은 셀 수 있는 사실만
확인하고 "어느 후보가 맞는가"는 판단하지 않는다. 그것은 2차 Evidence 검사의 몫이다
(`docs/FACT_LINKED_GRAPH_RAG_DESIGN.md`).

⚠️ **이 단계는 비용을 줄이지 않고 늘린다.** 오늘도 코드 `match` 는 LLM 을 안 부르므로,
게이트의 실제 효과는 지금까지 조용히 확정되던 unsafe match 를 LLM 으로 보내는 것이다.
그래서 기본값이 shadow(`fact.fast_path.enforce: false`)이고, 리포트의
`unsafe_match_rate` 가 "켜면 얼마나 늘어나는가"를 미리 알려준다. 절감은 상위 설계의
Phase 2(개념 판정 LLM)와 Phase 6(Entity 별 그룹 배치)에서 나온다.

`attribute_coverage` 가 필요한 이유는 `_decide_by_code()` 가 **양쪽의 공통 속성만** 보기
때문이다 — 기준에 세 속성이 있고 대상에 하나뿐이어도 그 하나가 같으면 `match` 가 된다.
```

- [ ] **Step 2: Verify the file renders and links resolve**

Run: `git diff --stat CLAUDE.md`
Expected: `CLAUDE.md` 1개 파일만 변경. 참조한 `docs/FACT_LINKED_GRAPH_RAG_DESIGN.md` 가 실제로 존재하는지 `ls docs/FACT_LINKED_GRAPH_RAG_DESIGN.md` 로 확인한다.

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: F5 Fast Path 게이트 설명 추가"
```

---

## 실측 절차 (구현 후)

구현이 끝나면 **실제 문서로 한 번 돌려** 지표를 본다. 이것이 이 작업의 산출물이다.

```bash
contentcompare --config config/config.yaml --engine fact \
  --reference 기준.xlsx --targets 규격서.docx --out report.md
python -c "import json;d=json.load(open('artifacts/기준_xlsx/comparison_result.json',encoding='utf-8'));print(json.dumps(d['stats'],ensure_ascii=False,indent=2))"
```

읽는 법:

| 관측 | 해석 | 다음 행동 |
|---|---|---|
| `enforce_new_llm_count` 가 작다 | enforce 를 켜도 비용이 거의 안 는다 | 늘어나는 그 건들이 **실제 오판인지** 먼저 확인 |
| `review_reasons` 가 `partial_attribute_coverage` 일색 | coverage 정의가 실제 문서에 너무 엄격할 수 있다 | lenient 정의(값 있는 속성만) 재검토 |
| `review_reasons` 가 `duplicate_entity_facts` 일색 | 같은 개념에 대상 fact 가 여럿 | F5 1:N 판정이 이미 처리한다(2026-08-13) |
| `result_changed_count` 가 크다 | 코드 판정을 LLM 이 자주 뒤엎고 있다 | 어느 방향으로 뒤엎는지 표본 감사 |

⚠️ **`unsafe_match_rate` 를 전환 비용으로 읽지 말 것.** 게이트 사유 셋
(`low_confidence`·`code_unknown`·`duplicate_entity_facts`)은 `finalize` 가 이미 LLM 으로
보내는 조건과 **같은 사실**을 가리켜서, enforce 를 켜도 늘어나지 않는다. 그 차이를 뺀
값이 `enforce_new_llm_count` 다.

---

## 실측 결과와 `enforce` 결정 (2026-08-13)

캐시된 `artifacts/자표준문서_xlsx`(자표준문서.xlsx ↔ spec_en.docx, 20건)를 오프라인
재현했다. `compare_code` 와 `gate.evaluate` 는 LLM 을 안 부르고 개념 그래프는 캐시에
있으므로 비용 0 이다.

```text
전체 비교              20
코드 match              8
unsafe match            7   (unsafe_match_rate 0.88)
  ├ 이미 LLM 으로 가던 것  5   (low_confidence 5 · duplicate_entity_facts 3, 중복)
  └ enforce 순증가        2   (enforce_new_llm_rate 0.10)
```

`unsafe_match_rate` 0.88 이 실제 순증가 10% 를 **3.5배 부풀렸다.** 이 계측 결함을
`enforce_new_llm_count`/`_rate` 추가로 고쳤다.

### 결정: `enforce` 를 켜지 않는다

순증가 2건이 전부 `partial_attribute_coverage` 단독이었고, 열어 보니 **둘 다 올바른
match** 였다.

| 기준 항목 | 기준 속성 | 대상 속성 | coverage |
|---|---|---|---|
| 표준환경온도 | `lower=21` `target_value=25` `upper=29` | `lower=21` **`center_value=25`** `upper=29` | 0.67 |
| 평가환경습도 | `lower=33` `target_value=43` `upper=53` | `lower=33` **`center_value=43`** `upper=53` | 0.67 |

값은 같고 **속성 이름만** 다르다(`target_value` ↔ `center_value`).
`attribute_coverage` 가 키 겹침만 세기 때문에 생기는 오탐이다 —
`_compare_single_attributes` 가 이미 "키 이름은 원본 표의 **열 위치**에서 온 것이라
의미 구분이 아닌 경우가 많다"고 인정한 문제인데, 그 예외는 양쪽 속성이 1개일 때만
적용된다. 여기는 양쪽 3개라 예외가 안 걸린다.

즉 이 데이터에서 enforce 의 순효과는 **정상 판정 2건을 LLM 에 보내 `unknown` 으로
뒤집힐 위험을 만드는 것뿐**이다. 잡아낸 오판은 0 건이다.

### 다시 검토할 조건

- 다른 문서쌍에서 `enforce_new_llm_count` 가 커지고, 그 건들 표본을 열었을 때 **실제
  오판이 섞여 있을 때.** 건수만으로 켜지 말 것 — 이번 실측이 그 함정이었다.
- `partial_attribute_coverage` 를 속성 **이름**이 아니라 **값** 기준으로 재정의했을 때
  (기준 속성값이 대상 어딘가에 같은 값으로 존재하는가). 그러면 위 두 건은 coverage
  1.0 이 되어 사유 자체가 사라진다. 이것이 게이트를 유용하게 만드는 선행 작업이다.
