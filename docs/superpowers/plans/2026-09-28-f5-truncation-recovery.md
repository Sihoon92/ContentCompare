# F5 출력 절단 복구 수정 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** F5 판정 출력이 절단되어도 해당 비교만 격리하고, schema 제거 1회 재시도와 원인별 unknown 기록을 거쳐 다음 항목 및 리포트 생성을 계속한다.

**Architecture:** 백엔드가 이미 정규화한 `LengthLimitError`를 `FactComparator`에서 처리한다. 공용 LlmRunner의 호출 예산·파싱·추적을 재사용하고, F5에 한정된 복구와 최종 강등을 분리한다. F5 통계는 기존 비교 산출물과 기준 문서의 run_stats에 같은 값으로 기록한다.

**Tech Stack:** Python, pytest, 기존 LlmRunner/ArtifactStore/timeline. 추가 의존성 없음.

**Spec:** 2026-09-28 사용자 요청 「F5 판정 LLM 절단 대응 - 스키마 제거 1회 재시도 + unknown 강등」. 아래 정책은 요청을 현재 코드에 적용하기 위한 구체화다. 이번 작업은 계획 수립까지이며 구현은 포함하지 않는다.

## 사건 기록과 이번 수정의 판단 근거

사용자가 제공한 사내 실측 요약이다. 시간은 제공된 표기를 그대로 보존한다.

| 실행 시각 | 관찰 |
|---|---|
| 09-28 11:20 | F7 50회 정상 → F5 12번째 호출에서 38.9초 / 16,184토큰 절단 → 실행 전체 중단 |
| 09-28 13:37 | 동일 항목에서 38.9초 / 16,184토큰으로 재현 |
| 09-28 15:59 | 동일 재현 |

**확인된 현상:** 다른 실행 시점에도 특정 F5 입력에서 실패가 재현된다. 일시적인 서버 혼잡을 기다리는 방식보다 입력/요청 조건에 따른 실패로 다루는 것이 타당하다. F7 정상 이력은 전역 schema 비활성화의 근거가 없음을 보여주며, 복구 범위는 F5의 실패한 비교로 제한한다. F7 성공이 F5 스키마의 정상 여부까지 증명하지는 않는다.

**유력한 가설:** 특정 입력과 디코딩 제약의 조합에서 반복 생성이 발생하거나, 해당 입력에 대해 출력 한도까지 생성하는 경로가 반복된다. 제공된 요약만으로 greedy 설정, 응답 원문의 반복 패턴, schema의 인과관계까지 확정하지 않는다. `16,184`가 output_tokens인지 total_tokens인지, reasoning_tokens가 포함되는지도 원본 추적에서 확인한다. 이를 임의로 `max_tokens=16184`라고 해석하지 않는다.

**수정 결정:** 동일 요청 재전송·대기·토큰 한도 확대를 복구책으로 추가하지 않는다. 첫 절단 이후에는 schema라는 요청 조건 하나만 제거해 최대 1회 시도한다. 같은 프롬프트·후보·모델·temperature·max_tokens·추론 설정을 유지해 효과를 구분한다. 재시도 실패가 전체 실행 중단으로 번지는 결함은 복구 성공 여부와 관계없이 해결한다.

**완료 기준의 우선순위:** (1) 문제 항목 이후 비교와 리포트 생성이 계속되는 것, (2) unknown 사유와 호출 비용이 추적 가능한 것, (3) schema 제거로 유효한 판정이 복구되는 것. 세 번째는 실측할 개선 지표이며 모든 입력에서의 성공을 보장하지 않는다.

## 확인한 현재 상태

- `contentcompare/fact/fact_comparator.py::_decide_by_llm()`은 `LlmBudgetExceeded`, `ValueError`만 잡는다. `LengthLimitError`는 `LLMRequestError` 계열이므로 빠져나간다.
- `FactPipeline.run()`의 문서별 격리는 추출 단계에만 있다. 이후 `_compare_from_store()`의 F5 루프에는 절단 격리가 없어 예외가 실행 밖으로 전파된다.
- 기존 `_fallback()`은 단일 후보에 코드 판정이 있으면 match/mismatch로 복귀한다. 따라서 이 함수를 실패 처리에 그대로 쓰면 unknown 요구를 충족하지 못한다.
- `LlmRunner.complete_json()`은 기본적으로 파싱 실패 재시도 1회를 수행한다. 절단은 재시도하지 않고 올리며, 실제 chat 호출 전에 공유 예산을 검사한다.
- `llm_calls`는 현재 성공적으로 JSON을 얻은 비교 수다. 실제 호출 시도 수는 runner의 `calls`에 있다. 이름만 보고 기존 지표의 뜻을 변경하지 않는다.
- F5 통계는 `comparison_result.json.stats`에만 저장된다. 문서별 `run_stats.json`은 F0~F4a 처리 finally에서 먼저 저장된다.
- LangChain의 auto/json_schema 모드에서는 `schema=None`이면 response_format을 보내지 않는다. json_object 모드는 schema가 없어도 JSON 모드가 유지된다. internal 등은 schema를 지원하지 않을 수 있다.
- 사내 실측 3회 요약은 제공되었으며, 전체 오류 로그·잘린 응답 원문은 아직 확인하지 않았다. 코드로 확인한 직접 중단 원인은 예외 처리 누락이다. 반복 생성 및 schema 제거 효과는 위 가설과 검증 기준을 따른다.

## 공통 제약

- F5의 코드 우선 판정, F7 개념 연결 및 F2/F3/F7 배치 분할 정책을 유지한다.
- 절단 뒤 추가 생성 호출은 최대 1회이며 기존 `max_llm_calls_per_compare` 예산 안에서만 실행한다.
- 클라이언트 전역 구조화 출력 설정을 끄지 않는다. 다음 비교는 다시 정상 schema를 사용한다.
- Office·네트워크·실제 LLM 없이 FakeChat으로 회귀 테스트한다. SDK 예외를 F5에 직접 import하지 않는다.
- 절단 응답을 억지로 JSON 복구해 판정에 쓰지 않는다. 기존 원문 추적·토큰 계측을 보존하고 타임라인에는 원문을 넣지 않는다.
- 현재 미커밋 변경(`CLAUDE.md`, `concept_builder.py`, `llm_stage.py`, `test_concept_builder_llm.py`)을 보존한다. 공용 runner 수정은 필요하지 않다.

## 동작 정책

| 상황 | 처리 | 최종 실패 코드 |
|---|---|---|
| 최초 정상 응답 | 기존 파싱·인용 검증·판정 적용 | 없음 |
| 최초 예산 고갈 | 즉시 unknown | `budget_exceeded` |
| 최초 JSON 파싱 최종 실패 | 기존 파싱 재시도 소진 후 unknown | `parse_failure` |
| schema가 적용된 호출 절단 | 같은 사실·후보·프롬프트로 `schema=None, retries=0` 재호출 | 아직 실패 집계 안 함 |
| 재호출 성공 | 기존 후처리로 판정, 복구 성공 기록 | 없음 |
| 재호출 전 예산 고갈 | 추가 생성 없이 unknown | `budget_exceeded` |
| 재호출 JSON 파싱 실패 | 추가 파싱 재시도 없이 unknown | `parse_failure` |
| 재호출도 절단 | 추가 호출 없이 unknown | `output_truncated` |
| 최초 호출에 적용된 schema가 없음 | 같은 요청을 반복하지 않고 unknown | `output_truncated` |

마지막 행은 변형 재시도 원칙에 따른 제안이다. **이전 계획의 structured_calls 증가만으로 판단하는 방법은 보완한다.** 그 카운터는 json_object 모드에서도 증가할 수 있어 실제 요청 변화를 보장하지 못한다.

재시도 자격은 다음 세 조건이 모두 충족될 때만 성립한다.

1. 첫 호출에 넘긴 `compare_schema = schema_for("compare")`가 비어 있지 않다.
2. `runner.structured_calls`가 첫 complete_json 호출 전보다 증가했다.
3. 절단 시점의 `runner.chat.supports_schema_removal_retry`가 True다.

`LangChainBackend.supports_schema_removal_retry: bool` 읽기 전용 프로퍼티를 추가한다. 반환식은 `self.supports_structured_output and self._mode in ("auto", "json_schema")`다. 래퍼는 기존 `__getattr__` 위임을 사용하고, F5는 `getattr(..., False)`로 읽어 백엔드 내부 설정에 의존하지 않는다. schema 미지원 백엔드에는 속성을 추가할 필요가 없다. json_object/off, pydantic 미설치, 호출 중 서버 거절로 구조화 출력이 꺼진 경우는 재시도하지 않는다. 호출별 모드 전체 비활성화 API는 추가하지 않는다.

재호출은 `runner.complete_json(COMPARE_SYSTEM, user, schema=None, retries=0)`이며, 직접 chat을 불러 예산·추적·요청 제한 래퍼를 우회하지 않는다. 절단 재시도 루프는 만들지 않고 첫 호출과 재호출을 중첩된 두 구간으로 분리한다. `LengthLimitError`, `LlmBudgetExceeded`, `ValueError`만 해당 구간에서 분류한다.

프롬프트 동일성은 최초 요청의 system/user 기준이다. 최초 JSON 파싱 교정 도중 절단된 경우에는 runner가 덧붙였던 파싱 교정 문구를 복제하지 않고 원래 user로 돌아간다. 이 경로의 복구를 schema 제거만의 효과로 해석하지 않으며, `runner.retries`와 원문 추적으로 구분한다.

**호출 상한의 의미:** 최초 호출의 기존 JSON 파싱 재시도 1회는 유지한다. 따라서 최초 응답부터 절단이면 최대 2회 생성, 최초 파싱 실패 → 교정 응답 절단이면 최대 3회 생성이다. 어느 경우에도 첫 절단 이후 추가 생성은 최대 1회다. 여기서 생성 횟수는 LlmRunner의 chat.complete 진입 수이며, 기존 전송 계층의 연결/429 재시도 정책과 구분한다. 절단 예외가 전송 계층의 대기 재시도로 분류되지 않는 기존 테스트를 유지한다.

최종 실패 원인은 마지막 실패로 분류한다. 예를 들어 절단 후 재시도 예산이 없으면 `budget_exceeded`이며, 최초 절단은 별도 절단 횟수에 남는다. 세 가지 운영 실패에서는 후보 수·기존 코드 판정과 관계없이 unknown으로 확정한다. LLM 비활성 상태의 기존 `_fallback()` 정책 및 LLM이 정상 응답으로 내린 unknown은 이 세 가지 실패 집계에서 제외한다. 인증 오류·연결 실패·프로그래밍 오류 등 무관한 예외를 포괄적으로 삼키지 않는다.

## 계측 계약

- `FactComparison.failure_reason: str = ""`을 추가하고 `to_dict()`에 포함한다. 값은 위 실패 코드 세 가지 또는 빈 문자열이다. `reason`은 사람이 읽는 한국어 사유이며, 예외 전체 문자열·원문을 복사하지 않는다.
- 강등은 코드가 수행하므로 `decided_by="code"`를 유지하고, 원래 코드 결과는 기존 `initial_result`에 보존한다. 실패 결과의 mismatch_attributes/findings는 비우고 제시된 후보와 근거 위치는 검수용으로 보존한다.
- `FactComparator.stats() -> dict[str, int]`에 기존 `llm_calls`, `llm_failures`, `llm_budget_exceeded`, `dropped_findings`, `quote_unverified`와 신규 `llm_parse_failures`, `llm_truncations`, `llm_schema_retries`, `llm_schema_recoveries`, `llm_output_truncated`를 반환한다.
- `llm_failures = llm_budget_exceeded + llm_parse_failures + llm_output_truncated`: 최종 실패 비교 건수다. `llm_truncations`는 복구 성공분을 포함한 절단 예외 횟수이므로 다른 합계다. `llm_schema_retries`는 실제 추가 chat 호출이 시작된 경우만 센다. runner.calls 전후 차이로 예산 고갈 전 호출 여부를 구분한다.
- `compare_stats.llm = runner.stats()`를 추가한다. 기존 llm_calls와 runner.calls를 혼동하지 않도록 문서에 명시한다. runner가 없으면 calls/retries/parse_failures/structured_calls는 모두 0이다.
- 세 가지 최종 실패는 공통 `_mark_unknown(out, candidates, *, failure_reason, retry_attempted) -> FactComparison`에서 결과 설정·카운터·`log_print(..., level=WARNING)`·`timeline.NOTE`를 한 번씩 처리한다.
- 공통 이벤트 필드: `action="unknown"`, `failure_reason`, `reference_fact_id`, `target_doc`, `candidate_ids`, `retry_attempted`, `reason`. 타임라인 name은 `current_stage()`로 F5 단계명을 유지하고 status는 `unknown`으로 둔다.
- 재시도·복구·강등 이벤트 모두 동일한 기준 fact/대상 문서/후보 식별자를 기록한다. 실행의 '12번째 호출'은 파싱 재시도·코드 판정 생략에 따라 바뀌므로 항목 식별자로 사용하지 않는다. 세 이벤트에 `runner_calls`도 남겨 F5 실제 호출 번호와 연결한다.
- 재시도는 `timeline.RETRY(status="length", action="retry_without_schema", attempt=1, max=1)`와 로그로 남긴다. 복구 성공은 `timeline.NOTE(action="recovered_without_schema")`로 기록한다. 예산 때문에 실제 호출이 없으면 재시도 이벤트를 남기지 않는다.
- 예산이 이미 소진되었으면 재시도 이벤트를 먼저 내지 않는다. 실행은 순차적이므로 `runner.calls < runner.max_calls`를 확인해 시작 이벤트를 기록하고, 실제 호출의 예산 최종 집행은 runner에 맡긴다. 카운터는 호출 전후 차이로 검증한다.
- 강등 문구를 고정한다: 예산은 `LLM 판정 호출 예산이 소진되어 unknown으로 보류합니다.`, 파싱은 `LLM 판정 응답을 JSON으로 해석하지 못해 unknown으로 보류합니다.`, 재절단은 `schema 제거 재시도에서도 출력이 절단되어 unknown으로 보류합니다.`. 재시도 자격이 없으면 `출력이 절단되었으며 제거할 JSON Schema 제약이 없어 재호출 없이 unknown으로 보류합니다.`로 기록한다. 절단 뒤 예산/파싱 실패인 경우 해당 문구 앞에 `출력 절단 후 schema 제거 복구 중 실패했습니다.`를 붙여 최초 사건도 드러낸다.
- 기존 `comparison_result.json.stats`와 기준 문서 `run_stats.json.comparison`에 동일한 compare_stats를 기록한다. `run_stats`의 기존 llm/records/facts/validation/stages 등은 보존한다. 비교 전체의 예산이므로 대상 문서별 추출 통계에 중복 합산하지 않는다.
- run_stats가 없으면 comparison 섹션만 생성한다. 읽기 실패·손상 JSON·쓰기 실패 시 기존 파일을 덮어 지우지 않고 경고 후 리포트 생성을 계속한다. `save_artifacts=false`면 읽기·쓰기를 생략한다. 재비교 시 comparison 섹션은 누적하지 않고 이번 값으로 교체한다.

## 검토할 경계 조건

1. 첫 절단이 마지막 예산을 소비한 경우: 추가 호출 0회, 최종 budget_exceeded 1건.
2. 단일 후보의 기존 코드 match 및 Acceptance Gate 강제 검토: 복구 실패 시 match로 복귀하지 않음.
3. 스키마 미지원·off·pydantic 미설치·서버 거절: 동일 요청을 변형 재시도라고 기록하지 않음.
4. 절단 후 잘못된 JSON: 공용 파싱 재시도로 세 번째 생성이 발생하지 않음.
5. 이전 run_stats 보존·저장 실패·캐시 재실행: 기존 추출 통계가 유실되거나 실패 집계가 누적되지 않음.

## 작업 1: F5 재시도와 최종 unknown 처리

**수정:** `contentcompare/fact/fact_comparator.py`, `contentcompare/llm/langchain_backend.py`, `contentcompare/llm/base.py`(능력 플래그 계약 문서)
**신규 테스트:** `tests/test_fact_comparator_recovery.py`
**수정/회귀 테스트:** `tests/test_fact_comparator.py`, `tests/test_fact_gate_pipeline.py`, `tests/test_llm_structured_wiring.py`
**인터페이스:** 기존 compare/finalize 시그니처 유지. 위 failure_reason, stats(), _mark_unknown() 및 백엔드 supports_schema_removal_retry 계약 추가. `_decide_by_llm()`에서 첫 호출과 한 번의 schema 제거 재호출을 분리하며 정상 응답 후처리는 공유한다.

- [ ] 스키마 지원 FakeChat에 응답/예외 시퀀스를 주입하는 테스트를 작성한다. `length → valid`는 schema 전달 순서 `[compare_schema, None]`, calls=2, 실패=0, 복구=1을 단언한다. 테스트 schema는 monkeypatch하여 pydantic 설치 여부에 의존하지 않는다.
- [ ] `length → length`는 unknown·calls=2·절단=2·최종 출력절단=1, `length → invalid JSON`은 unknown·calls=2·파싱 실패=1을 단언한다. `max_calls=1`에서는 length 뒤 calls=1·재시도=0·예산 고갈=1을 단언한다.
- [ ] 최초 파싱 실패/예산 고갈, 단일·복수 후보, 기존 코드 match/mismatch가 있던 검토 경로의 실패를 시험한다. 실패 시 모두 unknown이며 다음 비교는 실행된다. LLM 비활성 폴백과 무관한 RuntimeError 전파는 기존대로다.
- [ ] schema 미지원/off/서버 거절 뒤 비활성화된 경우 불필요한 재호출이 없고, 복구 성공 뒤 다음 항목에는 다시 schema가 전달되는지 검증한다.
- [ ] `test_schema_removal_retry_requires_request_change`: auto/json_schema에서만 capability=True이고 json_object/off/서버 거절 뒤에는 False인지, TracedChat/RateLimitedChat을 통해서도 값이 위임되는지 검증한다. 기존 supports_structured_output 값의 의미는 바꾸지 않는다.
- [ ] 가짜 LangChain chat의 bind 인자를 검사한다. 재시도에서 `response_format` 키 자체가 없고, 프롬프트·temperature·max_tokens·extra_body는 최초 요청과 동일하며, 다음 항목에는 JSON Schema가 복원되는지 검증한다. json_object는 불필요한 재시도 0회다.
- [ ] `test_parse_then_length_has_only_one_schema_retry`: 응답을 invalid JSON → LengthLimitError → valid로 주고 calls=3, parse retries=1, schema retries=1을 단언한다. 재호출에서도 invalid JSON이면 calls=3에서 종료한다.
- [ ] `pytest tests/test_fact_comparator_recovery.py -q`로 현재 실패를 확인하고 위 정책을 구현한다.
- [ ] 같은 테스트와 comparator/gate 회귀 테스트를 실행하여 통과를 확인한다.

## 작업 2: 로그·타임라인·run_stats 연결

**수정:** `contentcompare/fact/fact_comparator.py`, `contentcompare/fact/pipeline.py`
**신규 테스트:** `tests/test_fact_recovery_observability.py`
**회귀 테스트:** `tests/test_fact_pipeline_smoke.py`, `tests/test_fact_report.py`, `tests/test_timeline_wiring.py`
**인터페이스:** 작업 1의 stats()/failure_reason을 사용한다. `FactPipeline._save_comparison_stats(self, ref_doc: DocFacts, stats: dict) -> None`을 추가해 run_stats.comparison 병합과 실패 격리를 맡긴다. `_compare_from_store()`의 공통 종료 경로에서 호출한다.

- [ ] 세 가지 실패를 parameterize하여 로그·타임라인·직렬화 결과의 failure_reason 및 식별자가 일치하고 강등 이벤트가 비교당 1회인지 검증한다. 원문이 타임라인에 포함되지 않는지도 확인한다.
- [ ] 절단 후 복구 성공은 실패 합계가 0, 재시도·복구 이벤트가 각 1회임을 검증한다. 세 원인 합계와 llm_failures가 일치하는지 확인한다.
- [ ] FakePipeline에서 첫 비교의 연속 절단 뒤 두 번째 비교가 정상 완료되고, markdown과 comparison_result가 생성되며 두 산출물의 F5 통계가 일치하는지 검증한다.
- [ ] `test_twelfth_f5_call_truncation_does_not_abort_run`: 모든 항목이 LLM 검토를 요구하는 13개 비교를 구성한다. 앞 11회 정상, 12번째 절단, 13번째 schema 제거 호출도 절단, 14번째 호출에서 다음 비교 정상으로 구성한다. 결과 13건·해당 항목 unknown 1건·최종 실패 1건·절단 2회·재시도 1회·리포트 생성과 같은 fact 식별자를 단언한다. 호출 지연은 시뮬레이션하며 38.9초를 실제로 기다리지 않는다.
- [ ] 같은 시나리오의 재시도 성공형은 runner.calls=14, llm_failures=0, llm_schema_recoveries=1을 단언한다. 비교 예산이 12이면 runner.calls=12에서 멈추고 문제 항목 및 이후 LLM 검토 항목이 각각 budget_exceeded로 남되, 전체 비교 결과와 리포트는 생성되는지 검증한다.
- [ ] 기존 추출 통계 보존, run_stats 없음/손상/쓰기 오류, save_artifacts=false, 두 번 비교 시 새 값으로 교체되는 경우를 시험한다.
- [ ] `pytest tests/test_fact_recovery_observability.py -q`의 실패를 확인하고 공통 이벤트 및 저장 연결을 구현한다.
- [ ] 신규 테스트와 pipeline/report/timeline 회귀 테스트를 실행하여 통과를 확인한다.

## 작업 3: 문서 및 최종 검증

**수정:** `docs/FACT_PIPELINE_PLAN.md`, `docs/USER_GUIDE.md`

- [ ] schema 제거 복구 정책, schema가 없을 때의 예외 규칙, 실패 코드·통계 위치·호출 예산 의미를 한국어로 설명한다. 토큰 한도 증설이나 temperature 변경을 기본 해결책으로 추가하지 않는다.
- [ ] 다음 집중 회귀 검사를 실행한다: `pytest tests/test_fact_comparator_recovery.py tests/test_fact_recovery_observability.py tests/test_fact_comparator.py tests/test_fact_gate_pipeline.py tests/test_fact_pipeline_smoke.py tests/test_fact_pipeline_concept.py tests/test_fact_report.py tests/test_fact_llm_stage.py tests/test_llm_truncation.py tests/test_llm_structured_wiring.py tests/test_timeline_wiring.py -q`.
- [ ] 전체 `pytest -q`를 1회 실행한다. 기존 미커밋 변경으로 인한 실패가 있으면 이번 변경의 실패와 구분해 보고한다.
- [ ] 아래 사내 검증 절차를 수행한다. 사내 접근이 없는 환경에서는 이 실측을 미검증으로 남긴다.

## 사내 검증 절차와 수용 기준

1. 기존 세 실행의 문제 호출에서 기준 fact ID, 대상 문서, 후보 ID 목록, 실제 system/user, 모델·요청 설정, finish_reason, input/output/reasoning 토큰, 접힌 응답 원문을 확인한다. 출력의 같은 문구·findings 반복인지, 정상 항목이 길어진 것인지 구분한다. 원문 로그와 설정값이 없는 항목은 미확인으로 남긴다.
2. 기존에 동일 요청으로 3회 재현했으므로, 수정 전 전체 실행을 네 번째로 반복하는 것을 선행 조건으로 삼지 않는다. 기존 artifacts를 사용할 수 있으면 같은 fact·후보를 고정해 수정된 F5 경로에서 문제 항목을 검증한다. 이때 matcher의 후보 순서·도메인 지식까지 동일하게 유지한다.
3. 적용된 첫 요청이 JSON Schema를 실었다면 재시도 요청에서는 response_format 키가 제거되었는지 확인한다. 모델·토큰 상한·프롬프트는 동시에 바꾸지 않는다. 제거만으로 성공하면 schema 제약 변화가 해당 사례 복구에 유효했다는 증거이며, 모델 내부의 반복 원인까지 증명한 것은 아니다.
4. 성공 시 기존 findings의 후보 ID·인용 검증을 그대로 거치는지 확인한다. 재시도도 절단되거나 JSON이 깨지면 unknown과 정확한 최종 실패 코드가 남는지 확인한다. 복구 성공은 '응답이 정상 처리됨'이며 판정의 사실적 정확성은 근거를 따로 검수한다.
5. 이후 항목이 처리되고 리포트까지 생성되는지 한 번의 전체 실행으로 확인한다. F7 및 다른 F5 호출의 구조화 출력은 유지되어야 한다. 호출 순번이 달라져도 fact/후보 식별자로 같은 사건을 찾는다.
6. 로그·타임라인·comparison_result·run_stats를 대조한다. 원인이 세 가지 중 하나로 일치하며, 실패 총계는 최종 강등 건수와 같아야 한다. 복구된 절단은 llm_truncations에만 남고 llm_failures에 포함되지 않아야 한다.

같은 속도로 재절단되는 경우 문제 비교는 관측상 약 77.8초(38.9초 × 2)의 생성 시간이 들 수 있다. 이는 시간 상한이 아니며 전송·요청 제한 대기는 별도다. 이번 수정의 보장은 '첫 절단 후 생성 시도 최대 1회'와 '세 가지 실패의 항목별 격리'다. 회복률이 낮으면 후속 변경으로 출력 길이/반복 억제 프롬프트 등을 별도 검토하고, 이번 검증에 섞지 않는다.

## 계획 자체 검토

요청의 복구·격리·세 원인 계측을 각각 작업 1~2에 연결했다. 3회 재현을 사건 근거로 추가하고, 실제 요청 변화 확인, F5 12번째 호출 회귀 시나리오, 실패 원인별 수용 기준을 구체화했다. 핵심 결정은 실패 시 기존 코드 판정 복귀 금지, 제거할 schema가 없을 때 재시도 생략, 마지막 실패 기준 분류, run_stats의 comparison 섹션이다. 구현 없이 코드 경로를 조사한 계획이며, 사내 반복 루프의 내부 원인 및 schema 제거의 실제 복구율은 아직 검증하지 않았다.
