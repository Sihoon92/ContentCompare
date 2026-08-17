# Fact Acceptance Gate (Phase 1) — 코드 probe 와 Review Router 분리

> 작성일: 2026-08-10
> 상태: **설계 확정** — 구현 계획 대기
> 상위 설계: [`FACT_LINKED_GRAPH_RAG_DESIGN.md`](../../FACT_LINKED_GRAPH_RAG_DESIGN.md) §14 Phase 1
> 관련: [`FACT_F7_DESIGN.md`](../../FACT_F7_DESIGN.md) · [`FACT_PIPELINE_PLAN.md`](../../FACT_PIPELINE_PLAN.md)

---

## 0. 한 줄 결론

`FactComparator.compare()` 를 **LLM 을 안 부르는 `compare_code()`** 와 **`finalize()`** 로 쪼개고,
그 사이에 Acceptance Gate 를 넣어 "코드가 확정한 `match` 를 믿어도 되는가"를 채점한다.
**기본값은 shadow** — 채점만 남기고 분기는 오늘 그대로여서 판정과 LLM 호출 수가 변하지 않는다.
이번 작업의 산출물은 동작 변경이 아니라 **`unsafe_match_rate` 실측치**다.

---

## 1. 배경과 문제

### 1.1 Phase 1 은 비용을 줄이지 않는다

상위 설계는 "안전한 `match` 는 LLM 0회로 확정"을 완료 조건으로 두지만,
**오늘도 코드가 `match` 로 단정하면 LLM 을 안 부른다**(`fact_comparator.py` L165-167).
따라서 Phase 1 이 실제로 바꾸는 것은 하나뿐이다 — 지금까지 코드가 조용히 확정하던
**unsafe match 를 LLM 으로 보내는 것**. 정확도는 오르고 LLM 호출은 `unsafe_match_rate` 만큼
늘어난다. 비용 절감은 Phase 2(concept LLM)와 Phase 6(Entity 별 그룹 배치)에서 나온다.

이 사실을 문서에 남기는 이유는, Phase 1 을 "비용 절감 작업"으로 착각하면
실측에서 호출이 늘었을 때 회귀로 오인하기 때문이다.

### 1.2 조용한 오판의 실체

`_decide_by_code()` L178-196 은 **양쪽의 공통 attribute 만** 본다.

```python
shared = [k for k in ref.attributes if k in target.attributes]
```

기준에 3개, 대상에 1개뿐이어도 그 1개가 같으면 `match` 가 된다. 이것이 게이트가
`partial_attribute_coverage` 로 잡으려는 대상이다.

또 `compare()` L150 은 `best = candidates[0]` 로 **후보 1건만** 코드 비교에 쓴다.
`ConceptMatcher.search()` 는 같은 개념에 연결된 대상 fact 를 **전부** 돌려주므로,
후보가 여러 건인 상황은 "코드가 고를 문제가 아니다"라는 신호다.

### 1.3 상위 설계와의 차이 — 확인된 사실

상위 설계 §10.2 는 `fact_matcher.py` 를 "동일 Entity 의 다중 후보를 보존하고
첫 항목 조기 종료 제거" 대상으로 적었으나, 코드 확인 결과 **불필요하다**:

- `ConceptMatcher.search()`(L220-249)는 이미 다중 후보를 점수 내림차순으로 전부 반환한다.
- 조기 종료는 `FactMatcher` 의 이름 완전일치 경로(L122-129)에만 있고, 그것은 롤백 전용
  (`use_concept_graph: false`)이다.

따라서 **Phase 1 에서 `fact_matcher.py` 는 수정하지 않는다.**

---

## 2. 목표와 비목표

### 2.1 목표

1. `FactComparator` 를 "LLM 없는 코드 판정"과 "확정"으로 분리한다.
2. Acceptance Gate 로 코드 `match` 의 신뢰도를 채점하고 `review_triggers` 를 남긴다.
3. `unsafe_match_rate` 를 포함한 지표를 산출해 **enforce 전환 비용을 사전에 예측**한다.
4. 기본값(shadow)에서 판정 결과와 LLM 호출 수가 **변하지 않음**을 보장한다.
5. F7 개념 그래프와 `ConceptMatcher` 를 **건드리지 않아** 변경 원인을 격리한다.

### 2.2 비목표

- 2차 Graph RAG 검사기를 만들지 않는다(Phase 5-6).
- Deterministic Concept Resolver 를 만들지 않는다(Phase 2).
- Word 원문 무손실 EvidenceUnit 을 만들지 않는다(Phase 3).
- `conditional_series` 휴리스틱을 넣지 않는다(§3.3 근거).
- Review Queue 를 `(target_doc, entity_id)` 로 그룹화하지 않는다 — 소비자가 없다(Phase 6).
- `fact_matcher.py` 를 수정하지 않는다(§1.3).

---

## 3. 설계 결정과 근거

### 3.1 Gate 는 탐지가 아니라 라우팅이다

코드가 하는 일은 "후보가 2건 이상이다", "커버리지가 1.0 미만이다" 같은 **셀 수 있는 사실**의
확인이다. "이 4건 중 어느 것이 맞는가"는 코드가 판단하지 않는다 — 그것이 2차 Graph RAG 가
원문을 보고 할 일이다. 이 경계를 흐리면 게이트가 또 하나의 축약된 판정기가 된다.

이 원칙의 직접적 귀결로, 후보 전체를 `_decide_by_code` 로 채점하는 설계(초안의
`candidate_verdicts`)를 **폐기**했다. 게이트 판정에는 `len(candidates) > 1` 하나로 충분하고,
`conflicting_candidates` 와 `duplicate_entity_facts` 는 결국 같은 처리(2차로 보냄)를 받으므로
구분 실익이 없다.

### 3.2 shadow 를 기본값으로 두는 이유

enforce 를 바로 켜면 LLM 호출이 얼마나 늘지 모르는 채로 도입하게 된다.
shadow 는 같은 `ComparisonProbe` 를 채점하므로 지표가 enforce 와 **일관**되며,
`unsafe_match_rate` 가 그대로 "enforce 시 늘어날 호출 비율"이다.

게이트를 사후 평가(비교 결과를 받은 뒤 채점)로 구현하지 않는 이유도 여기 있다 —
`_decide_by_llm()` L227-231 이 후보를 교체할 수 있어, 사후 채점은 **코드 판정 시점과 다른
후보**를 채점하게 된다.

### 3.3 `conditional_series` 를 Phase 1 에서 제외하는 이유

상위 설계 §2 의 대표 사례(충전 온도 4구간)가 게이트를 빠져나가는 경로를 따져보면:

| 대상 fact 상태 | 코드 판정 | 2차로 가는가 |
|---|---|---|
| 엉뚱한 구간이 후보 → 값 다름 | `mismatch` | ✅ 자동 |
| 개념 연결 없음 | `missing` | ✅ 자동 |
| 단위 등가 미상 | `None` → LLM | ✅ 자동 |
| fact 가 4건으로 쪼개짐(후보 4건) | `match` | ✅ `duplicate_entity_facts` |
| **fact 1건에 4구간이 뭉침 + 값 일치** | `match` | ❌ 이것만 남음 |

마지막 한 줄만 `conditional_series` 가 필요하다. 그런데 그 경우는 F3 추출 단계에서 이미
정보를 잃은 상태이고, **Phase 3(원문 무손실 EvidenceUnit) 없이는 2차로 보내도 그래프가
원문을 볼 수 없다.** 즉 지금 넣으면 오탐 위험만 지고 효과가 0이다.

→ Phase 3 과 함께 도입한다.

### 3.4 `unresolved_concept` 사유를 만들지 않는 이유

후보 없음은 하나의 상황이고, 개념 경로인지 유사도 경로인지는 `missing_reason` 문자열이
이미 설명한다(`ConceptMatcher.explain_missing()` L252-275 가 차단한 `differs_by` 엣지까지
싣는다). 사유를 둘로 쪼개면 소비자(`missing_trace.py`, 리포트, UI)만 늘어난다.

---

## 4. 컴포넌트

```
contentcompare/fact/review_router.py   [신규]  AcceptanceGate · 사유 상수
contentcompare/fact/fact_comparator.py [수정]  compare() → compare_code() + finalize()
contentcompare/fact/pipeline.py        [수정]  probe → gate → finalize, 지표 집계
contentcompare/config.py               [수정]  FastPathConfig 추가
```

### 4.1 `FactComparator` 분리

기존 `compare()` 의 시그니처와 동작을 **100% 보존**한 채로 안을 쪼갠다.

```python
def compare_code(
    self, ref: Fact, candidates: list[MatchCandidate], target: DocFacts, *,
    ref_low_confidence: bool = False, missing_reason: str = "",
) -> ComparisonProbe:
    """LLM 을 절대 호출하지 않는다."""

def finalize(self, probe: ComparisonProbe, *, force_llm: bool = False) -> FactComparison:
    """probe → FactComparison. 코드 미확정·불안정·force_llm 이면 _decide_by_llm."""

def compare(self, ref, candidates, target, **kw) -> FactComparison:
    return self.finalize(self.compare_code(ref, candidates, target, **kw))
```

기존 테스트와 롤백 경로(`use_concept_graph: false`)는 `compare()` 를 그대로 쓰므로 무변경이다.

`_decide_by_code()` / `_decide_by_llm()` / `_fallback()` 의 내부 로직은 **손대지 않는다** —
호출 순서만 두 메서드로 나눈다.

### 4.2 `ComparisonProbe`

`fact_comparator.py` 에 둔다(comparator 의 산출물이므로). `review_router` 가 이를 import 하며,
반대 방향 import 가 없어 순환이 생기지 않는다.

```python
@dataclass
class ComparisonProbe:
    reference_fact: Fact
    target_doc: str
    candidates: list[MatchCandidate]
    code_result: Optional[str]        # match | mismatch | missing | None(코드 미확정)
    mismatch_attributes: list[str]
    code_reason: str
    attribute_coverage: float
    uncertain: bool                   # needs_review ∨ ref/target low_confidence
    missing_reason: str
```

`code_result` 가 `None` 인 것과 `"unknown"` 인 것은 다르다 — `None` 은 **코드가 판단을
포기했다**(단위 등가 미상 등)는 뜻이고, `unknown` 은 최종 판정 라벨이다. `finalize()` 가
LLM 을 못 쓸 때만 `None` → `unknown` 으로 내려간다.

### 4.3 `attribute_coverage`

```python
def attribute_coverage(ref: Fact, target: Optional[Fact], *, confirmed_link: bool) -> float:
    if target is None:
        return 0.0
    if not ref.attributes:
        return 1.0                       # 비교할 속성이 없으면 커버리지 논의가 무의미
    if confirmed_link and len(ref.attributes) == 1 and len(target.attributes) == 1:
        return 1.0                       # _compare_single_attributes 경로 (§4.3.1)
    covered = sum(1 for k in ref.attributes if k in target.attributes)
    return covered / len(ref.attributes)
```

분모는 **기준 fact 의 전체 attribute** 다. 값이 빈 attribute 를 제외하는 lenient 정의는
placeholder 판별 규칙이 하나 더 필요하므로 채택하지 않는다.

호출부는 `compare_code()` 이며 인자는 `target = candidates[0].fact`,
`confirmed_link = not candidates[0].needs_review` 다. 후보가 없으면 호출하지 않고
`attribute_coverage = 0.0` 으로 둔다(§5 의 "후보 필요" 규칙에 따라 이 값은 사유 판정에
쓰이지 않고 통계에서도 제외된다).

#### 4.3.1 단일 속성 예외

양쪽 속성이 각각 1개면 키 이름이 달라도 coverage 를 `1.0` 으로 본다.
`_compare_single_attributes()` L272-300 의 실측 근거가 그대로 적용된다 — 속성이 하나뿐인
fact 는 "이 항목의 값은 X"라는 뜻이고 키 이름은 원본 표의 **열 위치**에서 온 것이다
(기준이 공칭용량을 '하한치' 열에 적어 `lower_limit`, 대상은 `target_value` 였던 사례).

단 **개념 연결이 확정된 경우에만** 적용한다(`MatchCandidate.needs_review == False`).
LLM 이 만든 연결 위에서는 어차피 `low_confidence` 사유가 붙는다.

### 4.4 `AcceptanceGate`

```python
class AcceptanceGate:
    def __init__(self, cfg: FastPathConfig) -> None: ...
    @property
    def enforce(self) -> bool: ...
    def evaluate(self, probe: ComparisonProbe) -> list[str]:
        """review_reasons 를 반환한다. 빈 리스트면 안전하게 확정 가능."""
```

순수 함수에 가깝다 — 부작용도 I/O 도 없고 LLM 을 모른다. `cfg.enabled == False` 면 항상
빈 리스트를 반환한다.

---

## 5. 게이트 규칙

| `review_reason` | 조건 | 후보 필요 | 출처 |
|---|---|---|---|
| `code_missing` | `not probe.candidates` | — | 기존 신호 |
| `code_mismatch` | `code_result == "mismatch"` | ✅ | 기존 |
| `code_unknown` | `code_result is None` | ✅ | 기존 |
| `low_confidence` | `probe.uncertain` | ✅ | 기존 |
| `partial_attribute_coverage` | `attribute_coverage < 1.0` | ✅ | **신규** |
| `duplicate_entity_facts` | `len(probe.candidates) > 1` | ✅ | **신규** |
| `invalid_evidence` | 최상위 후보의 `evidence_text` 또는 `source` 가 빔 | ✅ | **신규** |

**"후보 필요" 열이 ✅ 인 규칙은 후보가 하나도 없으면 평가하지 않는다.** 후보가 없으면
`code_missing` 하나만 붙는다 — 그러지 않으면 모든 `missing` 에 `partial_attribute_coverage`
(coverage 0.0)와 `invalid_evidence`(대상 fact 없음)가 함께 붙어 사유 통계가 무의미해진다.

사유는 **누적**된다(하나의 비교에 여러 사유가 붙을 수 있다). 순서는 위 표 순서로 고정해
리포트·통계에서 결정적으로 재현되게 한다.

`invalid_evidence` 는 `Fact.evidence_text` 가 빈 문자열이거나 `Fact.source` 가 빈 dict 인
경우다. 사람이 원문 대조로 검수할 수 없는 판정은 안전하다고 볼 수 없다는 §6.2 원칙의 적용이다.

---

## 6. 흐름

`pipeline._compare_from_store()` L182-197 을 다음으로 바꾼다.

```python
gate = AcceptanceGate(self.fact.fast_path)
for target in store.targets:
    matcher = self._matcher_for(graph, ref_doc, target)
    explain = getattr(matcher, "explain_missing", None)
    with stage(f"F5 값 대조 · {target.doc_name}"):
        for ref_fact in ref_doc.facts.facts:
            candidates = matcher.search(ref_fact)
            probe = comparator.compare_code(
                ref_fact, candidates, target,
                ref_low_confidence=ref_doc.is_low_confidence(ref_fact),
                missing_reason=("" if candidates or explain is None
                                else explain(ref_fact)),
            )
            reasons = gate.evaluate(probe)
            unsafe_match = bool(reasons) and probe.code_result == MATCH
            c = comparator.finalize(
                probe, force_llm=gate.enforce and unsafe_match
            )
            c.initial_result = probe.code_result or UNKNOWN
            c.review_triggers = reasons
            c.attribute_coverage = probe.attribute_coverage
            result.comparisons.append(c)
```

**`enforce=False`(기본)에서는 `force_llm` 이 항상 `False`이므로 `finalize()` 가 오늘과 동일하게
동작한다.** 새로 생기는 것은 필드 3개와 통계뿐이다.

---

## 7. 산출물

### 7.1 `FactComparison` 확장

```python
initial_result: str = ""                              # 코드 판정
review_triggers: list[str] = field(default_factory=list)
attribute_coverage: float = 1.0

@property
def result_changed(self) -> bool:
    return bool(self.initial_result) and self.initial_result != self.result

@property
def safe_to_finalize(self) -> bool:
    return not self.review_triggers
```

`to_dict()` 에 `initial_result` · `review_triggers` · `attribute_coverage` ·
`result_changed` · `safe_to_finalize` 를 **추가**한다. 기존 키는 그대로 두어
`missing_trace.py` · `artifact_reader.py` · `ui/micro_world.py` · `why_missing.py` 가
무수정으로 동작한다.

`result` 는 그대로 최종 판정이다(상위 설계의 `final_result` 에 해당). Phase 6 에서
`secondary_result` 가 생기면 그때 별도 필드로 추가한다.

### 7.2 `compare_stats` 확장

| 키 | 타입 | 산식 |
|---|---|---|
| `fast_path_rate` | float | `safe_to_finalize` 건수 / 전체 비교 건수 |
| `secondary_review_rate` | float | `1 - fast_path_rate` |
| `unsafe_match_rate` | float | 게이트가 거부한 code match / 전체 code match |
| `review_reasons` | dict | `{사유: 건수}` — 누적이므로 합이 비교 건수를 넘을 수 있다 |
| `mean_attribute_coverage` | float | 후보가 있는 비교만의 평균 |
| `result_changed_count` | int | `initial_result != result` 인 건수 |

`unsafe_match_rate` 가 이번 작업의 핵심 산출물이다 — enforce 를 켜기 전에 늘어날 LLM 호출
비율을 알려준다. 분모가 0이면(코드 match 가 하나도 없으면) `0.0` 으로 둔다.
`mean_attribute_coverage` 도 후보가 있는 비교가 하나도 없으면 `0.0` 이다.

통계 키는 `result_changed_count` 이고 `FactComparison.result_changed` 는 bool property 다 —
이름을 구분해 "비율인가 건수인가"를 읽는 쪽에서 헷갈리지 않게 한다.

`fast_path.enabled == False` 면 이 키들을 **넣지 않는다** — 있으면 항상 0으로 보여
"게이트가 아무것도 안 잡았다"로 오독된다.

---

## 8. 설정

```python
@dataclass
class FastPathConfig:
    enabled: bool = True
    """게이트 채점과 계측. False 면 오늘과 완전히 동일하게 동작한다."""

    enforce: bool = False
    """True 면 게이트가 거부한 code match 를 LLM 판정으로 강등한다.
    기본 False(shadow) — unsafe_match_rate 를 실측한 뒤 켠다."""
```

`FactConfig` 에 `fast_path: FastPathConfig = field(default_factory=FastPathConfig)` 로 추가하고
`config/config.example.yaml` 에 주석과 함께 노출한다.

규칙별 on/off(`require_full_coverage` 등)는 상위 설계 §11 의 "코드 기본값으로 시작" 원칙대로
노출하지 않는다. 실측에서 조정이 필요해지면 그때 승격한다.

---

## 9. 에러 처리

- `AcceptanceGate.evaluate()` 는 순수 함수이며 예외를 던지지 않는다. `probe.candidates` 가
  비어도, `target_fact` 가 `None` 이어도 사유 문자열만 달라진다.
- 통계 집계 실패가 비교를 죽이지 않게 한다 — `pipeline.py` L256-259 의 기존 방어와 같은 원칙.
- `fast_path.enabled == False` 는 게이트 코드를 **전혀 실행하지 않는** 경로여야 한다
  (빈 리스트를 만들어 도는 것이 아니라 조기 반환).

---

## 10. 테스트

기존 원칙대로 `FakeLLM`/`FakeEmbedder` 를 주입해 Office·네트워크 없이 실행한다.

### 10.1 신규 `tests/test_review_router.py`

`Fact` 를 직접 만들면 되므로 LLM 이 전혀 필요 없다.

- coverage 1.0 · 후보 1건 · evidence 유효 · 확정 연결 → 사유 없음(통과)
- **후보 0건 → `code_missing` 하나만** (coverage/evidence 사유가 함께 붙지 않는다)
- 기준 3속성 · 대상 1속성 → `partial_attribute_coverage`
- 후보 2건 이상 → `duplicate_entity_facts`
- `evidence_text` 빈값 → `invalid_evidence`
- `source` 빈 dict → `invalid_evidence`
- 양쪽 단일 속성 + 확정 연결 → coverage 1.0 (키 이름이 달라도)
- 양쪽 단일 속성 + `needs_review=True` → `low_confidence` (예외 미적용)
- 사유 다중 부착 시 순서가 고정인지
- `enabled=False` → 항상 빈 리스트

### 10.2 `FactComparator`

- `compare_code()` 가 LLM 을 0회 호출한다(runner 호출 카운터 검증)
- `compare()` 결과가 분리 전과 동일하다(기존 케이스 회귀)
- `finalize(force_llm=True)` 가 코드 `match` 를 LLM 으로 넘긴다
- `finalize(force_llm=True)` 인데 `use_llm=False` 면 `_fallback` 으로 코드 판정을 유지한다

### 10.3 파이프라인 회귀

- shadow 기본값에서 `tests/test_fact_pipeline_smoke.py` 가 **무수정 통과**한다
- shadow 에서 LLM 호출 수가 게이트 도입 전과 같다
- `comparison_result.json` 에 새 필드가 실리고 기존 키가 보존된다
- enforce 모드에서 unsafe match 가 LLM 으로 가고 `result_changed` 가 집계된다

---

## 11. 완료 조건

- [ ] `compare_code()` 가 LLM 을 호출하지 않는다(테스트로 보장).
- [ ] `compare()` 의 기존 동작과 시그니처가 보존된다.
- [ ] 게이트 사유 7종이 `review_triggers` 로 남는다.
- [ ] `attribute_coverage` 가 기준 fact 전체 속성 기준으로 계산되고 단일 속성 예외가 적용된다.
- [ ] shadow 기본값에서 판정 결과와 LLM 호출 수가 변하지 않는다.
- [ ] `fast_path_rate` · `unsafe_match_rate` · `review_reasons` 가 `comparison_result.json` 과
      `compare_stats` 에 남는다.
- [ ] `enforce: true` 로 unsafe match 를 LLM 으로 보낼 수 있다.
- [ ] `fact_matcher.py` · `concept_builder.py` · F7 관련 코드가 수정되지 않는다.
- [ ] 기존 artifact 소비자 4종이 무수정으로 동작한다.

---

## 12. Phase 1 이후

이 스펙은 상위 설계 Phase 1 만 다룬다. 다음 단계의 진입점은 이렇게 남는다.

| 이후 Phase | 이 스펙이 남기는 진입점 |
|---|---|
| Phase 2 (Deterministic Concept Resolver) | `_matcher_for()` 교체 지점 |
| Phase 3 (원문 무손실 EvidenceUnit) | `conditional_series` 사유 추가 자리 |
| Phase 5-6 (Graph RAG 2차 검사) | `review_triggers` 가 붙은 비교 = Review Queue 후보 |
| Phase 6 (결과 병합) | `initial_result` 옆에 `secondary_result` 추가 |

실측 결과가 다음 결정을 좌우한다.

- `unsafe_match_rate` 가 낮으면 → enforce 를 바로 켜도 안전하다.
- `partial_attribute_coverage` 가 대부분이면 → coverage 정의(strict/lenient)를 재검토한다.
- `duplicate_entity_facts` 가 대부분이면 → Phase 5-6 의 가치가 크다는 신호다.
