# Phase F7 상세 설계 — 개념 그래프 (Concept Graph)

> 작성일: 2026-08-04
> 상태: **설계** — 파일·JSON 스키마·프롬프트·테스트를 고정한다.
> 상위 계획: [`FACT_PIPELINE_PLAN.md`](FACT_PIPELINE_PLAN.md)
> 선행: [`FACT_F3_DESIGN.md`](FACT_F3_DESIGN.md)(`facts.json`), F4a Validator, F5 Matcher/Comparator
> 실측 근거: [`FACT_F3_5_LIVE_REPORT.md`](FACT_F3_5_LIVE_REPORT.md) §6·§9.4

---

## 0. 범위와 목표

F5 는 "기준 fact 에 대응하는 대상 fact 가 **있는가**"를 임베딩 코사인 점수와 임계값으로
판단한다. F7 은 이 판단을 **개념 그래프 조회**로 대체한다.

### In-scope

1. **개념 그래프 데이터 모델** — 노드(개념) + 엣지(`same_as` / `differs_by`), 실행 전체에서 **하나**.
2. **후보 쌍 생성(코드)** — 임베딩/BM25 는 여기서만 쓴다. 판정에는 쓰지 않는다.
3. **개념 관계 유도(LLM 배치)** — 후보 쌍마다 같은 개념인지/무엇이 달라 다른 개념인지 판정.
4. **그래프 조립·검증(코드)** — `same_as` 병합, `differs_by` 제약, 모순 검출.
5. **영속 온톨로지** — 사람이 확정한 관계를 `knowledge/ontology.yaml` 로 승격해 재사용.
6. **F5 연결 변경** — Matcher 가 유사도 대신 그래프를 조회한다.

### Out-of-scope

- **F3 추출 누락**은 해결하지 않는다. fact 가 애초에 추출되지 않으면(§4 `고객 표준 버전`)
  개념 층이 정확해도 `missing` 이다. 그것은 추출 단계의 문제다.
- **값 판정**(단위 배수 관계 등)은 F5 Comparator 가 그대로 담당한다. F7 은 "비교해도 되는가"만 답한다.
- 승격용 UI. v1 은 `knowledge/ontology.yaml` 직접 편집이다.

---

## 1. 문제 — 유사도는 부정 판정을 표현할 수 없다

실측(bge-m3, 기준 20 fact ↔ Word 11 fact). 완전일치 10건은 전부 정확했고, 임베딩으로
임계를 통과한 4건 중 **3건이 비교 대상이 아닌 쌍**이었다:

| 기준 항목 | 임베딩 top1 | 점수 | 실제 |
|---|---|---|---|
| 고객 표준 버전 | 문서 기준 규격 | 0.7656 | 같은 개념 |
| 평가환경온도 | 표준환경온도 | 0.6944 | **다른 개념** |
| 충전환경온도 | 정격 충전 전압 | 0.6551 | **다른 개념** |
| 1개월저장온도 | 표준환경온도 | 0.6084 | **다른 개념** |

`0.6084`(다른 개념) · `0.6944`(다른 개념) · `0.7656`(같은 개념)이 한 구간에 섞여 있다.
`entity_name` 만 임베딩해도 정답 0.87~0.97 vs 오답 0.61~0.87 로 여전히 겹친다.

원인은 임계값 선정 실패가 아니다. **코사인 점수에는 "무관함"을 뜻하는 지점이 없다.**
0.61 과 0.77 은 정도의 차이일 뿐이며, "이 둘은 다른 항목이다"라는 **부정 판정을 표현할
수단이 아니다.** 임계값은 이 질문에 원리적으로 답할 수 없다.

또한 점수는 모델 종속이다. `match_min_score=0.65` 는 bge-m3 실측치이며, 코사인 절대
스케일이 다른 모델(예: multilingual-e5-large)에서는 사실상 "전부 통과"가 된다.

---

## 2. 설계 원칙

### 2.1 고정하는 것은 어휘가 아니라 계약

배터리 스펙에서는 (물리량 / 측정조건 / 한정)이 구분 축이지만, 계약서는 (의무주체 / 기한 /
산정방식), 재무는 (계정과목 / 기간 / 연결범위)다. **축 목록을 코드에 넣으면 도메인마다
코드를 고쳐야 한다.**

그래서 LLM 이 축과 개념을 자유롭게 만들되 **그래프의 형식만 고정**한다. 코드는 엣지
종류(`same_as` / `differs_by`)만 보고 집행하며, `axis` 값("측정조건", "산정방식", …)이
무슨 뜻인지 **해석하지 않는다**.

선례가 있다: F1 Schema Inducer 는 문서마다 다른 컬럼 의미(`semantic_role`)를 LLM 이
유도하고 코드는 그것을 규칙으로 소비한다. F7 은 같은 패턴을 개념 층으로 확장한 것이다.

### 2.2 판정 규칙은 하나

> **두 fact 의 개념이 `same_as` 로 이어져 있지 않으면 비교하지 않는다.**

임계값 없음, 슬롯 어휘 없음, 도메인 사전 없음. "연결이 없으면 없는 것"이 기본값이다.

`differs_by` 는 판정에 **필수가 아니다**. 판정은 `same_as` 존재 여부만으로 결정된다.
`differs_by` 는 (a) 왜 아닌지를 사람에게 설명하고 (b) 승격되면 다음 실행에서 같은 쌍을
LLM 에 다시 묻지 않게 하는 **재발 방지 데이터**다.

### 2.3 권한은 비대칭 — 연결은 LLM, 차단은 코드

틀린 연결(무관한 쌍을 mismatch 로 보고)은 사람을 잘못된 수정으로 유도한다. 놓친 연결은
`missing` 으로 남아 사람이 확인하면 된다. 손해가 비대칭이므로 권한도 비대칭으로 준다:

- **연결(`same_as`)** 은 LLM 이 제안하고 근거 인용이 검증을 통과해야 성립한다.
- **차단**은 코드가 단독으로 할 수 있다(그래프 모순, 근거 인용 실패, 승격된 `differs_by`).

### 2.4 유사도의 역할 축소

임베딩/BM25 는 **LLM 에게 검토시킬 후보 쌍을 좁히는 용도로만** 남는다. 판정에서 빠지므로
임계값을 느슨하게(기본 0.3) 잡아도 안전하다 — **틀려도 손해가 작다.** 낮게 잡으면 LLM
호출이 늘고, 높게 잡으면 후보가 안 만들어져 `missing` 이 된다. 최적값을 고를 필요가 없다.

---

## 3. 데이터 모델

### 3.1 `concept_graph.json` (실행 산출물)

실행 전체에서 하나. 저장 위치는 `comparison_result.json` 과 같은 **기준 문서 폴더**다
(`artifacts/<기준문서>/concept_graph.json`) — 실행 단위 산출물이라는 점이 같고, 새로운
폴더 규약을 만들지 않기 위해서다.

```json
{
  "nodes": [
    {
      "concept_id": "c-0001",
      "label": "1개월저장온도",
      "members": [
        {"doc": "자표준문서.xlsx", "fact_id": "fact-row-20", "entity_name": "1개월저장온도"}
      ]
    }
  ],
  "edges": [
    {
      "type": "same_as",
      "from": "c-0003",
      "to": "c-0014",
      "axis": "",
      "evidence": {
        "from_text": "배터리승인규격 ver 4.7 SEC Req. ver.4.7",
        "to_text": "본 규격은 배터리승인규격 ver 4.7 (SEC Req. ver.4.7) 을 따른다."
      },
      "reason": "둘 다 SEC Req. ver.4.7 을 가리킨다.",
      "decided_by": "llm",
      "promoted": false
    },
    {
      "type": "differs_by",
      "from": "c-0001",
      "to": "c-0009",
      "axis": "측정조건",
      "evidence": {"from_text": "-10.0, 35.0, 80.0", "to_text": "표준환경온도, 21 ~ 29 (중심 25), ℃"},
      "reason": "1개월 저장 조건과 상시 환경 조건은 서로 다른 규격이다.",
      "decided_by": "llm",
      "promoted": false
    }
  ],
  "stats": {"pairs_considered": 0, "pairs_from_ontology": 0, "pairs_by_llm": 0,
            "same_as": 0, "differs_by": 0, "unknown": 0, "rejected": 0, "llm_calls": 0}
}
```

**노드 정체성**: 노드는 fact 가 아니라 **개념**이다. 처음에는 fact 하나당 노드 하나로
시작하고, `same_as` 가 확정되면 병합된다. `members` 가 여러 문서에 걸쳐 있는 노드가
"두 문서가 같은 것을 말하고 있다"는 표현이다.

### 3.2 `knowledge/ontology.yaml` (사람이 관리하는 영속 온톨로지)

사람이 확정한 관계만 들어간다. `knowledge/*.md`(도메인 지식)와 같은 human-in-the-loop
자리이며, 실행마다 갱신되지 않는다.

```yaml
# 같은 개념 — 표현만 다르다
same_as:
  - names: ["고객 표준 버전", "문서 기준 규격"]
    reason: "둘 다 SEC Req. ver.4.7 을 가리킨다"

# 다른 개념 — 비교 대상이 아니다
differs_by:
  - names: ["1개월저장온도", "표준환경온도"]
    axis: "측정조건"
    reason: "저장 조건과 상시 환경 조건은 다른 규격"
  - names: ["평가환경온도", "평가환경습도"]
    axis: "물리량"
```

**키는 정규화된 `entity_name`** 이다(`fact_matcher.norm_name` 재사용 — 공백·기호 제거).
fact_id 는 실행마다 바뀌므로 쓸 수 없다. 항목명이 문서마다 달라도 `names` 에 나열된 이름
중 둘이 매칭되면 적용된다.

승격 규칙: `concept_graph.json` 의 엣지를 사람이 검토해 이 파일로 옮긴다. v1 은 직접
편집이며, `promoted: true` 로 표시되는 것은 로드 시점에 코드가 판단한다(파일에 있으면 승격).

---

## 4. 파이프라인

```
facts.json (문서별, F3) + validation_report.json (F4a)
   ↓ F7-1 후보 쌍 생성            (코드: 완전일치 + 임베딩/BM25 recall)
   ↓ F7-2 온톨로지 조회            (코드: 승격된 관계는 LLM 을 건너뛴다)
   ↓ F7-3 개념 관계 유도           (LLM: 남은 쌍만 배치)
   ↓ F7-4 그래프 조립·검증        (코드: same_as 병합, 모순 검출)
concept_graph.json
   ↓ F5 Matcher (변경)            (그래프 조회 — 같은 노드의 대상 fact 만 후보)
   ↓ F5 Comparator (그대로)       (값·단위 대조, 애매하면 LLM)
comparison_result.json → F6 리포트
```

### F7-1 후보 쌍 생성 (코드)

기준 문서 fact × 각 대상 문서 fact 에 대해:

1. 정규화 `entity_name` **완전일치** → 무조건 후보(점수 1.0).
2. 임베딩 코사인 상위 `concept_recall_top_k`(기본 5) 중 `concept_recall_min`(기본 0.3) 이상.
3. 임베더가 없으면 BM25 로 폴백(기존 `FactMatcher` 규약 재사용).

출력은 `(ref_fact, target_fact, recall_score)` 목록이다. **이 점수는 이후 판정에 쓰지
않는다** — 리포트에 참고용으로만 남긴다.

### F7-2 온톨로지 조회 (코드)

`knowledge/ontology.yaml` 에 두 항목명의 관계가 이미 있으면 그 관계를 그대로 채택하고
LLM 후보에서 제외한다. 이것이 재현성과 비용을 동시에 해결하는 장치다 — 실행을 거듭하고
사람이 승격할수록 LLM 이 새로 판단할 몫이 줄어든다.

### F7-3 개념 관계 유도 (LLM 배치)

남은 쌍을 `concept_batch_pairs`(기본 20) 단위로 묶어 호출한다. 프롬프트에 함께 넣는 것:

- `document_profile.json` 의 `main_purpose`(도메인 문맥 — 축을 그 도메인에 맞게 짓게 한다)
- `knowledge/*.md`(용어·표기 규칙)
- 이미 승격된 온톨로지 요약(일관성 유지)

응답은 쌍마다 `same_as` / `differs_by(axis)` / `unknown` 중 하나 + **양쪽 원문 인용**이다.

### F7-4 그래프 조립·검증 (코드)

1. **근거 검증**: `evidence.from_text` / `to_text` 가 해당 fact 의 `evidence_text` 또는
   `search_text` 에 실재하는지 확인(F4a 의 `evidence_missing` 검사와 같은 정규화 규칙).
   실패하면 그 엣지를 **거부**하고 `rejected` 로 계측한다 — LLM 이 `same_as` 를 남발해도
   근거가 없으면 성립하지 않는다(§2.3).
2. **`same_as` 병합**: union-find 로 노드를 합친다.
3. **`differs_by` 제약**: 병합 결과가 `differs_by` 로 연결된 두 노드를 같은 노드로 만들면
   그 병합을 **거부**하고 해당 `same_as` 를 `unknown` 으로 강등한다. 승격된
   `differs_by`(사람이 확정)가 있으면 그쪽이 항상 이긴다.
4. **무결성 검사**: 존재하지 않는 노드 참조, 같은 쌍에 `same_as`+`differs_by` 동시 존재,
   자기 자신 참조. 발견 시 해당 엣지를 강등하고 검증 리포트에 남긴다.

### F5 Matcher 변경

`FactMatcher.search(ref)` 가 유사도 대신 그래프를 조회한다:

- 기준 fact 가 속한 노드의 `members` 중 **해당 대상 문서**의 fact 를 후보로 반환.
- 없으면 빈 리스트 → 기존 코드 경로 그대로 `missing`.
- `MatchCandidate.method = "concept"`, `score` 는 recall 점수(참고용),
  `needs_review` 는 **온톨로지 승격 여부**로 재정의한다 — 사람이 승격한 관계면 `False`,
  이번 실행에서 LLM 이 판단한 관계면 `True`. 즉 **승격 전에는 값 판정도 LLM 이 한 번 더
  본다.** 보수적이지만, 아직 사람이 확인하지 않은 연결 위에서 코드가 `mismatch` 를
  단정하지 않게 하려는 의도다. 승격되면 그 비용이 사라진다.

`match_min_score` / `match_review_score` 는 비교 경로에서 사용하지 않게 되며, recall
파라미터로 대체된다(§7 마이그레이션).

---

## 5. 프롬프트 규약

`CONCEPT_SYSTEM` 의 원칙(현행 프롬프트 규약과 동일한 어조):

1. 두 항목이 **같은 대상에 대한 같은 주장**이면 `same_as`. 표기·언어·단위가 달라도 된다.
2. 다르면 `differs_by` 와 **무엇이 다른지 축 이름**을 직접 지어 쓴다(예: 측정조건, 기간,
   물리량, 산정방식). 정해진 목록은 없다 — 이 문서의 도메인에 맞게 지어라.
3. **판단이 서지 않으면 `unknown`.** 틀린 단정보다 보류가 낫다.
4. `same_as` 를 쓰려면 **양쪽 원문을 그대로 인용**해야 한다. 인용할 원문이 없으면 `same_as`
   를 쓸 수 없다.
5. 값이 다른 것은 `differs_by` 의 근거가 **아니다**. 값 비교는 다음 단계가 한다. 여기서는
   "같은 것을 말하고 있는가"만 판단한다.

원칙 5가 중요하다. "21~29 와 -10~80 은 값이 다르니 다른 개념"이라는 추론을 허용하면,
값이 다른 정상적인 불일치(=우리가 찾으려는 것)를 개념 층이 삼켜버린다.

출력은 쌍 배열 JSON 하나다:

```json
{"pairs": [
  {"ref_fact_id": "fact-row-20", "target_fact_id": "fact-word-11",
   "relation": "differs_by", "axis": "측정조건",
   "from_text": "...", "to_text": "...", "reason": "..."}
]}
```

---

## 6. 검증 (F4a 확장)

`validator.py` 에 그래프 검사를 추가한다. 코드는 **위상만** 본다:

| 검사 | 심각도 | 처리 |
|---|---|---|
| `concept_evidence_missing` — 인용이 원문에 없음 | error | 엣지 거부 |
| `concept_contradiction` — 같은 쌍에 same_as + differs_by | error | 둘 다 unknown 강등 |
| `concept_merge_violation` — 병합이 differs_by 를 위반 | error | 해당 same_as 강등 |
| `concept_dangling_node` — 없는 노드 참조 | error | 엣지 폐기 |
| `concept_unknown_pair` — 관계 미정 | warn | 리포트 "검토 필요" |

결과는 `validation_report.json` 에 기존 검사와 같은 형식으로 합류한다.

---

## 7. 설정

```yaml
fact:
  # --- F7 개념 그래프 ---
  use_concept_graph: true          # false 면 F5 가 기존 유사도 매칭으로 동작(롤백 스위치)
  concept_recall_top_k: 5          # 기준 fact 당 LLM 에 검토시킬 후보 수
  concept_recall_min: 0.3          # 후보 생성 최소 점수(판정 아님 — 계산량 제한)
  concept_batch_pairs: 20          # 한 LLM 호출당 판정할 쌍 수
  max_llm_calls_per_concept: 30    # 개념 단계 LLM 호출 예산
  ontology_path: knowledge/ontology.yaml
```

**마이그레이션**: `match_min_score` / `match_review_score` 는 `use_concept_graph: true`
일 때 사용되지 않는다. 설정 키는 남겨 두고(롤백용) `config.example.yaml` 주석에 "F7 사용
시 무시됨"을 명시한다.

---

## 8. 파일 구성

| 파일 | 역할 |
|---|---|
| `contentcompare/fact/concept_models.py` | `ConceptNode` / `ConceptEdge` / `ConceptGraph`(+`to_dict`/`from_dict`/`from_llm`) |
| `contentcompare/fact/concept_builder.py` | F7-1~F7-4 오케스트레이션 |
| `contentcompare/fact/ontology.py` | `knowledge/ontology.yaml` 로드·조회(정규화 이름 키) |
| `contentcompare/fact/validator.py` | 그래프 검사 추가(§6) |
| `contentcompare/fact/prompts.py` | `CONCEPT_SYSTEM` / `build_concept_user` |
| `contentcompare/fact/fact_matcher.py` | 그래프 조회 경로 추가(유사도 경로는 recall 로 격하) |
| `contentcompare/fact/pipeline.py` | F7 단계 삽입, `concept_graph.json` 저장 |
| `contentcompare/report/fact_report.py` | 연결 근거 표시 + "검토 필요" 섹션 |

기존 파일에 얹기보다 새 모듈로 분리한다 — `fact_matcher.py` 는 이미 검색 전략을 담고
있고, 그래프 조립·검증까지 들어가면 책임이 섞인다.

---

## 9. 오류 처리와 예산

- **LLM 실패/예산 초과** → 그 배치의 쌍은 `unknown`. 비교 단계에서 판단보류로 남기고
  리포트에 표시한다. **버리지 않는다**(§6.2 원칙 유지).
- **임베더 없음** → BM25 recall 폴백. 개념 층은 그대로 동작한다.
- **`ontology.yaml` 없음** → 빈 온톨로지로 시작(정상 경로).
- **문서 처리 실패** → 기존 격리 유지. 그 문서만 비교에서 빠진다.

계측은 `run_stats.json` 에 `concept` 절로 합류한다(쌍 수, 온톨로지 적중, LLM 호출,
거부/강등 건수). **오판 추적이 목적이므로 이 계측을 제거하지 않는다.**

---

## 10. 테스트 계획

전부 가짜 LLM/임베더 주입으로 네트워크·Office 없이 돈다(기존 규약).

**단위**
- `concept_models`: 직렬화 왕복, `from_llm` 의 방어적 파싱.
- `ontology`: 정규화 이름 조회, 이름 순서 무관, 파일 없음.
- 병합: `same_as` 추이 병합, `differs_by` 위반 시 강등, 승격된 `differs_by` 우선.
- 근거 검증: 인용이 원문에 없으면 `same_as` 거부.
- 배치: 예산 초과 시 남은 쌍이 `unknown` 으로 남는지.

**회귀(실측 고정)** — 이 세 쌍은 `same_as` 가 되면 안 된다:
- `1개월저장온도` ↔ `표준환경온도`
- `평가환경온도` ↔ `평가 환경 습도`
- `충전환경온도` ↔ `정격 충전 전압`

그리고 `고객 표준 버전` ↔ `문서 기준 규격` 은 **승격된 온톨로지가 있으면** 연결되어야 한다.

**end-to-end**: `scripts/compare_engines.py` 골든셋 채점.

---

## 11. 완료 기준 (DoD)

1. 골든셋 기준 항목 19개에서 fact 엔진 정확도가 **현행 17/19 이상**.
2. **비교 대상이 아닌 쌍을 `match`/`mismatch` 로 보고하는 건이 0건** — 이것이 F7 의 존재 이유다.
3. `concept_recall_min` 을 0.3~0.6 사이에서 바꿔도 **골든셋 채점 결과가 변하지 않는다**
   — 유사도가 판정에서 빠졌음을 실측으로 확인하는 항목이다(모델 종속성 제거).
4. 재실행 시(승격 후) 개념 단계 LLM 호출이 0~1회.
5. `python -m pytest` 전체 통과.

---

## 12. 해결하지 못하는 것

- **F3 추출 누락**(Word `w_b004` 미추출 등)은 그대로 `missing` 이다.
- **값·단위 판정**(1495 vs 1.495A)은 F5 Comparator / F4b 의 몫이다.
- LLM 이 `same_as` 를 빠뜨려 생기는 `missing` 증가. 리포트의 "검토 필요"로 사람에게
  넘기고, 사람이 승격하면 영구히 해결된다.
