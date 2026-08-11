# F5 다중 후보 통합 판정 (1:N Fact Comparison)

- 작성일: 2026-08-11
- 상태: 설계 승인됨 (구현 전)
- 관련 문서: `docs/FACT_LINKED_GRAPH_RAG_DESIGN.md` §8.1, `docs/FACT_F7_DESIGN.md`,
  `docs/superpowers/specs/2026-08-10-fact-acceptance-gate-phase1-design.md`

## 1. 문제

기준(엑셀) 한 행이 대상(워드)의 **여러 fact** 와 함께 대응할 때 판정이 깨진다.

실제 사례:

| 쪽 | 형태 |
|---|---|
| 엑셀 기준 | `충전환경온도` — 한 행(=한 블록)에 4개 조건 구간이 들어 있다 |
| 워드 대상 | 같은 내용이 여러 줄로 나뉘어 있고, F3 가 `charge temperature range` 라는 **같은 이름의 fact 4건**으로 추출한다 |

`candidate_pairs.json` 에는 4건이 모두 후보로 들어온다 — **recall 은 성공한다.** 그런데
하류에서 3건이 버려진다.

### 1.1 원인 — 1:1 이 세 층에 박혀 있다

| 층 | 위치 | 현재 동작 |
|---|---|---|
| 후보 선택 | `fact_comparator.py` `_decide_by_code(ref, best.fact)` | `candidates[0]` **하나만** 본다 |
| LLM 판정 | `prompts.py` `COMPARE_SYSTEM` 원칙 4 | `"target_fact_id 는 후보 중 하나만 쓰세요"` — 프롬프트가 1:N 을 금지 |
| 결과 스키마 | `FactComparison.target_fact: Optional[Fact]` | 단수 필드 — 1:N 결과를 담을 그릇이 없다 |

후보가 여러 건일 때 `candidates[0]` 은 recall 점수 순의 **사실상 임의 선택**이다. 같은
개념 노드에 속한 동명 fact 들은 점수로 갈리지 않는다(`FACT_F3_5_LIVE_REPORT.md` §6 의
"두 분포가 겹친다"와 같은 이유). 그 위에서 내린 `match`/`mismatch` 는 원리적으로 신뢰할
수 없는데, 지금은 그것이 **조용히 확정**된다.

`AcceptanceGate` 는 이 상황을 `duplicate_entity_facts`(후보 2건 이상)로 **탐지는** 하고
있으나, 기본이 shadow(`fast_path.enforce: false`)라 판정을 바꾸지 않는다.

즉 recall 문제도 개념 그래프 문제도 아니다. **F5 값 대조 계약이 1:1 로 고정된 것**이
원인이다.

## 2. 목표와 비목표

**목표**

- 기준 1행 = 리포트 1줄을 유지한 채, 후보 N건을 LLM 에 함께 넣어 **하나의 종합 판정**을 낸다.
- 종합 판정과 함께 **후보별 내역**을 구조화해 남겨, 사람이 구간 단위로 검수할 수 있게 한다.
- 이 변경이 없앤 오판의 양을 계측으로 드러낸다.

**비목표 (YAGNI)**

- 기준(엑셀) 쪽 fact 를 구간별로 분해하지 않는다.
- 개념 그래프(F7)를 바꾸지 않는다. 후보 생성은 이미 옳게 동작한다.
- `fast_path.enforce` 기본값을 바꾸지 않는다.
- 새 config 키를 추가하지 않는다.
- 후보들을 "조건 계열"로 묶는 코드 휴리스틱을 넣지 않는다 — 게이트가 판단하지 않는다는
  원칙(`review_router.py` 모듈 docstring)과 충돌하고, "후보 2건 이상" 규칙만으로 이
  케이스가 잡힌다.

## 3. 라우팅과 데이터 흐름

```text
ConceptMatcher.search(ref) → candidates[N]
        ↓
compare_code()            ← 무변경. candidates[0] 기준 코드 판정을 그대로 낸다
        ↓
AcceptanceGate.evaluate() ← 무변경. duplicate_entity_facts 가 붙는다
        ↓
force_llm = (gate.enforce and unsafe_match) or len(candidates) >= 2   ← 신규
        ↓
finalize() → _decide_by_llm(후보 전부)
        ↓
FactComparison(result, target_facts[N], findings[N], ...)
```

### 3.1 코드 판정을 지우지 않는다

N≥2 일 때 `candidates[0]` 기준 코드 판정은 의미가 없으므로 `code_result = None`(판단
포기)으로 두는 선택지가 있다. **채택하지 않는다.** 그러면 게이트가 `code_unknown` 을
붙여 "단위 등가를 몰라 포기"와 사유가 섞이고, 사유 통계가 흐려진다.

그대로 남기면 `_code_overridden()` 이 **"1:1 축약이 만들던 오판 건수"** 를 그대로
드러낸다 — 이 변경의 효과 측정치가 공짜로 생긴다. `review_router.py` 의
`_code_overridden` docstring 이 "코드가 의견을 냈는데 최종이 다른가"와 "코드에 의견이
없었다"를 이미 갈라 두었으므로, 이 케이스가 그 구분에 정확히 맞아 들어간다.

### 3.2 게이트가 아니라 `finalize` 호출부에 붙인다

두 가지 이유가 있다.

1. 게이트는 사유만 붙이고 강제는 `pipeline` 이 하는 현 구조를 유지한다.
2. `fast_path.enabled: false`(게이트를 끔)여도 1:N 라우팅은 살아야 한다. 게이트는
   라우팅 실험이고 이것은 정확성 버그 수정이므로 서로 독립이어야 한다.

따라서 조건은 게이트 상태가 아니라 `probe.candidates` 를 직접 본다.

### 3.3 롤백 경로도 함께 고쳐진다

`use_concept_graph: false` 의 `FactMatcher` 도 `match_top_k` 만큼 후보를 주므로 같은
규칙이 그대로 걸린다. 별도 분기가 필요 없다.

## 4. 스키마

`FactComparison` 에 필드 2개를 **추가**한다. 기존 필드는 그대로 둔다.

```python
target_fact:  Optional[Fact]           # 유지. 대표 1건 (아래 규칙으로 코드가 정한다)
target_facts: list[Fact] = []          # 신규. 종합에 쓰인 후보 전부
findings:     list[FactFinding] = []   # 신규. 후보별 내역
```

`target_fact`(대표 1건)는 **코드가 결정론적으로 고른다** — 프롬프트에서 `target_fact_id`
를 없앴으므로 LLM 이 고르지 않는다. 규칙은 "첫 `mismatch` finding, 없으면 첫 finding,
findings 가 비면 `candidates[0]`" 이다. 리포트에서 대표로 보여줄 것은 **문제가 있는
쪽**이어야 사람이 먼저 볼 곳을 찾는다.

후보가 1건이면 `target_facts`/`findings` 가 1건이라, 기존 소비자(`report/fact_report.py`,
`fact/missing_trace.py`, 현미경 UI)는 **무변경**이다.

```python
@dataclass
class FactFinding:
    fact_id: str
    result: str                        # match|mismatch|unknown
    mismatch_attributes: list[str]
    quote: str                         # LLM 이 인용한 대상 원문
    quote_verified: bool               # 코드가 evidence_text 와 대조한 결과
    reason: str
```

종합 레벨의 `mismatch_attributes` 는 findings 의 합집합이다.

## 5. 프롬프트

`COMPARE_SYSTEM` 의 원칙 4번(`"target_fact_id 는 후보로 제시된 id 중 하나만 쓰세요"`)을
교체한다.

> 후보가 여러 건이면 **그것들이 함께 하나의 규격을 이룰 수 있습니다**(예: 조건 구간별로
> 나뉜 값). 후보를 각각 기준과 대조해 `findings` 에 적고, 그 전체를 종합해 `result`
> 하나를 내세요. 하나라도 다르면 종합은 `mismatch` 입니다.

출력 JSON:

```json
{
  "result": "match|mismatch|missing|unknown",
  "mismatch_attributes": ["<어긋난 속성 이름>"],
  "findings": [
    {
      "fact_id": "<후보로 제시된 id>",
      "result": "match|mismatch|unknown",
      "mismatch_attributes": ["<어긋난 속성 이름>"],
      "quote": "<이 후보의 근거 원문 인용>",
      "reason": "<한국어 한 문장>"
    }
  ],
  "reason": "<종합 판단 근거 한두 문장>"
}
```

JSON 스키마는 **후보 수와 무관하게 항상 동일**하다(1건이면 `findings` 1건). 파서 분기를
만들지 않기 위해서다.

`build_compare_user` 는 이미 후보 전부를 `[id]` 와 함께 프롬프트에 넣고 있으므로
**변경하지 않는다.**

## 6. 코드가 하는 검증

제안은 LLM, 차단은 코드 — F7 의 인용 검증과 같은 권한 비대칭을 유지한다.

| 검사 | 실패 시 |
|---|---|
| `fact_id` 가 실제 후보 목록에 있나 | 해당 finding **드롭** + 계측 (기존 교집합 규칙과 동일) |
| `quote` 가 그 fact 의 `evidence_text` 안에 있나 | `quote_verified: false` 로 표시, **판정은 유지**, 리포트에 ⚠️ |

인용 검증 실패를 드롭으로 처리하지 않는 이유는, 그러면 종합 판정이 통째로 날아가
사용성이 크게 떨어지기 때문이다. 대신 사람이 검수할 수 있게 표시만 남긴다.

다만 LLM 이 finding 을 냈는데 **그것이 전부 드롭되면** 근거가 하나도 없는 판정이므로
`unknown` 으로 강등한다. LLM 이 애초에 finding 을 하나도 안 낸 경우(`findings: []`)는
여기에 해당하지 않는다 — `result: "missing"`("후보 중 기준과 같은 대상이 없다")의 정상
형태이므로 그대로 둔다. 이 둘을 합치면 정당한 `missing` 이 `unknown` 으로 바뀐다.

인용 대조는 공백 병합 후 부분일치로 한다(`fact_extractor._norm` 과 같은 규약).

## 7. 실패 처리 — 1:1 로 되돌아가지 않는다

| 상황 | 처리 |
|---|---|
| LLM 꺼짐(`compare_use_llm: false`) + N≥2 | `unknown` 보류. 사유: "후보 N건을 종합 판정할 수 없어 보류합니다" |
| 예산 초과(`LlmBudgetExceeded`) + N≥2 | 동일하게 `unknown` |
| JSON 파싱 실패(`ValueError`) + N≥2 | 동일하게 `unknown` |
| 위 상황 + N=1 | **기존 `_fallback` 그대로** (코드 판정으로 복귀) |

N≥2 에서 `_fallback` 이 코드 판정(= 임의의 `candidates[0]` 축약)으로 되돌아가면 지금
고치려는 오판이 조용히 그대로 남는다. 확신이 없으면 보류한다는 `unknown` 원칙(§6.2)의
정확한 적용 대상이다.

## 8. 계측

`compare_stats` 에 3개를 추가한다.

```text
multi_candidate_comparisons   후보 2건 이상이었던 비교 수
multi_candidate_overridden    그중 코드 1:1 판정과 최종 판정이 갈린 수  ← 이 변경의 효과
quote_unverified              인용 검증에 실패한 finding 수
```

`multi_candidate_overridden` 이 이 작업의 성과 지표다. 값이 0 이면 1:1 축약이 애초에
오판을 만들지 않았다는 뜻이므로, 그 자체가 유효한 정보다.

## 9. 리포트

기준 1행 = 1줄을 유지하고, 사유 아래에 후보별 내역을 붙인다.

```text
| 충전환경온도 | ⚠️ 불일치 | 4구간 중 3구간 일치 |
   ├ ✅ 0~10℃ 0.2C          "Charge temperature range 0~10C: 0.2C"
   ├ ⚠️ 10~45℃ 기준 1C vs 대상 0.7C
   ├ ✅ 45~50℃ 0.5C
   └ ✅ 50~60℃ 0.2C
```

판정 라벨은 `report/fact_report.py` 의 `LABEL`/`ORDER` 단일 출처를 그대로 쓴다. RAG 의
`runner.VERDICT_LABEL` 과 섞지 않는다.

`quote_verified: false` 인 finding 에는 ⚠️ 를 덧붙여 사람이 원문 확인을 하게 유도한다.

## 10. 테스트

`tests/test_multi_candidate_compare.py` (FakeLLM 주입, 플랫폼 독립).

1. 후보 1건 → 결과가 기존과 **완전히 동일** (회귀 방어)
2. 후보 2건 이상 → `finalize` 가 LLM 을 부른다 (게이트가 꺼져 있어도)
3. `compare_code` 는 후보가 몇 건이든 **LLM 0회** (기존 계약 유지)
4. LLM 이 `findings` 4건을 주면 종합 `mismatch` + 내역 4건이 실린다
5. 후보에 없는 `fact_id` 를 준 finding 은 드롭되고 계측에 남는다
6. 인용이 `evidence_text` 에 없으면 `quote_verified: false`, 판정은 유지
7. 모든 finding 이 드롭되면 `unknown`
8. LLM 없음 + 후보 2건 → `unknown` (코드 판정으로 복귀하지 않는다)
9. 골든셋 27건 회귀 (`golden/`)

## 11. 변경 파일

| 파일 | 변경 |
|---|---|
| `contentcompare/fact/fact_comparator.py` | `FactFinding` 신규 · `FactComparison` 필드 2개 · `_decide_by_llm` 다건 처리 · `_fallback` 분기 |
| `contentcompare/fact/prompts.py` | `COMPARE_SYSTEM` 원칙 4번 교체 + JSON 스키마 |
| `contentcompare/fact/pipeline.py` | `force_llm` 조건에 `len(candidates) >= 2` · 계측 3개 |
| `contentcompare/report/fact_report.py` | 후보별 내역 렌더 |
| `tests/test_multi_candidate_compare.py` | 신규 |

## 12. 남는 한계

- 기준(엑셀) 셀 안에 4구간이 문장으로 들어 있고 F3 가 그중 하나만 `attributes` 로 뽑은
  경우, 기준 쪽 손실은 이 변경으로 복구되지 않는다. 그것은 F3 추출의 문제이고
  `facts_by_block.json` 의 줄 단위 커버리지(Phase 4a)가 탐지 대상이다.
- 대상 쪽 fact 가 4건이 아니라 1건으로 뭉쳐 추출된 경우도 이 변경의 범위 밖이다
  (후보가 1건이므로 기존 경로를 탄다).
