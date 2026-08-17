# 2단계 Fact-linked Graph RAG 설계 — 코드로 먼저 비교하고 원문으로 재검사하기

> 작성일: 2026-08-10  
> 상태: **설계 개정안** — 기존 Fact 코드 비교를 1차 고속 경로로 유지하고,
> 실패·의심 결과만 Graph RAG로 재검사  
> 관련 문서: [`DESIGN.md`](DESIGN.md) · [`FACT_PIPELINE_PLAN.md`](FACT_PIPELINE_PLAN.md) ·
> [`FACT_F3_DESIGN.md`](FACT_F3_DESIGN.md) · [`FACT_F7_DESIGN.md`](FACT_F7_DESIGN.md) ·
> [`FACT_ATTR_GENERALIZATION_DESIGN.md`](FACT_ATTR_GENERALIZATION_DESIGN.md)

---

## 0. 한 줄 결론

기존 Fact 기반 코드 비교를 버리지 않는다. 먼저 현재의 구조화 Fact와 값·단위 비교 코드로
전체 항목을 빠르게 검사하고, **안전하게 확정된 `match`는 즉시 종료**한다. 1차 결과가
`unknown`, `missing`, `mismatch`이거나 `match`라도 근거가 불완전하면, 그 항목만 그래프가
찾아온 **검증 가능한 원문 Evidence**로 2차 검사한다.

```text
1차 Fact 코드 비교  = 빠른 대량 처리와 명확한 match 확정
Fact/Entity 그래프  = 재검사할 원문 위치를 찾는 지도
Word/PPT 원문       = 2차 검사의 실제 근거
LLM/코드            = 의심 항목만 정밀 재판정
```

이 문서에서는 이 방식을 **Cascaded Fact-linked Graph RAG** 또는 **2단계
Entity-guided Evidence RAG**로 부른다. Microsoft GraphRAG 전체를 그대로 도입하는 것이
아니라, Local Search의 핵심 패턴인 `Entity/Relationship + 원문 Text Unit` 결합을 현재
프로젝트의 **2차 정밀 검사기**로 적용하는 설계다.

---

## 1. 배경

ContentCompare는 엑셀 기준 문서의 각 항목을 Word/PPT/Excel 대상 문서와 비교한다. 현재 두
개의 주요 엔진이 서로 다른 장단점을 갖는다.

### 1.1 기존 RAG 엔진

대상 문서를 `DocItem`으로 읽고 고정 크기로 chunk한 뒤, 각 기준 항목으로 임베딩+BM25 검색을
수행한다. 검색된 상위 chunk를 LLM에 넣어 최종 판정한다.

장점:

- 최종 판단에서 원문을 직접 볼 수 있다.
- 사전에 모든 문장 구조를 정형화하지 않아도 된다.
- 문서 종류나 표현 방식이 달라도 LLM이 문맥을 해석할 수 있다.

한계:

- 정답 문장이 검색어와 직접 유사하지 않으면 후보에서 빠질 수 있다.
- paragraph/고정 글자 수 기반 경계가 실제 의미 단위와 다를 수 있다.
- 제목은 한 문단에 있고 값은 다음 여러 문단에 있으면 일부 문단만 검색된다.
- 표의 값은 검색되지만 상위 헤더가 누락되는 등 구조적 문맥이 끊길 수 있다.

### 1.2 Fact 엔진

Word/PPT를 블록 단위로 묶어 LLM에 전달하고, `entity_name`, `attributes`, `evidence_text`,
`source_ids`를 가진 Fact로 미리 변환한다. 이후 F7 Concept Graph가 같은 개념인지 판정하고,
F5 Comparator가 값을 비교한다.

장점:

- `Charge temperature ranges`처럼 표현이 반복되어도 같은 `entity_name`으로 묶는 의미 인식이
  비교적 잘 동작한다.
- 검색 대상이 원문 전체가 아니라 정리된 Fact이므로 관련 개념 후보를 찾기 쉽다.
- `source_ids`와 `evidence_text`가 있어 원문 위치를 추적할 기반이 이미 존재한다.
- 숫자와 단위를 구조화할 수 있으면 코드로 결정적인 비교가 가능하다.

한계:

- 자유 문서의 모든 의미를 `attributes: {name: {value, unit}}` 형태로 표현하기 어렵다.
- 조건, 예외, 순서, 인과관계, 반복 구간을 속성 맵으로 압축하면서 세부 내용이 손실될 수 있다.
- `evidence_text`가 원문에 존재하는지는 검증하지만, 필요한 원문을 **충분히** 포함했는지는
  검증하지 않는다.
- 같은 `entity_name`의 Fact가 여러 건이어도 비교 단계가 사실상 기준 Fact 1건과 대상 Fact
  1건의 최상위 후보를 비교한다.
- Fact 추출 단계에서 빠진 내용은 이후 Concept Graph에서도 복구할 수 없다. 이는
  [`FACT_F7_DESIGN.md`](FACT_F7_DESIGN.md)의 명시적 Out-of-scope이기도 하다.

---

## 2. 대표 문제

### 2.1 원문 예시

```text
Charge temperature ranges:
-5~5℃, 0.1C(4.55V)
5~12℃, 0.3C(4.55V)
12~15℃, 0.7C(4.55V)
15~45℃, 1.2C(4.20V)
```

LLM은 이 문장들을 동일한 `Charge temperature ranges` 개념으로 인식할 수 있다. 그러나
현재 구조에서는 다음 문제가 생긴다.

1. Word 내부 줄바꿈이 공백으로 평탄화될 수 있다.
2. 여러 조건을 하나의 `attributes` 맵에 자연스럽게 표현하기 어렵다.
3. 여러 Fact로 만들더라도 동일 이름 후보 중 하나만 최종 비교에 사용될 수 있다.
4. 첫 번째 조건만 `evidence_text`에 담겨도 원문에 실재하므로 검증을 통과한다.
5. 첫 번째 블록만 검색되거나 인용되면 나머지 조건은 `비교 불가`로 오판될 수 있다.

### 2.2 문제의 본질

문제는 LLM이 `Charge temperature ranges`라는 개념을 인식하지 못하는 것이 아니다. 오히려
의미 인식 결과를 다음 단계에서 너무 일찍 축약하고, 최종 비교를 그 축약본에 의존하는 것이
핵심이다.

```text
기존 RAG 실패: 올바른 원문 위치를 못 찾음
현재 Fact 실패: 위치는 알지만 원문의 상세 내용을 충분히 보존하지 못함
```

따라서 해결책은 다음 두 기능을 분리하는 것이다.

```text
관련 위치 탐색 → Fact/Entity 그래프
세부 내용 해석 → 원문 Evidence + LLM
```

### 2.3 개정된 해결 전략 — 전체를 Graph RAG로 보내지 않는다

기존 Fact 엔진은 구조화가 잘 되는 항목에서는 이미 효율적이다.

```text
Nominal voltage: 3.85V
Rated capacity: 1150mAh
Upper limit: 4.55V
```

이런 항목은 Entity가 올바르게 연결되고 값과 단위가 명확하면 LLM 없이 코드가 비교할 수
있다. 대상 문서 수가 늘어날수록 한 번 구조화한 값을 반복해서 코드로 비교하는 이점도 커진다.
따라서 새 설계의 목적은 Fact 비교를 대체하는 것이 아니라 **Fact 비교가 자신 있게 처리하지
못한 소수의 항목을 정밀 재검사하는 것**이다.

```text
전체 항목
   │
   ▼
1차: 기존 Fact 후보 매칭 + 코드 값 비교
   ├─ 안전한 match ───────────────▶ 최종 확정(LLM 호출 없음)
   └─ unknown/missing/mismatch
      또는 불완전한 match
             │
             ▼
2차: Fact-linked Graph RAG + 원문 Evidence 비교
             │
             ▼
        최종 결과 확정
```

이 구조가 비용을 줄이려면 2차 검사 비율이 실제로 낮아야 한다. 문서 품질이 낮거나 서로 다른
문서가 많아 `missing/mismatch` 비율이 높으면 절감 폭도 줄어든다. 따라서 “획기적 절감”을
가정하지 않고 `fast_path_rate`, `secondary_review_rate`, `LLM 호출/Entity`를 실측한다.

---

## 3. 설계 목표와 비목표

### 3.1 목표

1. 기존 Fact 후보 매칭과 코드 값·단위 비교를 1차 고속 경로로 유지한다.
2. 안전하게 확정 가능한 `match`는 Graph RAG와 비교 LLM을 호출하지 않는다.
3. `unknown`, `missing`, `mismatch`와 불완전한 `match`만 2차 검사한다.
4. Fact의 좋은 `entity_name` 인식 능력을 2차 원문 검색의 seed로 활용한다.
5. 모든 내용을 `attributes`로 강제하지 않고 원문 Evidence로 복구할 수 있게 한다.
6. 고정 글자 수 대신 문서 구조와 그래프 관계로 문맥을 동적으로 조립한다.
7. 동일 Entity에 연결된 여러 조건/문단/표 행을 한 번에 회수한다.
8. 그래프 탐색 실패 시 기존 BM25+임베딩 raw 검색으로 보완한다.
9. 같은 Entity의 여러 재검사 항목이 Evidence Bundle과 LLM 호출을 공유하게 한다.
10. 기존 F7 Concept Graph, Validator, ArtifactStore, 값·단위 코드 비교를 재사용한다.

### 3.2 비목표

- 모든 자유 문장을 완전한 지식 그래프의 술어로 변환하지 않는다.
- 모든 Claim을 정량 `attributes`로 변환하지 않는다.
- 기존 구조화 Fact와 코드 비교를 Lightweight Claim으로 전면 교체하지 않는다.
- Microsoft GraphRAG의 Community Detection/Global Summary 전체를 1차 구현에 도입하지 않는다.
- 그래프 연결 자체를 최종 사실 근거로 사용하지 않는다.
- LLM이 인용한 문구를 검증 없이 정답으로 받아들이지 않는다.
- 안전한 코드 `match`까지 무조건 Graph RAG로 다시 검사하지 않는다.
- 그래프 DB 도입을 선행 조건으로 두지 않는다. 초기에는 JSON/SQLite/메모리 인덱스로도
  충분하다.

---

## 4. 핵심 설계 원칙

### 4.0 1차 결과를 보존하고 2차 결과를 덮어쓰지 않는다

2차 검사는 1차 결과를 없애는 후처리가 아니다. 두 판정을 모두 저장하고 최종 결과가 왜
변경되었는지 추적한다.

```text
initial_result = mismatch (code)
review_trigger = code_mismatch
secondary_result = match (graph_llm)
final_result = match
result_changed = true
```

이 정보가 없으면 개선인지 새로운 오판인지 진단할 수 없다.

### 4.1 Graph는 정답 저장소가 아니라 탐색 지도다

그래프에 현재 Fact의 `attributes`와 짧은 `evidence_text`만 저장하면 이미 누락된 내용은
복구할 수 없다. 그래프 노드는 반드시 원본 Text Unit을 가리켜야 한다.

```text
Entity ─has_claim→ Claim ─supported_by→ EvidenceUnit ─belongs_to→ Document
```

### 4.2 구조화는 선택 사항이다

`attributes`는 다음과 같이 명확한 값에만 사용한다.

```text
Nominal voltage: 3.85V
```

조건·예외·설명처럼 구조가 불명확한 내용은 `claim_text`에 보존한다.

```text
At low temperature, charging current shall be reduced according to the table below.
```

`attributes`가 없더라도 `entity_name + claim_text + source_refs`가 있으면 검색 가능한 Claim이다.

### 4.3 원문 보존이 그래프보다 먼저다

줄바꿈, 표 행, 헤더 관계를 원문 추출 단계에서 잃으면 그래프도 복구할 수 없다. 따라서
Word의 hard paragraph와 soft line break, 표의 행/셀/헤더를 구분해 저장해야 한다.

### 4.4 최종 비교 입력은 원문이다

1차에서는 기존 Fact/Attribute를 코드로 직접 비교한다. 2차로 분기된 항목에서는 Fact/Claim을
후보 탐색과 문맥 조립에 사용하고, Comparator에 기준 항목과 검증된 Evidence Bundle을
전달한다.

안전한 1차 `match`를 확정하려면 단순히 공통 attribute 몇 개가 같다는 것만으로 부족하다.
다음 **Fast-path Acceptance Gate**를 모두 통과해야 한다.

- 비교 대상 Entity 연결이 확정되어 있음
- 기준의 비교 필수 attribute가 대상에서 충분히 커버됨
- 값·단위·범위를 코드로 모호함 없이 비교 가능
- 기준/대상 Fact가 Validator 저신뢰 상태가 아님
- 같은 Entity의 중복 Fact나 상충값이 없음
- evidence/source locator가 유효함
- 조건형/반복형 Fact가 단일값 Fact로 축약된 징후가 없음

하나라도 만족하지 않으면 코드 결과가 `match`여도 2차 검사 대상으로 보낸다.

### 4.5 실패 경로를 구분한다

다음을 하나의 `missing`으로 합치지 않는다.

- Entity를 찾지 못함
- Entity는 찾았지만 연결된 Evidence가 없음
- Evidence는 있으나 필요한 조건이 없음
- 조건은 있으나 해석할 수 없음
- 문서 전체 탐색 후에도 관련 내용이 없음

---

## 5. 제안 아키텍처

```text
┌───────────────────────────────────────────────────────────────────────┐
│ 공통 인덱싱: raw → compact → profile/records → Fact → Validator      │
└───────────────────────────────────┬───────────────────────────────────┘
                                    ▼
┌───────────────────────────────────────────────────────────────────────┐
│ 1차 Fast Path                                                        │
│ deterministic 개념 연결 → attributes 코드 비교 → Acceptance Gate    │
└───────────────────┬──────────────────────────────────┬────────────────┘
                    │ 안전한 match                     │ review 필요
                    ▼                                  ▼
          ┌──────────────────┐          ┌──────────────────────────────┐
          │ 즉시 최종 확정    │          │ Review Queue                 │
          │ LLM 호출 0        │          │ mismatch/missing/unknown/    │
          └──────────────────┘          │ unsafe match                 │
                                        └──────────────┬───────────────┘
                                                       ▼
                                        ┌──────────────────────────────┐
                                        │ 2차 Evidence Graph RAG       │
                                        │ Entity seed → graph expansion│
                                        │ → raw fallback → context     │
                                        └──────────────┬───────────────┘
                                                       ▼
                                        ┌──────────────────────────────┐
                                        │ Evidence Comparator          │
                                        │ 코드 재비교 → 애매하면 LLM    │
                                        └──────────────┬───────────────┘
                                                       ▼
                                        ┌──────────────────────────────┐
                                        │ 최종 결과 + 1·2차 이력       │
                                        └──────────────────────────────┘
```

### 5.1 기존 기능을 어디까지 동일하게 사용하는가

다음 단계는 모든 항목에 대해 현재와 동일하게 수행한다.

```text
F0 physical_raw/compact_raw
F1 Document Profile 및 Excel Schema
F2 Excel Record 정규화
F3 Structured Fact 추출
F4a Validator와 저신뢰 표시
```

그 이후는 비용 목표에 따라 두 수준으로 나눈다.

#### 호환 우선 v1

현재처럼 F7 Concept Graph를 먼저 만들고 F5 코드 비교까지 수행한 뒤 분기한다. 기존 결과와 새
2차 검사 효과를 분리해 검증하기 쉽지만, F7 개념 판정 LLM 비용은 그대로 남는다.

#### 비용 최적화 목표 v2

F7을 다음처럼 둘로 나눈다.

```text
F7a Deterministic Concept Resolver (1차)
    ontology aliases/same_as
    정규화 entity_name 완전일치
    이미 승격·캐시된 개념 관계
    → LLM 호출 없음

F7b Lazy Concept Review (2차)
    F7a에서 연결되지 않은 Review Queue의 후보만 LLM 판정
```

그 다음 F5 코드 값·단위 비교와 Acceptance Gate를 실행한다. 목표 구조에서는 모든 후보 쌍을
먼저 F7 LLM에 보내지 않는다. 이름/온톨로지로 안전하게 연결되는 대량 항목은 코드만 거치고,
번역·약칭·모호한 개념만 2차 Graph RAG에서 판정한다.

최종 분기점은 `FactComparator._decide_by_code()`가 코드 판정과 불확실성 정보를 만든
**직후**다.
현재는 코드가 확정하지 못하거나 근거가 불안하면 `FactComparator._decide_by_llm()`으로 바로
넘어간다. 개정안에서는 그 전에 Acceptance Gate를 두고 다음처럼 나눈다.

```text
안전한 code match → 최종 결과
그 외              → Graph Evidence Review Queue
```

즉, 현재 Fact 비교 LLM에 전달하던 애매한 항목을 새 Graph RAG 2차 검사기로 대체한다. 기존
Fact-only LLM 비교는 호환용 fallback 모드로 남길 수 있지만 기본 경로에서는 중복 호출하지
않는다.

주의: Word/PPT F3 Fact 추출과 Document Profile은 인덱싱 단계에서 여전히 LLM을 사용한다.
“Fast Path LLM 0회”는 이미 만들어진 Fact를 문서 간 비교하는 단계의 추가 LLM 호출이 0이라는
뜻이다. 문서별 Fact artifact를 캐시·재사용할수록 대상 문서가 많고 반복 실행이 잦은 환경에서
효과가 커진다.

### 5.2 2차 검사에 사용하는 세 계층

#### A. Entity 계층

문서의 비교 개념을 나타낸다.

```json
{
  "entity_id": "entity:charge-temperature-range",
  "canonical_name": "Charge temperature ranges",
  "aliases": ["충전 온도 범위", "charging temperature range"],
  "entity_path": ["Charging", "Temperature conditions"]
}
```

F7의 `same_as`/`differs_by`를 재사용할 수 있다. 단, 이름이 같다는 이유만으로 무조건 병합하지
않고 문서 섹션과 `entity_path`를 함께 고려한다.

#### B. Claim 계층

원문의 비교 가능한 진술을 가볍게 표현한다. 모든 Claim이 `attributes`를 가질 필요는 없다.
다만 Claim이 기존 Structured Fact를 대체하지는 않는다.

```text
Structured Fact → 1차 코드 비교용
Lightweight Claim → 2차 원문 탐색용
```

하나의 F3 LLM 응답에서 두 projection을 함께 만들거나, 기존 Fact에서 Claim을 파생할 수 있다.
기존 Fact의 값·단위 구조는 Fast Path를 위해 유지한다.

```json
{
  "claim_id": "claim:word:17",
  "entity_id": "entity:charge-temperature-range",
  "claim_text": "5~12℃에서는 0.3C, 4.55V 조건으로 충전한다.",
  "attributes": {
    "temperature_range": {"value": "5~12", "unit": "℃"},
    "charge_rate": {"value": 0.3, "unit": "C-rate"},
    "charge_voltage": {"value": 4.55, "unit": "V"}
  },
  "source_refs": ["word:w_b013"]
}
```

위 `attributes`는 선택적 최적화다. LLM이 안정적으로 구조화하지 못하면 빈 맵이어도 된다.
그 경우 최종 비교는 `claim_text`와 EvidenceUnit 원문을 사용한다.

#### C. Evidence 계층

문서의 실제 텍스트와 구조를 보존한다.

```json
{
  "evidence_id": "word:w_b013:l01",
  "document_id": "doc:target-a",
  "block_id": "w_b013",
  "unit_type": "line",
  "order": 13,
  "raw_text": "5~12℃, 0.3C(4.55V)",
  "normalized_text": "5~12℃, 0.3C(4.55V)",
  "parent_id": "word:w_b013",
  "heading_path": ["Charging", "Charge temperature ranges"]
}
```

`raw_text`는 인용용, `normalized_text`는 검색용이다. 둘을 분리해야 검색 정규화가 원문 인용을
훼손하지 않는다.

---

## 6. 그래프 관계

### 6.1 의미 관계

| 관계 | 의미 | 생성 주체 |
|---|---|---|
| `same_as` | 번역어·약칭을 포함한 동일 개념 | F7 LLM 제안 + 코드 검증/온톨로지 |
| `differs_by` | 유사하지만 비교 축이 다른 개념 | F7/온톨로지 |
| `has_claim` | Entity에 속한 Claim | F3 LLM + 코드 조립 |
| `related_to` | 같은 문맥에서 관련되지만 동일하지 않음 | 선택적, 초기 버전 제외 가능 |

### 6.2 근거 관계

| 관계 | 의미 |
|---|---|
| `supported_by` | Claim의 근거가 되는 EvidenceUnit |
| `mentioned_in` | Entity가 직접 언급된 EvidenceUnit |
| `derived_from` | 정규화된 값이 나온 원문 위치 |

### 6.3 문서 구조 관계

| 관계 | 예 |
|---|---|
| `next` / `previous` | 연속 문단·연속 목록 행 |
| `parent` | line → paragraph, cell → table row |
| `under_heading` | 문단 → 가장 가까운 상위 제목 |
| `same_list` | 동일 목록 그룹 |
| `same_table` | 동일 표에 속한 행 |
| `header_of` | 열/상위 병합 헤더 → 표 행·셀 |
| `same_slide` | PPT 도형/노트의 슬라이드 공속 |

초기 구현에서는 `has_claim`, `supported_by`, `next`, `under_heading`, `same_table`, `same_as`
정도로 시작하고 실측으로 필요한 관계만 늘린다.

---

## 7. 인덱싱 상세

### 7.1 F0: 원문 추출과 구조 보존

현재 `raw/word_raw.py`는 문단 내부의 `<w:br>`/`<w:cr>`을 공백으로 바꾸고 전체 공백을
정리한다. 새 경로에서는 다음을 함께 보존한다.

- 문단 ID와 문서 순서
- 문단 내부 line break와 line별 sub-ID
- Word style/heading level/list 정보
- 표의 행·셀·병합·헤더 관계
- 검색용 정규화 텍스트와 인용용 원문 텍스트

예시:

```text
w_b012:l01 Charge temperature ranges:
w_b012:l02 -5~5℃, 0.1C(4.55V)
w_b012:l03 5~12℃, 0.3C(4.55V)
w_b012:l04 12~15℃, 0.7C(4.55V)
```

Word의 실제 문단이 분리되어 있으면 각 문단 ID를 유지하고, soft line break인 경우에만 line
sub-ID를 부여한다.

### 7.2 F3 유지 + Lightweight Claim projection 추가

1차 코드 비교를 살리려면 기존 Structured Fact를 없애거나 `attributes`를 약화하면 안 된다.
기존과 같이 최대 20개 블록을 한 번에 LLM에 전달하고 현재 Fact를 만든다. 그 결과에서 2차
검색용 Claim을 별도로 파생한다.

가장 안전한 v1은 F3 출력 계약을 바꾸지 않는 것이다.

```text
Fact.entity_name   → Claim.entity_name
Fact.entity_path   → Claim.entity_path
Fact.evidence_text → Claim.claim_text 초기값
Fact.source        → Claim.source_ids
Fact.attributes    → Claim 검색 텍스트의 선택적 보조 정보
```

원문 세부 내용은 Claim에 복사하려 하지 않고 `source_ids`가 가리키는 EvidenceUnit에서 읽는다.
Fact에 없는 내용도 찾을 수 있도록 모든 EvidenceUnit을 별도 BM25/임베딩 fallback 인덱스에
넣는다.

v1 실측에서 Claim 표현력이 부족한 경우에만 F3 프롬프트를 확장해 `facts`와 `claims`를 함께
받는다. 이때도 `facts`는 Fast Path용 기존 계약을 유지한다.

```json
{
  "facts": [
    {
      "entity_name": "Charge temperature ranges",
      "attributes": {
        "temperature_range": {"value": "5~12", "unit": "℃"},
        "charge_rate": {"value": 0.3, "unit": "C-rate"},
        "charge_voltage": {"value": 4.55, "unit": "V"}
      },
      "evidence_text": "5~12℃, 0.3C(4.55V)",
      "source_ids": ["w_b012:l03"]
    }
  ],
  "claims": [
    {
      "entity_name": "Charge temperature ranges",
      "entity_path": ["Charging"],
      "claim_text": "5~12℃, 0.3C(4.55V)",
      "attributes": {},
      "source_ids": ["w_b012:l03"],
      "confidence": 0.94
    }
  ]
}
```

프롬프트 원칙:

1. 기존 Structured Fact와 attributes를 계속 생성한다.
2. Claim에서는 비교 가능한 내용을 대표값 하나로 축약하지 않는다.
3. 같은 Entity의 반복 조건은 Claim 여러 건으로 보존할 수 있다.
4. 자유 서술은 `claim_text`에 그대로 둔다.
5. Claim마다 가장 작은 유효 `source_ids`를 인용한다.
6. 같은 원문 조각을 근거 없이 여러 Claim에 복제하지 않는다.
7. 제목과 후속 목록을 같은 배치에 유지한다.

이 확장은 인덱싱 LLM 호출 수를 늘리지 않고 같은 응답의 출력만 확장하지만, JSON 파싱 실패와
출력 토큰은 증가할 수 있다. 그래서 v1 파생 projection을 먼저 검증한다.

### 7.3 배치 경계

단순히 매 20개 paragraph에서 자르면 제목과 값 목록이 서로 다른 배치에 들어갈 수 있다.
따라서 `20`은 하드 경계가 아니라 목표 크기로 사용한다.

```text
heading + 하위 연속 문단/목록 = 하나의 구조 그룹
표 전체 또는 헤더+행 묶음     = 하나의 구조 그룹
슬라이드 도형+노트            = 하나의 구조 그룹
```

구조 그룹 하나가 20블록을 넘으면 단독 배치로 보내고, 모델 토큰 한도를 넘는 경우에만 그룹
내부를 자르되 제목/헤더를 각 조각에 반복한다.

### 7.4 Entity Resolution

Claim에서 추출한 `entity_name`을 다음 순서로 통합한다.

1. `knowledge/ontology.yaml`의 aliases/same_as
2. 동일 문서·동일 `entity_path` 내 정규화 이름 완전일치
3. BM25/임베딩으로 후보 생성
4. F7 Concept Graph의 LLM 판정 및 인용 검증
5. `differs_by` 제약과 모순 검사

`Charge temperature ranges`가 네 번 등장하면 Claim은 네 건이어도 Entity 노드는 하나다.

### 7.5 그래프 조립

각 Claim의 검증된 source ID를 이용해 코드가 관계를 만든다.

```text
Entity --has_claim--> Claim
Claim  --supported_by--> EvidenceUnit
EvidenceUnit --next--> EvidenceUnit
EvidenceUnit --under_heading--> EvidenceUnit(heading)
```

LLM이 임의의 source ID를 생성하면 현재와 같이 실제 배치 ID와 교집합을 취하고, 유효 source가
없으면 Claim을 그래프에 연결하지 않는다. 단, 삭제만 하지 말고 드롭 사유를 계측한다.

### 7.6 검색 인덱스

다음 세 인덱스를 분리한다.

- Entity 인덱스: `canonical_name + aliases + entity_path`
- Claim 인덱스: `claim_text + optional attributes`
- Evidence 인덱스: `normalized_text + heading_path`

Entity 검색이 실패해도 Claim/Evidence 검색을 fallback으로 실행할 수 있어야 한다.

---

## 8. 질의와 비교 상세

### 8.1 1차 Fast Path

각 `기준 Fact × 대상 문서`에 대해 F7a가 온톨로지·완전일치·승격 관계로 후보를 만든다.
호환 우선 v1에서는 기존 ConceptMatcher 결과를 그대로 사용할 수 있다. 후보가 있으면 기존
코드가 공통 attribute의 값과 단위를 비교한다. 후보가 없으면 `unresolved_concept` 또는
`code_missing`으로 Review Queue에 넣는다. 현재 `FactComparator.compare()`처럼 내부에서
곧바로 LLM까지 호출하지 않고 코드 판정 결과를 중간 객체로 반환하도록 책임을 분리한다.

```python
@dataclass
class ComparisonProbe:
    reference_fact: Fact
    target_doc: str
    candidates: list[MatchCandidate]
    code_result: str                 # match|mismatch|missing|unknown
    code_reason: str
    mismatch_attributes: list[str]
    review_reasons: list[str]
    safe_to_finalize: bool
```

구현 선택지는 두 가지다.

1. `FactComparator.compare_code()`를 새로 만들고 `_decide_by_code()`를 재사용한다.
2. `compare(..., decision_mode="code_probe")`를 추가한다.

책임이 명확한 1번을 권장한다. 기존 `compare()`는 호환 모드로 유지할 수 있다.

현재 `_decide_by_code()`는 양쪽의 **공통 attribute만** 모두 같으면 `match`를 반환한다. 기준에
세 속성이 있고 대상에 한 속성만 있어도 그 한 개가 같으면 match가 될 수 있으므로 Fast Path
Gate에서는 별도 커버리지를 계산해야 한다.

```text
attribute_coverage = 비교 가능한 기준 attribute 중 대상에서 대응된 수
                     / 비교 가능한 기준 attribute 전체 수
```

기본은 `attribute_coverage == 1.0`일 때만 즉시 확정한다. 단일 속성의 이름만 다른 기존
호환 규칙은 Concept/Attribute alias가 확인된 경우에만 예외로 허용한다. 조건형 반복 Fact,
동일 Entity 다중 후보, 상충값은 coverage가 1이어도 `duplicate_entity_facts` 또는
`conditional_series` 사유로 2차 검사한다.

표준 `review_reasons` 후보:

```text
code_mismatch
code_missing
code_unknown
low_confidence
partial_attribute_coverage
duplicate_entity_facts
conflicting_candidates
conditional_series
invalid_evidence
unresolved_concept
```

### 8.2 2차 검사 분기 정책

기본 정책은 다음과 같다.

| 1차 결과 | 기본 처리 | 이유 |
|---|---|---|
| 안전한 `match` | 즉시 최종 확정 | 가장 빈번할 가능성이 높고 코드 판정의 비용 이점이 큼 |
| 불완전한 `match` | 2차 검사 | 공통 속성 일부만 같거나 반복 조건이 누락됐을 수 있음 |
| `mismatch` | 2차 검사 | 잘못된 후보 1건이나 조건 불일치로 생긴 오판인지 원문 확인 필요 |
| `missing` | 2차 검사 | Fact 추출/개념 연결/검색 누락과 실제 부재를 구분해야 함 |
| `unknown` | 2차 검사 | 자유 서술·단위 모호·공통 속성 부재를 원문으로 해소 |

`mismatch`가 매우 많은 업무에서는 비용이 다시 증가할 수 있다. 운영 설정으로 다음 두 정책을
제공할 수 있다.

- `accuracy_first`: 모든 mismatch 재검사
- `balanced`: 후보·근거가 불안한 mismatch만 재검사하고, 검증된 단일 스칼라 mismatch는 확정

초기 도입은 사용자 제안대로 `accuracy_first`를 기본으로 측정한다.

Review Queue는 항목별로 즉시 LLM을 호출하지 않고 `(target_doc, entity_id)`로 묶는다. 같은
Entity에 속한 여러 기준 항목은 그래프 탐색과 Evidence Bundle을 공유한다.

### 8.3 여기서 Query의 의미

Query는 질문별로 LLM에게 새 요약을 요청한다는 의미가 아니다. 엑셀 기준 항목을 검색 키로
사용해 인덱스와 그래프를 조회하는 코드 작업이다.

```text
Excel 행 → 검색 문자열 구성 → Entity/Claim/Evidence 조회
```

LLM 호출은 최종 비교 또는 정말 애매한 Entity 판정에만 사용한다.

2차 검사 대상으로 분기된 항목에 대해서만 Query를 만든다. 첫 번째 단계에서 얻은
`entity_name`, 후보 Fact, mismatch attribute, 탈락 후보와 source ID를 검색 seed로 재사용해
처음부터 검색을 다시 시작하지 않는다.

### 8.4 Entity Seed Retrieval

각 기준 항목에서 다음을 검색 텍스트로 사용한다.

- `entity_name`
- `entity_path`
- 기준 원문 텍스트
- 단위와 값 형식
- 온톨로지 aliases

후보 생성은 병렬 채널로 수행한다.

1. 이름/alias 완전일치
2. BM25
3. 임베딩
4. 단위·숫자 패턴

점수는 Entity를 확정하는 절대 기준이 아니라 후보를 줄이는 recall 용도로 사용한다. 동일 개념
확정은 기존 F7의 `same_as` 규칙을 따른다.

### 8.5 Graph Expansion

선택된 Entity에서 제한적으로 확장한다.

```text
hop 0: 선택 Entity
hop 1: has_claim, mentioned_in, same_as
hop 2: supported_by, next/previous, under_heading, same_table
```

기본은 1~2 hop이며 다음을 우선한다.

- 같은 대상 문서
- 같은 섹션/슬라이드/표
- 직접 `supported_by`로 연결된 근거
- 문서 순서상 연속된 목록

`related_to`를 따라 무제한 확장하지 않는다.

### 8.6 Adaptive Context Builder

가져온 EvidenceUnit을 고정 단어 수로 자르지 않고 구조적으로 조립한다.

#### Word

- 적중 line/paragraph
- 가장 가까운 상위 heading
- 동일 목록의 앞뒤 항목
- 연속 형식이 유지되는 후속 문단
- 다음 heading에서 중단

#### Word/PPT 표

- 적중 셀만이 아니라 전체 행
- 열 헤더와 병합된 상위 헤더
- 필요하면 같은 Entity를 다루는 인접 행

#### PPT

- 적중 도형
- 슬라이드 제목
- 같은 슬라이드의 관련 도형
- 스피커 노트

#### Excel

- 적중 행
- 열 헤더/멀티헤더
- 상위 분류 carry-over

겹치는 Evidence는 병합하고 문서 순서로 정렬한다. 토큰 예산을 초과하면 직접 근거,
제목/헤더, 인접 문맥 순으로 우선순위를 둔다.

### 8.7 Reranking

그래프에서 가져온 원문이 많을 수 있으므로 Evidence Bundle 단위로 재정렬한다.

점수 예시:

```text
final_score =
    entity_match_score
  + claim_relevance_score
  + direct_source_bonus
  + same_section_bonus
  + structural_continuity_bonus
  - graph_distance_penalty
```

정확한 가중치는 golden 데이터로 결정한다. 그래프 hop 수만으로 관련성을 확정하지 않는다.

### 8.8 2차 최종 비교

Comparator 입력:

```text
[기준 항목]
entity/path/기준값/단위/원문

[대상 Evidence Bundle]
문서 위치 + 검증 가능한 원문
```

판정 순서:

1. 1차 코드 판정과 `review_reasons`를 함께 전달한다.
2. 회수한 원문에서 명확한 숫자·단위·범위를 다시 구조화할 수 있으면 코드로 재비교한다.
3. 조건 대응, 자유 서술, 예외만 LLM으로 비교한다.
4. LLM evidence quote가 실제 EvidenceUnit에 있는지 코드로 검증한다.
5. 검증 실패 시 `unknown` 또는 재시도하며 1차 결과를 조용히 확정하지 않는다.
6. `initial_result`, `secondary_result`, `final_result`, `decided_by`를 모두 저장한다.

### 8.9 동일 Entity 질의 재사용

엑셀에 같은 Entity의 세부 필드가 여러 개 있을 수 있다.

```text
충전 온도 범위
충전 전류
충전 전압
```

세 항목이 모두 `Charge temperature ranges` Entity로 매핑되면 그래프 조회와 Evidence Bundle을
한 번만 생성하고 공유한다. 최종 비교도 같은 Bundle에 여러 기준 필드를 묶어 배치 처리할 수
있다. 따라서 항목 수만큼 문서를 다시 요약할 필요가 없다.

---

## 9. 예시 동작

### 9.1 인덱싱 결과

```text
Entity E1: Charge temperature ranges

Claim C1: -5~5℃, 0.1C(4.55V)   → source w_b012:l02
Claim C2: 5~12℃, 0.3C(4.55V)   → source w_b012:l03
Claim C3: 12~15℃, 0.7C(4.55V)  → source w_b012:l04
Claim C4: 15~45℃, 1.2C(4.20V)  → source w_b012:l05
```

### 9.2 엑셀 기준 항목

```text
항목명: 충전 온도 범위
규격: 5~12℃에서 0.3C, 4.55V
```

### 9.3 1차 코드 비교와 분기

1. 기존 Fact Matcher가 `Charge temperature ranges` 후보를 찾는다.
2. 현재 1:1 후보 선택으로 C1에 해당하는 `-5~5℃ / 0.1C` Fact가 선택될 수 있다.
3. 기준 `5~12℃ / 0.3C`와 값이 달라 1차 결과가 `mismatch`가 된다.
4. 정책에 따라 `code_mismatch` 사유로 Review Queue에 들어간다.

2차 검사에서는 다음을 수행한다.

1. 이미 확인한 E1을 graph seed로 사용한다.
2. E1의 C1~C4를 모두 조회한다.
3. 각 Claim의 source를 따라 `w_b012:l01~l05`를 가져온다.
4. heading과 연속 line을 하나의 Evidence Bundle로 만든다.
5. 기준 조건 `5~12℃`와 C2 원문을 비교한다.

### 9.4 결과

```json
{
  "initial_result": "mismatch",
  "review_trigger": ["code_mismatch", "duplicate_entity_facts"],
  "secondary_result": "match",
  "final_result": "match",
  "result_changed": true,
  "decided_by": "graph_llm",
  "matched_entity": "Charge temperature ranges",
  "matched_claims": ["C2"],
  "evidence": "5~12℃, 0.3C(4.55V)",
  "source": "word:w_b012:l03",
  "reason": "기준 온도 범위의 충전 전류와 전압이 대상 원문과 일치합니다."
}
```

전체 온도 표를 비교하면 조건별 결과를 합산한다.

- 모든 구간 일치: `match`
- 일부 구간 없음: `partial`
- 같은 구간의 값 다름: `mismatch`
- 구간 해석 불가: `unknown`

### 9.5 Fast Path 예시

```text
기준: Nominal voltage = 3.85V
대상: Nominal voltage = 3.85V
```

Entity가 확정되어 있고 양쪽 evidence가 유효하며 단일 attribute의 값과 단위가 일치하면
Acceptance Gate를 통과한다.

```json
{
  "initial_result": "match",
  "secondary_result": null,
  "final_result": "match",
  "review_trigger": [],
  "decided_by": "code",
  "result_changed": false
}
```

이 항목에는 Evidence Graph 검색과 비교 LLM 호출이 발생하지 않는다.

---

## 10. 현재 코드와의 통합 지점

### 10.1 유지할 구성요소

- `raw/`: Office 파일 물리 정보 추출 기반
- Structured `Fact.entity_name`, `entity_path`, `attributes`, `source`, `evidence_text`
- F7 Concept Graph의 `same_as`/`differs_by`와 온톨로지
- BM25/임베딩 후보 생성
- `FactComparator._decide_by_code()`의 값·단위 비교
- ArtifactStore 캐시와 단계별 계측
- evidence 실재 검증과 실행 로그

### 10.2 수정할 구성요소

| 영역 | 변경 방향 |
|---|---|
| `raw/word_raw.py` | line break, line sub-ID, heading/list 구조 보존 |
| `raw/compact.py` | `raw_text`/`normalized_text` 및 구조 관계 compact 출력 |
| `fact/fact_models.py` | 기존 Fact 계약 유지; 별도 Claim/Evidence 참조 또는 review metadata 추가 |
| `fact/prompts.py` | 기존 Fact 출력 유지; 필요 시 Claim별 최소 source를 추가 출력 |
| `fact/fact_extractor.py` | 기존 Fact 생성 유지 + Lightweight Claim projection 생성 |
| `fact/fact_matcher.py` | 동일 Entity의 다중 후보를 보존하고 첫 항목 조기 종료 제거 |
| `fact/concept_builder.py` | 전체 선행 판정 호환 + Review Group 대상 Lazy F7 진입점 제공 |
| `fact/fact_comparator.py` | 코드 probe와 기존 LLM fallback 분리; 안전한 match gate 지원 |
| `fact/pipeline.py` | 1차 결과 분기, Review Queue, 2차 결과 병합 단계 연결 |
| `report/fact_report.py` | initial/secondary/final 판정과 변경 사유 표시 |

### 10.3 `FactPipeline._compare_from_store()` 개정 흐름

현재의 문서 처리·FactStore는 유지하고 비교 루프를 두 단계로 나눈다. 아래는 비용 최적화 목표
v2의 의사코드다. 호환 우선 v1에서는 `fast_concept_resolver` 대신 현재 `_build_graph()`와
`ConceptMatcher`를 그대로 넣을 수 있다.

```python
known_graph = self._load_known_concepts()       # ontology/승격 관계, LLM 없음
review_queue = []

for target in store.targets:
    matcher = FastConceptResolver(known_graph, ref_doc, target)

    for ref_fact in ref_doc.facts.facts:
        candidates = matcher.search(ref_fact)

        # 기존 _decide_by_code를 호출하지만 여기서는 LLM을 호출하지 않는다.
        probe = fast_comparator.compare_code(
            ref_fact,
            candidates,
            target,
            ref_low_confidence=ref_doc.is_low_confidence(ref_fact),
        )

        if acceptance_gate.can_finalize(probe):
            results.append(FinalComparison.from_fast_path(probe))
        else:
            review_queue.append(probe)

# 대상 문서+Entity별로 묶어 동일 검색/문맥/LLM 호출을 공유한다.
for review_group in group_reviews(review_queue):
    # 미해결 개념에 대해서만 기존 F7 판정 로직을 지연 실행할 수 있다.
    reviewed_concepts = lazy_concept_reviewer.resolve(review_group)
    bundle = graph_retriever.retrieve(review_group)
    secondary = evidence_comparator.compare(review_group, bundle)
    results.extend(merge_initial_and_secondary(review_group, secondary))
```

현재 `FactComparator.compare()`는 코드 판정 뒤 불확실하면 `_decide_by_llm()`을 호출한다. 새
`cascaded` 모드에서는 이 호출을 하지 않고 Review Queue로 넘겨야 한다. 그렇지 않으면 같은
항목에 Fact-only LLM과 Graph RAG LLM이 연속으로 호출되어 비용 절감 목적이 사라진다.

최종 결과 모델은 기존 필드를 유지하면서 판정 이력을 추가한다.

```python
@dataclass
class CascadedComparison:
    reference_fact: Fact
    target_doc: str
    initial_result: str
    secondary_result: str | None
    final_result: str
    review_triggers: list[str]
    result_changed: bool
    decided_by: str                 # code|graph_code|graph_llm|legacy_llm
    target_facts: list[Fact]
    evidence_bundle_id: str | None
    evidence: list[dict]
    reason: str
```

기존 리포트 호환을 위해 `.result`는 `final_result`를 반환하는 property로 둘 수 있다.

### 10.4 신규 모듈 제안

파일명은 구현 시 조정할 수 있다.

```text
contentcompare/fact/evidence_models.py
    EvidenceUnit, EvidenceBundle, ClaimNode, EntityNode

contentcompare/fact/evidence_graph.py
    그래프 조립, 관계 검증, 직렬화

contentcompare/fact/graph_retriever.py
    Entity seed 검색, 제한 hop 확장, fallback 검색

contentcompare/fact/context_builder.py
    Word/PPT/Excel 구조별 Evidence Bundle 조립

contentcompare/fact/evidence_comparator.py
    기준 Fact/Record와 Evidence Bundle 비교

contentcompare/fact/review_router.py
    Acceptance Gate, Review Queue 그룹화, 1·2차 결과 병합

contentcompare/fact/fast_concept_resolver.py
    ontology/완전일치/승격 관계 기반 무LLM 후보 연결
```

기존 Concept Graph와 새 Evidence Graph의 책임은 다르다.

```text
F7 Concept Graph:
    서로 다른 문서의 두 개념을 비교해도 되는가?

Evidence Graph:
    그 개념의 실제 근거는 문서 어디에 있고 무엇을 함께 읽어야 하는가?
```

둘은 하나의 저장소로 합칠 수 있지만 코드 책임은 분리하는 편이 안전하다.

---

## 11. 설정 초안

```yaml
fact:
  comparison_strategy: cascaded    # legacy | graph_all | cascaded

  concept_resolution:
    mode: deterministic_then_lazy  # current_full_f7 | deterministic_then_lazy
    fast_sources: [ontology, exact_name, promoted]
    lazy_llm_for_review: true

  fast_path:
    enabled: true
    finalize_safe_match: true
    require_full_attribute_coverage: true
    reject_duplicate_entity_facts: true
    reject_low_confidence: true

  secondary_review:
    enabled: true
    policy: accuracy_first          # accuracy_first | balanced
    review_results: [mismatch, missing, unknown]
    review_unsafe_match: true
    group_by_entity: true
    max_llm_calls: 100

  claim_extraction:
    batch_blocks: 20
    preserve_line_breaks: true
    mode: derive_from_fact          # derive_from_fact | dual_output

  evidence_graph:
    max_hops: 2
    include_next_blocks: 3
    include_previous_blocks: 1
    include_heading: true
    include_table_headers: true
    max_entities: 5
    max_claims: 20

  evidence_context:
    max_tokens: 6000
    rerank_top_k: 8
    merge_overlapping_units: true

  fallback:
    raw_hybrid_search: true
    legacy_fact_llm: false
    fallback_top_k: 5
```

초기에는 설정 항목을 모두 노출하지 말고 코드 기본값으로 시작한 뒤, golden 실측에서 실제로
조정이 필요한 값만 `config.example.yaml`에 공개한다.

---

## 12. 실패 처리와 fallback

### 12.1 권장 상태

| 상태 | 의미 |
|---|---|
| `entity_not_found` | 기준 항목에 대응하는 Entity 후보가 없음 |
| `evidence_not_linked` | Entity는 있으나 source 연결이 없음 |
| `condition_not_found` | 관련 원문은 있으나 기준 조건이 없음 |
| `conflict` | 동일 조건에 상충하는 복수 원문 존재 |
| `unknown` | 원문은 있으나 해석/단위가 모호함 |
| `not_found_after_fallback` | 그래프와 raw hybrid 검색 모두 실패 |

최종 리포트에서는 필요에 따라 기존 `missing/unknown/partial`로 매핑하되, 내부 진단 정보는
위 상태를 보존한다.

### 12.2 fallback 순서

```text
Entity Graph 검색
    ↓ 실패/근거 부족
Claim 인덱스 검색
    ↓ 실패/근거 부족
Raw Evidence BM25+Embedding 검색
    ↓
구조적 Context 확장
```

그래프가 잘못 구축되어도 기존 RAG의 recall 경로를 잃지 않게 한다.

### 12.3 커버리지 계측

현재 `blocks_cited`만으로는 블록 첫 문장만 인용해도 전체 블록이 커버된 것으로 보일 수 있다.
다음 계측을 추가한다.

- line/row 단위 `units_in`, `units_linked`, `units_uncited`
- 숫자·단위가 있는 EvidenceUnit 중 Claim 미연결 수
- Entity별 Claim 수와 EvidenceUnit 수
- Claim별 인용 토큰 커버리지
- 그래프 검색 성공률과 raw fallback 사용률
- 최종 Evidence에 포함되었지만 판정에서 사용되지 않은 unit 수

---

## 13. 비용과 성능

### 13.1 인덱싱 비용

기존 Word/PPT F3와 마찬가지로 20블록 내외 배치 추출을 유지하므로 LLM 호출 규모는 크게
늘리지 않을 수 있다. 관계 전체를 LLM으로 추출하지 않고 대부분을 source ID와 문서 구조로
코드 생성한다.

기존 Fact 추출 LLM 비용은 그대로 남는다. 호환 우선 v1에서는 F7 Concept Graph 비용도
남지만, 비용 최적화 v2에서는 온톨로지/완전일치/승격 관계를 먼저 적용하고 미해결 Review
Group에 대해서만 Lazy F7을 호출한다. 새 설계가 우선 줄이는 것은 **F5 이후 항목별 비교 LLM
비용**이며, v2에서는 개념 판정 LLM도 함께 줄인다. Claim을 `derive_from_fact`로 만들면 추가
인덱싱 LLM은 없다.

### 13.2 질의 비용

Entity/Claim/Evidence 검색과 graph traversal은 코드로 수행한다. 전체 비교 건수를 `N`,
Fast Path 확정 비율을 `p`, 2차 검사 그룹 수를 `G`라고 하면 개략적인 비용은 다음과 같다.

```text
현재 항목별 LLM fallback 비용 ≈ 애매한 항목 수 × Fact LLM 비용
개정 비용                  ≈ N × 매우 작은 코드 비용 + G × Graph Evidence LLM 비용
```

`G`는 2차 항목 수와 같지 않다. 같은 `(대상 문서, Entity)` 항목을 하나로 묶기 때문이다. `p`가
높고 Entity 재사용이 많을수록 절감 효과가 크다. 반대로 실제 mismatch/missing이 대부분이면
Graph RAG 호출도 많아지므로 절감 효과를 실측해야 한다.

캐시 키 예시:

```text
hash(reference_entity + target_doc_version + graph_version + retrieval_config)
```

### 13.3 Full GraphRAG를 바로 도입하지 않는 이유

Community report와 전역 요약은 문서 전체 경향을 묻는 질문에 유용하지만, 사양 항목의 근거
검색에는 과도할 수 있다. 이 프로젝트는 먼저 Entity Local Search와 원문 Text Unit 연결만
구현한다. 이후 다중 섹션·다중 문서 추론이 실제 병목으로 확인되면 DRIFT/커뮤니티 계층을
검토한다.

---

## 14. 단계별 구현 계획

### Phase 0 — Golden과 진단 기준 고정

- 대표 문서에서 정답 EvidenceUnit을 line/row 단위로 표시
- 현재 RAG/Fact 엔진의 결과와 실패 원인 저장
- `Charge temperature ranges` 같은 반복 조건 사례 포함

완료 조건:

- 엔진별 `entity_recall`, `evidence_recall`, 최종 판정 정확도를 같은 데이터로 비교 가능

### Phase 1 — 코드 Probe와 Review Router 분리

- `FactComparator._decide_by_code()`를 재사용하는 `compare_code()` 추가
- Acceptance Gate와 `review_reasons` 구현
- `match/mismatch/missing/unknown`별 Review Queue 생성
- 기존 `_decide_by_llm()` 직접 호출은 `legacy` 모드에만 유지
- initial/final 결과 모델과 통계 추가
- 이 단계에서는 현재 F7/ConceptMatcher를 그대로 써 변경 원인을 격리

완료 조건:

- 안전한 단일값 match는 비교 LLM 0회로 최종 확정됨
- 나머지 항목은 결과를 잃지 않고 Review Queue에 들어감
- 실제 데이터의 `fast_path_rate`와 결과별 분기 비율을 측정할 수 있음

### Phase 2 — Deterministic Concept Fast Path와 Lazy F7

- ontology alias/same_as, 정규화 이름 완전일치, 승격 관계 전용 Resolver 추가
- 이 Resolver에서 연결되지 않은 항목은 `unresolved_concept`로 Review Queue에 등록
- 기존 F7 LLM concept 판정을 Review Group에 대해서만 지연 실행
- `current_full_f7` 호환 스위치 유지

완료 조건:

- 온톨로지/완전일치 항목은 비교 단계 LLM 없이 후보 연결과 코드 비교가 끝남
- 번역·모호 개념만 Lazy F7 또는 Evidence Graph로 넘어감
- full F7 대비 concept LLM 호출 절감량을 측정할 수 있음

### Phase 3 — 원문 무손실 EvidenceUnit

- Word line break와 sub-ID 보존
- heading/list/table 관계 추출
- `raw_text`와 `normalized_text` 분리
- EvidenceUnit artifact 저장 및 뷰어 지원

완료 조건:

- 예제 네 온도 조건이 서로 구분된 ID와 원문으로 저장됨

### Phase 4 — Fact 기반 Claim projection과 그래프 조립

- 기존 Structured Fact와 attributes 계약 유지
- v1은 Fact의 entity/source/evidence로 Lightweight Claim 파생
- 필요성이 입증된 경우에만 F3 dual-output 프롬프트 추가
- Entity→Claim→Evidence 관계 생성
- 같은 Entity의 다중 Claim 보존
- F7 same_as/ontology 연결 재사용

완료 조건:

- `Charge temperature ranges` 하나의 Entity 아래 네 Claim과 네 Evidence가 연결됨
- Claim 미연결 EvidenceUnit도 raw fallback 인덱스에서 검색 가능

### Phase 5 — Graph Retriever와 Adaptive Context

- Entity seed retrieval
- 제한된 1~2 hop graph expansion
- 문서 구조별 Context Builder
- raw hybrid fallback
- Evidence Bundle artifact 및 상세 로그

완료 조건:

- 예제 중 어느 온도 구간으로 검색해도 전체 관련 구간 또는 필요한 구간+헤더가 회수됨

### Phase 6 — Evidence Comparator와 결과 병합

- Evidence Bundle 기반 코드/LLM 비교
- Claim 집합 및 조건별 결과 집계
- 인용 실재 검증
- initial/secondary/final 결과 병합과 변경 이력 저장
- `(target_doc, entity_id)` 그룹 단위 LLM 배치
- 세분화 실패 상태와 리포트 연결

완료 조건:

- 첫 구간만 인용하고 전체 항목을 `match`로 확정하는 오판이 발생하지 않음
- Fast Path 결과는 2차 LLM을 호출하지 않음
- 2차 결과가 1차 결과를 변경한 이유를 artifact에서 확인 가능

### Phase 7 — Shadow 운영과 전환

- `comparison_strategy=cascaded`에서 기존 initial 결과와 새 final 결과 동시 저장
- golden 및 라이브 문서에서 차이 검토
- `legacy`, `graph_all`, `cascaded` 비용·정확도 비교
- 기준을 충족하면 `cascaded`를 기본값으로 전환

---

## 15. 테스트 전략

### 15.1 단위테스트

- 안전한 scalar match의 Acceptance Gate 통과
- 공통 attribute 일부만 같은 match의 Gate 거부
- duplicate Entity Fact/저신뢰 Fact의 Gate 거부
- mismatch/missing/unknown의 Review Queue 등록
- Fast Path 항목에서 비교 LLM 호출 0회
- `(target_doc, entity_id)`별 Review Queue 그룹화
- initial/secondary/final 결과 병합과 `result_changed` 계산
- Word hard paragraph/soft line break 보존
- table row/header 관계 생성
- 동일 `entity_name`의 Claim 여러 건 보존
- source ID 할루시네이션 거부
- graph hop 제한과 순환 방지
- EvidenceUnit 중복 병합과 문서 순서 유지
- raw fallback 발동 조건
- 인용문 실재 검증

### 15.2 통합테스트

1. 제목 1개 + 후속 조건 4문단
2. 한 문단 내부 soft line break 조건 4개
3. 표 헤더 + 조건 행 10개
4. 제목과 값이 배치 경계에 걸리는 문서
5. 같은 이름이 다른 섹션에서 다른 의미로 사용되는 문서
6. 번역어/약어로 동일 Entity를 표현한 기준·대상 문서
7. Entity 연결이 실패하지만 raw BM25로는 검색되는 문서

모든 테스트는 기존 원칙대로 FakeLLM/FakeEmbedder를 주입해 Office/네트워크 없이 실행한다.

### 15.3 핵심 지표

| 지표 | 의미 |
|---|---|
| `fast_path_rate` | 전체 비교 중 코드 결과로 즉시 확정된 비율 |
| `secondary_review_rate` | Graph RAG 2차 검사로 넘어간 비율 |
| `unsafe_match_rate` | 코드 match였지만 Gate가 거부한 비율 |
| `secondary_change_rate` | 2차 검사가 1차 결과를 변경한 비율 |
| `changes_by_initial_result` | mismatch/missing/unknown별 결과 변경률 |
| `entity_recall@k` | 정답 Entity가 후보에 포함된 비율 |
| `evidence_recall@k` | 정답 원문 line/row가 Evidence Bundle에 포함된 비율 |
| `condition_coverage` | 반복 조건 중 회수된 조건 비율 |
| `false_missing_rate` | 원문에 있는데 `missing` 처리한 비율 |
| `citation_validity` | 최종 인용이 원문에 실재하는 비율 |
| `comparison_accuracy` | match/mismatch/partial/unknown 최종 정확도 |
| `fallback_rate` | 그래프 실패로 raw RAG를 사용한 비율 |
| `tokens_per_item` | 항목별 평균 입력 토큰 |
| `llm_calls_per_entity` | 같은 Entity에 대한 LLM 호출 재사용 수준 |
| `llm_calls_avoided` | `graph_all` 대비 Fast Path로 피한 LLM 호출 수 |
| `concept_llm_calls_avoided` | `current_full_f7` 대비 Lazy F7로 피한 개념 판정 호출 수 |
| `latency_fast_path_p95` | 코드 전용 항목의 p95 처리시간 |
| `latency_secondary_p95` | 2차 검사 항목의 p95 처리시간 |

단순 최종 정확도만 보면 검색 실패와 비교 실패를 구분할 수 없으므로 최소한
`entity_recall → evidence_recall → comparison_accuracy`를 단계별로 측정한다.

---

## 16. 위험과 대응

| 위험 | 영향 | 대응 |
|---|---|---|
| LLM이 Claim 자체를 누락 | 그래프에서도 검색 불가 | raw Evidence fallback, 미연결 unit 계측 |
| 잘못된 Entity 병합 | 다른 사양을 함께 비교 | F7 비대칭 권한, entity_path/section 사용 |
| 그래프 과확장 | 토큰 증가·노이즈 | max hop, 같은 section 우선, rerank |
| 너무 작은 EvidenceUnit | 문맥 단절 | heading/list/table 기반 Context Builder |
| 너무 큰 EvidenceUnit | 조건 누락 탐지 어려움 | line/row sub-ID + overlap 병합 |
| attributes가 다시 필수화 | 자유 문서 손실 재발 | `claim_text`를 1급 필드로 유지 |
| 그래프 구축 비용 증가 | 처리시간 증가 | 구조 관계는 코드 생성, LLM 관계 추출 최소화 |
| 잘못된 code match가 2차 검사를 우회 | 조용한 오판 | 엄격한 Acceptance Gate, unsafe match 표본 감사 |
| mismatch가 많아 2차 비용 급증 | 기대한 비용 절감 실패 | Entity별 배치, balanced 정책, 비율 사전 계측 |
| Fact-only LLM과 Graph LLM 중복 호출 | 비용·지연 이중 발생 | cascaded 모드에서 기존 `_decide_by_llm` 우회 |
| 2차 결과가 1차 이력을 덮어씀 | 원인 추적 불가 | initial/secondary/final 결과 모두 보존 |
| GraphRAG 도입 자체가 목적화 | 복잡도만 증가 | golden 개선 지표를 단계별 gate로 사용 |

---

## 17. 의사결정 요약

| 질문 | 결정 |
|---|---|
| 기존 Fact 코드 비교를 유지할 것인가? | **예.** 모든 항목의 1차 Fast Path로 사용한다. |
| 어떤 결과를 2차 검사할 것인가? | 기본적으로 mismatch/missing/unknown과 unsafe match를 검사한다. |
| 안전한 code match도 Graph RAG로 보낼 것인가? | **아니오.** Acceptance Gate 통과 시 즉시 확정한다. |
| 모든 내용을 attributes로 만들 것인가? | **아니오.** Fact의 명확한 값에는 유지하고 자유 내용은 Claim/원문에 둔다. |
| Fact를 없앨 것인가? | **아니오.** 1차 코드 비교와 2차 검색 seed의 두 역할로 사용한다. |
| 그래프가 최종 답을 갖는가? | **아니오.** 그래프는 원문 Evidence로 안내한다. |
| chunk를 고정 크기로 만들 것인가? | **아니오.** 최소 EvidenceUnit은 보존하되 질의 시 구조적으로 조립한다. |
| 질문마다 문서를 다시 요약할 것인가? | **아니오.** 2차 항목만 코드 검색하고 Entity별로 묶어 비교한다. |
| 기존 Fact 비교 LLM도 함께 호출할 것인가? | 기본 `cascaded` 모드에서는 **아니오.** Graph RAG와 중복 호출하지 않는다. |
| 동일 Entity의 여러 Claim은 어떻게 처리하는가? | 모두 보존하고 같은 Entity 아래 그룹화한다. |
| 그래프 검색이 실패하면? | Claim/Evidence 하이브리드 검색으로 fallback한다. |
| Full Microsoft GraphRAG를 도입할 것인가? | 초기에는 도입하지 않고 Local Search 패턴만 구현한다. |

---

## 18. 최종 완료 조건

- [ ] 기존 Structured Fact와 코드 값·단위 비교가 1차 경로로 유지된다.
- [ ] 온톨로지/완전일치/승격 개념은 F7 LLM 없이 1차 후보로 연결된다.
- [ ] 미해결 개념만 Lazy F7 또는 2차 Evidence 검색으로 넘어간다.
- [ ] 안전한 code match는 2차 검색/비교 LLM 없이 최종 확정된다.
- [ ] mismatch/missing/unknown/unsafe match가 Review Queue로 분기된다.
- [ ] Review Queue가 대상 문서+Entity 단위로 묶여 Evidence와 LLM 호출을 공유한다.
- [ ] initial/secondary/final 결과와 변경 사유가 artifact/report에 남는다.
- [ ] Word 원문의 line/paragraph/table 구조가 EvidenceUnit으로 무손실 보존된다.
- [ ] Claim/Evidence가 자유 문서 내용을 attributes로 강제하지 않는다.
- [ ] 동일 Entity의 다중 Claim이 유실 없이 그래프에 연결된다.
- [ ] Entity에서 원문 source까지 추적 가능하다.
- [ ] 검색된 원문은 heading/list/table 문맥과 함께 동적으로 조립된다.
- [ ] 그래프 실패 시 raw BM25+임베딩 fallback이 동작한다.
- [ ] 최종 판정의 모든 evidence가 원문에 실재한다.
- [ ] `missing`이 검색 실패인지 실제 부재인지 진단 가능하다.
- [ ] 기존 RAG/Fact 대비 golden의 false missing과 evidence recall이 개선된다.
- [ ] `graph_all` 대비 LLM 호출·토큰·지연이 줄고 정확도 목표를 충족한다.

---

## 19. 참고 자료

- [Microsoft GraphRAG Query Engine](https://github.com/microsoft/graphrag/blob/main/docs/query/overview.md) — Local Search가 지식 그래프와 원문 text chunk를 함께 사용하는 구조
- [Microsoft GraphRAG Custom Graphs](https://microsoft.github.io/graphrag/index/byog/) — Entity/Relationship와 `text_unit_ids` 연결 요구
- [Microsoft GraphRAG Indexing Methods](https://github.com/microsoft/graphrag/blob/main/docs/index/methods.md) — Standard/FastGraphRAG 인덱싱 비교
- [LightRAG: Simple and Fast Retrieval-Augmented Generation](https://aclanthology.org/2025.findings-emnlp.568/) — 저수준 상세 정보와 고수준 개념의 dual-level retrieval
- [LazyGraphRAG: Setting a new standard for quality and cost](https://www.microsoft.com/en-us/research/blog/lazygraphrag-setting-a-new-standard-for-quality-and-cost/) — 전체 선행 요약보다 질의 중심의 비용 적응형 검색이 유리할 수 있다는 연구 방향
