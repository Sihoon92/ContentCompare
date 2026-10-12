# 이해 문서 (understanding)

이 폴더는 **무엇이 왜 그렇게 됐는가**를 남기는 곳이다.
`docs/FACT_*_DESIGN.md` 가 "무엇을 만들 것인가"(설계)라면, 여기는 그 결과 실제로
무슨 일이 벌어졌고 어디서 어긋났는지를 사후에 설명한다.

> 📖 **[`index.html`](index.html) 을 브라우저로 여세요.** 전체 흐름을 한 편으로 읽고
> 각 대목에서 심화 문서로 내려가도록 짜여 있습니다. 아래는 GitHub 용 색인입니다
> (HTML 은 GitHub 웹에서 렌더링되지 않습니다).

## 계층

### 0. 입문 — 전체를 한 번에

이 시스템이 무엇을 하고 어떻게 굴러가는지 한 편으로 훑는 글들.

- [LLM 은 무엇을 보고 판단할까](2026-08-06-explanation-what-the-llm-sees.html)  
  밑바닥부터 시작하는 fact 엔진 전 구간 개괄. 처음 읽기에 가장 좋다. <sub>개괄 · 2026-08-06</sub>
- [ContentCompare 는 문서를 어떻게 비교하는가](2026-08-03-explanation-how-contentcompare-compares-documents.html)  
  RAG 경로와 fact 경로를 코드 단위로 끝까지 따라가는 마스터 개괄. 가장 길고 가장 넓다. <sub>개괄 · 2026-08-03</sub>
- [엑셀과 워드는 같은 말을 하고 있을까?](2026-08-02-explain-fact-pipeline.html) ⚠️ *초기판*  
  왜 RAG 를 버리고 fact 방식으로 가려 하는가 — 비유 중심의 첫 설명. <sub>개괄 · 2026-08-02</sub>

### 1. F0 읽기·경계 — 문서 → 블록

문서를 해석 없이 뜯어내고 덩어리로 자르는 자리. 여기서 잃은 것은 뒤에서 복구되지 않는다.

- [사라진 두 줄 — 워드의 Enter 가 fact 를 지우는 자리](2026-08-14-explanation-word-block-boundary.html)  
  문단이 나뉘었다는 이유로 fact 2건이 통째로 사라진 사건과 그 수정. <sub>사건 · 2026-08-14</sub>
- [한 문단, 네 개의 조건](2026-08-10-explanation-charge-temperature-merge.html) 🔧 *정정판*  
  Word 줄바꿈이 네 구간을 한 덩어리로 삼키는 자리 — raw/compact 경계 추적. <sub>사건 · 2026-08-10</sub>

### 2. F1~F3 추출 — 블록 → fact

덩어리를 비교 가능한 주장(fact)으로 바꾼다. LLM 이 가장 많이 개입하는 구간.

- [fact 엔진의 LLM 사용 지점](2026-08-05-explanation-fact-llm-io-walkthrough.html)  
  LLM 이 호출되는 여섯 자리에 각각 무엇이 들어가고 무엇이 나오는가. <sub>심화 · 2026-08-05</sub>

### 3. F7 개념 그래프 — 짝 찾기

"이 둘을 비교해도 되는가"만 답한다. 값이 같은지는 다음 단계의 몫이다.

- [온톨로지 개념 그래프](2026-08-05-explanation-ontology-concept-graph.html)  
  유사도로는 '다르다'를 말할 수 없다 — F7 설계 전체. 이 계층의 정본. <sub>설계 · 2026-08-05</sub>
- [바구니에 담기와 금지표](2026-08-06-explanation-concept-merge-and-veto.html)  
  맞는 판정이 `rejected_by: differs_by` 로 거부된 이유 — 병합 순서와 veto. <sub>사건 · 2026-08-06</sub>
- [증거가 주장을 뒷받침하지 못할 때](2026-08-06-explanation-evidence-gate-redesign.html)  
  근거 검문소가 '이름이 같다'를 값으로 증명시키는 구조적 오류와 3단계 해법. <sub>설계 · 2026-08-06</sub>
- [언어가 다르면 왜 비교가 무너지는가](2026-08-06-explanation-cross-language-recall-bottleneck.html)  
  교차언어 recall 경로 해부 — 반전은 임베딩이 범인이 아니었다는 것. <sub>심화 · 2026-08-06</sub>
- [검색 문자열은 원문이 아니다](2026-08-18-explanation-search-text-embedding.html)  
  재조립된 search_text 가 숫자를 잃는 자리, 그리고 그것을 재다 뒤집힌 세 가지. <sub>사건 · 2026-08-18</sub>
- [영어로 쓴 문서는 왜 비교가 안 됐을까](2026-08-05-explanation-english-document-fix.html)  
  영어 대상 문서에서 매칭이 0이 된 사건 — 범인은 언어가 아니라 검문소였다. <sub>사건 · 2026-08-05</sub>

### 4. F5 값 대조 — 판정

코드가 확실한 것만 단정하고 애매하면 LLM 에 넘긴다. 최종 판정이 여기서 나온다.

- [⚪ 대상에 없음 은 어떻게 정해지는가](2026-08-14-explanation-missing-and-same-as.html)  
  `same_as` 에서 `findings` 까지 판정 경로 전체. 이 계층의 정본. <sub>심화 · 2026-08-14</sub>
- [Acceptance Gate — 코드의 '일치' 판정을 믿어도 되는가](2026-08-10-explanation-fact-acceptance-gate.html)  
  조용한 오판을 막는 라우팅 게이트 7규칙과, 왜 기본값이 shadow 인가. <sub>설계 · 2026-08-10</sub>
- [후보가 넷일 때 — 1:N Fact 비교 문제와 해법](2026-08-13-explanation-multi-candidate-1n-comparison.html) ✅ *구현됨*  
  `candidates[0]` 축약이 만든 임의 선택을 1:N 종합 판정으로 바꾼 설계. <sub>설계 · 2026-08-13</sub>

### 5. 리포트·RAG 엔진 — 결과와 다른 경로

판정을 사람이 읽을 형태로 내는 자리, 그리고 별도로 존재하는 RAG 엔진.

- [판정 단계의 해부](2026-08-07-explanation-rag-verdict-stage.html)  
  `--engine rag` 의 판정 LLM 호출 — verdict·findings 네 필드는 어디서 오는가. <sub>심화 · 2026-08-07</sub>

### 6. 운영·진단 — 산출물과 숫자

실행이 남기는 파일과 계측을 읽는 법. 오판을 추적하려면 여기부터 본다.

- [fact 파이프라인 해부 — 생성되는 파일들](2026-08-05-explanation-fact-pipeline-artifacts.html)  
  실행이 남기는 JSON 산출물 지도, 그리고 '왜 LLM 위임이 2건뿐인가'. <sub>심화 · 2026-08-05</sub>
- [왜 AI는 ‘틀렸는지’ 말할 수 있어야 하는가](2026-08-17-explanation-why-missing-explainability.html)  
  why_missing.py 를 판단 블랙박스 판독기·교정 라우터로 읽는 설명 가능성 전략. <sub>심화 · 2026-08-17</sub>
- [실행 통계 읽는 법](2026-08-13-explanation-run-stats-anatomy.html)  
  run stats 의 스무 개 숫자가 각각 무엇을 분모로 무엇을 세는가. <sub>심화 · 2026-08-13</sub>
- [missing 76건은 정상인가](2026-08-13-explanation-run-stats-anatomy-followup.html)  
  위 글 독자의 질문 셋에 실측으로 답하는 후속편. <sub>심화 · 2026-08-13</sub>

### 7. 전략 — 앞으로

지금 구조를 어디로 끌고 갈 것인가.

- [이 파이프라인을 AI 에이전트로 만들 수 있는가](2026-08-14-explanation-pipeline-to-agent.html)  
  루프 · 그래프 엔지니어링 · 권한 경계, 그리고 무엇이 중요한 역량인가. <sub>전략 · 2026-08-14</sub>

## 읽기 경로

**처음 왔어요** — 이 시스템이 뭘 하는 물건인지부터. 60~90분.

1. [LLM 은 무엇을 보고 판단할까](2026-08-06-explanation-what-the-llm-sees.html)
2. [ContentCompare 는 문서를 어떻게 비교하는가](2026-08-03-explanation-how-contentcompare-compares-documents.html)
3. [온톨로지 개념 그래프](2026-08-05-explanation-ontology-concept-graph.html)
4. [⚪ 대상에 없음 은 어떻게 정해지는가](2026-08-14-explanation-missing-and-same-as.html)

**판정이 이상해요 (디버깅)** — 숫자를 먼저 읽고, 그 다음 원인 갈래로 내려간다.

1. [실행 통계 읽는 법](2026-08-13-explanation-run-stats-anatomy.html)
2. [missing 76건은 정상인가](2026-08-13-explanation-run-stats-anatomy-followup.html)
3. [⚪ 대상에 없음 은 어떻게 정해지는가](2026-08-14-explanation-missing-and-same-as.html)
4. [왜 AI는 ‘틀렸는지’ 말할 수 있어야 하는가](2026-08-17-explanation-why-missing-explainability.html)
5. [fact 파이프라인 해부 — 생성되는 파일들](2026-08-05-explanation-fact-pipeline-artifacts.html)

**왜 이렇게 설계했나** — 결정과 그 근거를 시간 순으로.

1. [온톨로지 개념 그래프](2026-08-05-explanation-ontology-concept-graph.html)
2. [증거가 주장을 뒷받침하지 못할 때](2026-08-06-explanation-evidence-gate-redesign.html)
3. [Acceptance Gate — 코드의 '일치' 판정을 믿어도 되는가](2026-08-10-explanation-fact-acceptance-gate.html)
4. [후보가 넷일 때 — 1:N Fact 비교 문제와 해법](2026-08-13-explanation-multi-candidate-1n-comparison.html)
5. [이 파이프라인을 AI 에이전트로 만들 수 있는가](2026-08-14-explanation-pipeline-to-agent.html)

## 문서를 추가하려면

`scripts/understanding_index.py` 의 `CATALOG` 에 한 줄 추가하고 실행한다.

```bash
python scripts/understanding_index.py
```

`index.html` · 이 `README.md` · 각 문서의 상하단 네비가 **함께** 갱신된다.
폴더의 HTML 과 `CATALOG` 가 어긋나면 생성 전에 중단하므로, 문서만 추가하고
색인을 잊는 드리프트가 구조적으로 불가능하다.
