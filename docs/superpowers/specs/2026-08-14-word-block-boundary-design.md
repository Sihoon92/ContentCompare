# Word 블록·셀 경계 재구성 — 경계는 손버릇이고, fact 는 뭉쳐 담는다

- 작성일: 2026-08-14
- 상태: **설계 합의 완료 — 구현 전**
- 관련 문서: `docs/FACT_LINKED_GRAPH_RAG_DESIGN.md` §2, `docs/FACT_PIPELINE_PLAN.md`,
  `docs/understanding/2026-08-14-explanation-word-block-boundary.html`

## 0. 개정 이력 — 초안에서 뒤집힌 것

초안(같은 날 오전)은 **"연속행을 독립된 fact 로 쪼개라"** 였다. 표 데이터를 확인한 뒤
**"하나의 fact 로 묶되 조건별로 속성을 나눠 담아라"** 로 뒤집었다. 지우지 않고 남기는
이유는, 왜 그렇게 예측했는지가 사라지면 같은 착각을 반복하기 때문이다.

| | 초안 | 개정 |
|---|---|---|
| F3 목표 | 조건마다 **fact 1건** | 조건이 여럿이어도 **fact 1건**, 조건별 **속성** |
| 표 처리 | 행을 펼쳐 여러 논리 행으로 | 펼치지 않고 **셀 안 줄만 보여준다** |
| 프롬프트 방향 | 병합 + **분해** 허용 | **병합 유지**, 분해는 넣지 않음 |

뒤집은 근거는 §1.4 다 — 쪼개면 이 파이프라인이 이미 아파하는 세 곳(top_k 경쟁·동명
fact·개념 노드 과병합)을 동시에 누른다. 그리고 쪼개도 코드 대조는 어차피 안 된다.

## 1. 문제

Word 문서에서 **한 항목이 조건별로 여러 줄에 걸쳐 적히면 값이 사라지거나 뭉개진다.**
두 가지 형태로 나타나며, 원인은 같다.

### 1.1 형태 A — 문단이 갈라져 주어를 잃는다

원문:

```
12~15도씨, 0.7C(4.55V)
15~45도씨, 1.2C (4.20V)
           1.1C(4.28V)        ← "15~45도씨" 를 생략한 연속행
           0.8C (4.55V)
```

`physical_raw.json`:

| block | text |
|---|---|
| `w_b245` | `12~15도씨, 0.7C(4.55V) 15~45도씨, 1.2C (4.20V)` |
| `w_b246` | `1.1C(4.28V) 0.8C (4.55V)` |

`w_b246` 에서 fact 가 나오지 않는다. **나올 수 없는 것이 맞다** — 그 블록에는 주어가 없고
프롬프트는 *"입력에 실제로 있는 문구만"*, *"지어내기 금지"* 를 요구한다.

### 1.2 형태 B — 표 셀 하나에 조건표가 통째로 들어간다

`physical_raw.json` 의 표 블록 `w_b289`(1행 × 5열):

| 2.3.23 | Operating Protocol… | Operating Protocol… | Cell charge protocol by different temperature range | `-5~5도씨, 0.1C(4.55V), 5~12도씨, 0.3C(4.55V) 12~15도씨, 0.8C(4.55V) …` |
|---|---|---|---|---|

여기서 나온 fact 는 **한 건**이고 속성도 **한 개**다.

```
fact-word-79  Operating Protocol in different Temperatures
  attributes: { "temp_range_standard_cycle": ... }   ← 구체적 수치 없음
```

**이쪽이 형태 A 보다 위험하다.** 현재 진단이 전부 통과시키기 때문이다.

| 검사 | 결과 | 왜 못 잡나 |
|---|---|---|
| `_check_attributes` | 통과 | 속성이 **0개일 때만** 경고. 1개면 정상으로 본다 |
| `_check_units` | 통과 | *"숫자 값인데 단위가 비었나"* 를 본다. 값이 문자열이면 `_as_number()` 가 `None` → 검사 자체가 안 걸린다 |
| `_check_evidence` | 통과 | 인용이 원문에 실재하는지만 본다. 다섯 구간을 통째로 인용했으니 당연히 통과 |
| 줄 커버리지(Phase 4a) | — | **표는 분모에서 제외**된다. 근거가 *"표는 블록이 이미 최소 단위"* 인데 이 데이터가 그 가정을 반박한다 |
| `facts_by_block` | `cited: true` | 블록이 근거로 쓰였다고 보고한다 — **문제없어 보인다** |

**모든 계기판이 초록인데 다섯 조건 중 넷의 수치가 없다.**

### 1.3 근본 원인 — 경계는 의미가 아니라 손버릇의 기록이다

Word 작성자는 Enter·Shift+Enter·표를 **자기 편한 대로** 쓴다. 파서는 `<w:body>` 직계
자식만 훑어 `<w:p>` 하나 = 블록 하나로 옮기므로(`raw/word_raw.py:190-195`), 블록 경계는
곧 손버릇의 기록이 된다.

그리고 **판단 근거를 우리 코드가 먼저 지운다.** 두 경로 모두 같은 패턴이다.

```python
# 문단 — word_raw.py:86 · :129
text = " ".join((p.text or "").split())   # 줄바꿈·들여쓰기 → 공백
raw  = piece.strip()                      # 줄 목록에서도 양끝 공백 제거

# 표 — word_raw.py:380-387 (_cell_text) · :103 (build_word_doc)
return " ".join(" ".join(parts).split())  # 셀 안 줄바꿈 → 공백
rows = [[" ".join((c or "").split()) for c in row] for row in p.rows]   # 한 번 더
```

`.split()` 이 공백과 줄바꿈을 구분하지 않는 것이 공통 원인이다. 실측으로 확인했다 —
`ParaProbe.text` 단계까지는 선행 공백 23칸과 탭이 **그대로 살아 있다.** 정보는 파일에서
나올 때 존재했고, 우리 코드가 버린다.

또 하나. F3 프롬프트가 표를 **파이썬 리스트 repr 한 줄**로 렌더한다(`_render_unit`).

```
[w_b289] 표 [['2.3.23', 'Operating Protocol…', 'Operating Protocol…',
              'Cell charge protocol by different temperature range',
              '-5~5도씨, 0.1C(4.55V), 5~12도씨, …']]
                      ↑ 이름처럼 보이는 칸      ↑ 값처럼 보이는 칸 (하나)
```

전형적인 **이름–값 쌍**의 모양이다. LLM 이 속성을 하나 만든 것은 이 렌더가 유도한
결과다.

### 1.4 기각한 접근 두 가지

**① 코드가 연속행을 판별하기(문단).** "선행 공백이 있으면 앞 줄의 레이블을 상속한다"는
규칙은 작성자 습관을 예측하는 시도다. 들여쓰기의 의미는 문서마다 다르고(각주·인용·목록),
오탐은 **없는 내용을 만들어내는** 가장 위험한 실패가 된다. 표의 `vMerge` 처리가 안전한
이유는 Word 가 `<w:vMerge/>` 로 **명시**해 주기 때문이지 우리가 잘 추측해서가 아니다.

**② 조건마다 fact 로 쪼개기(초안 방향).** 쪼개면 이 파이프라인이 이미 아파하는 세 곳을
동시에 누른다.

| | 대상 fact 1건 | 대상 fact 5건 |
|---|---|---|
| `concept_recall_top_k` 자리 경쟁 | 1칸 | **5칸** — `73ad11c` 에서 방금 고친 문제가 되돌아온다 |
| 동명 fact | 없음 | 5건이 같은 이름 → recall 점수로 구분 불가 |
| `candidates[0]` 선택 | 문제 없음 | 사실상 임의 선택 → 1:N 라우팅 강제 |
| 개념 노드 | 작음 | 커짐 → union-find 과병합(실측 18멤버 blob) |

게다가 **쪼개도 코드 대조는 안 된다.** 쪼갠 fact 의 속성 이름도 `charge_temp_range` 이지
기준의 `lower_limit` 이 아니다. 이름 문제는 쪼개기로 풀리지 않는다.

## 2. 목표와 비목표

**목표**

- 블록·셀 경계를 **의미 경계로 취급하지 않는다.**
- **fact 는 뭉쳐 담되, 조건마다 속성을 나눈다** — 검색 단위는 하나, 판정 단위는 조건별.
  RAG 엔진의 `excel.granularity: hybrid`(행=검색 단위, 셀=판정 단위)와 같은 전략이다.
- 그 판단은 **LLM 이** 한다. 코드는 판단하지 않고, 판단에 필요한 원문을 덜 훼손해서
  넘기는 데까지만 관여한다.
- 축약이 일어나면 **탐지된다.**

**비목표**

- 연속행 판별 규칙을 코드에 넣지 않는다(§1.4-①).
- **fact 를 조건 단위로 쪼개지 않는다**(§1.4-②).
- 표의 행을 코드가 펼치지 않는다.
- `compact_raw` 스키마를 바꾸지 않는다(§3).
- Excel 경로(F1 schema → F2 records → F3 코드 추출)는 건드리지 않는다.
- **속성 이름 정합**(기준 `lower_limit` ↔ 대상 `charge_temp_range_3`)은 이번 범위 밖이다(§12).

## 3. 결정 0 — `compact_raw` 는 한 글자도 바꾸지 않는다

```
compact_raw 를 지문·프롬프트로 쓰는 곳
  ├ profiler.py:32        PROFILER_VERSION  ← F1 캐시 + build_profiler_user(compact)
  ├ schema_inducer.py:39  SCHEMA_VERSION    ← Excel 전용
  └ fact_extractor.py:61  FACT_VERSION      ← 우리가 고치려는 곳
```

compact 에 필드를 더하면 **고칠 것은 F3 하나인데 F1 프로파일러의 캐시까지 무효화되고
그 프롬프트도 부풀어 오른다.**

⚠️ 표에서 특히 중요하다. **표의 `rows` 는 `compact_raw` 에 그대로 실린다**
(`compact.py:118`). 그래서 `_cell_text()` 가 만드는 셀 문자열을 바꾸면 compact 이 바뀐다.
**셀 문자열은 지금 그대로 두고, 줄 정보를 별도 필드로 낸다.**

대신 이미 있는 패턴을 쓴다. `build_facts_by_block()` 은 `physical_raw` 를 별도 인자로
받는다(`pipeline.py:413`). `extract_facts()` 도 같은 방식으로 받는다.

```python
facts = extract_facts(
    compact, profile=profile, runner=runner, store=store,
    batch_blocks=self.fact.fact_batch_blocks, stats=fact_stats,
    lines_by_block=raw_obj.to_dict(),      # ← 추가 (build_facts_by_block 과 같은 인자)
)
```

### 3.1 지문에 반드시 반영할 것

새 입력이 지문에 없으면 **같은 compact + 다른 줄 정보인 두 실행이 캐시를 공유**해 옛
결과를 준다. 조용히 틀리는 종류다.

```python
payload = json.dumps(compact, ensure_ascii=False)
if lines_payload:                       # Word 만 — 없으면 기존과 바이트 동일
    payload += json.dumps(lines_payload, ensure_ascii=False, sort_keys=True)
fingerprint_for(payload, FACT_VERSION)
```

`lines_payload` 는 `physical_raw` 전체가 아니라 **F3 렌더에 실제로 쓰는 부분만** 추린
것이다. 전체를 넣으면 렌더에 안 쓰는 필드가 바뀌어도 캐시가 깨진다.

## 4. §1 — raw 가 줄 구조를 버리지 않는다

| 파일 | 변경 |
|---|---|
| `raw/models.py` | `RawLine.indent: int = 0`<br>`RawWordBlock.indent: int = 0` (문단 들여쓰기)<br>`RawWordBlock.cell_lines: list[list[list[str]]]` — 표 전용. **`rows` 와 같은 모양에 한 겹을 더한 것**(행 × 열 × 줄)이라 인덱스로 바로 짝지어진다. 튜플 키 dict 를 쓰지 않는 이유는 JSON 직렬화가 안 되기 때문이다. 줄이 하나뿐인 셀은 `[]` 로 두어(원소 1개짜리 리스트가 아니라) **여러 줄인 셀만 골라내는 판정을 `if cell_lines[r][c]` 한 줄로** 만든다 |
| `raw/word_raw.py` | `_split_lines()` 가 `strip()` **전에** `indent` 를 센다<br>`_parse_paragraph()` 가 `<w:pPr><w:ind w:left>` 를 읽는다 **(지금은 아예 안 읽음)**<br>`_parse_table()` 이 셀마다 **줄 목록을 함께** 만든다 |

**바꾸지 않는 것 — 이게 이 절의 안전장치다.**

- `RawLine.raw_text` 는 계속 strip 한다(근거 인용 검증의 기준).
- `RawWordBlock.text` 는 계속 `" ".join(split())`.
- **표의 `rows` 셀 문자열도 계속 뭉갠 채**로 둔다 — compact 로 가기 때문이다(§3).

`<w:ind w:left>` 단위는 twips(1/1440인치). `indent` 는 칸 수로 환산해 담는다
(`round(twips / 120)`, 720 twips ≈ 6칸).

> ⚠️ **미확정**: 형태 A 의 연속행 신호가 `<w:ind>` 인지 선행 공백인지 아직 확인되지
> 않았다(`scripts/inspect_paragraph.py` 미실행). 둘 다 읽으므로 어느 쪽이든 동작하지만,
> 둘 다 없으면 형태 A 의 개선은 §6·§7 에만 의존한다. **구현 전에 확인한다.**
> 형태 B(표)는 이미 확인됐으므로 영향받지 않는다.

## 5. §2 — F3 입력을 원문 모양 그대로 렌더한다

### 5.1 문단

```
[지금]
[w_b245] 12~15도씨, 0.7C(4.55V) 15~45도씨, 1.2C (4.20V)
[w_b246] 1.1C(4.28V) 0.8C (4.55V)

[바뀐 뒤]
[w_b245] 12~15도씨, 0.7C(4.55V)
         15~45도씨, 1.2C (4.20V)
[w_b246]            1.1C(4.28V)
                    0.8C (4.55V)
```

### 5.2 표

파이썬 리스트 repr 을 버리고 **행 단위**로 렌더한다. 여러 줄인 셀은 줄을 살린다.

```
[지금]
[w_b289] 표 [['2.3.23', 'Operating Protocol…', 'Operating Protocol…',
              'Cell charge protocol by different temperature range',
              '-5~5도씨, 0.1C(4.55V), 5~12도씨, 0.3C(4.55V) 12~15도씨, …']]

[바뀐 뒤]
[w_b289] 표 (1행 × 5열)
  행1 | 2.3.23
      | Operating Protocol in different Temperatures
      | Operating Protocol in different Temperatures
      | Cell charge protocol by different temperature range
      | -5~5도씨, 0.1C(4.55V),
        5~12도씨, 0.3C(4.55V)
        12~15도씨, 0.8C(4.55V)
        …
```

규칙:

- 줄이 1개인 블록·셀은 **출력이 안 바뀐다** — 대부분의 블록은 그대로다.
- **행을 펼치지 않는다.** 셀 안 줄은 그 셀 안에서만 보인다(§1.4-②).
- 들여쓰기 상한을 둔다(40칸) — 비정상적으로 큰 값이 프롬프트를 망가뜨리지 않게.
- `lines_by_block` 이 없으면(PPT·Excel·옛 산출물) **기존 렌더 그대로**.

**코드는 여기서 아무 판단도 하지 않는다.** 원문을 덜 훼손해 넘길 뿐이다.

## 6. §3 — 프롬프트: 병합을 유지하고, 조건별 속성을 요구한다

`FACT_SYSTEM` 에 아래를 넣는다. **분해 방향은 넣지 않는다**(§1.4-②).

```
- 블록·셀 경계는 작성자가 Enter 를 눌렀는지, 표로 그렸는지의 결과일 뿐 의미 경계가
  아닙니다. 레이블이 생략된 줄·블록은 앞의 레이블에 딸린 것일 수 있습니다.
- 한 항목에 조건이 여럿이면(온도 구간별 전류 등) **fact 를 나누지 말고 하나로 두되,
  조건마다 속성을 나눠 담으세요**: charge_temp_range_1 / charge_rate_1 /
  charge_temp_range_2 / … 처럼 번호를 붙입니다.
- 조건 하나만 담고 나머지를 버리지 마세요. 값을 한 문자열로 뭉쳐 담지도 마세요 —
  둘 다 비교 불가가 됩니다.
- 다른 블록·줄의 레이블을 이어받아 조건을 채웠으면 inherited_from 에 그 [id] 를
  적으세요. 판단이 서지 않으면 이어받지 말고 confidence 를 낮추세요.
```

출력 스키마에 `"inherited_from": ["<앞 블록/줄의 id>"]` 를 추가한다.

`FACT_VERSION` 을 `fact-v2` → **`fact-v3`** 로 올린다(`prompts.py:229`). 이 값이 재추출을
일으키는 스위치이자 롤백 스위치다.

## 7. §4 — 배치 맥락

`_pack_batches`(`fact_extractor.py:259-270`)는 겹침 없이 20개씩 자른다. 형태 A 는 운 좋게
안 걸렸지만(245·246 이 같은 배치) **20블록마다 한 번씩 같은 사고가 난다.**

각 배치 앞에 **직전 배치의 마지막 K(=3) 블록을 `[맥락]` 으로** 덧붙인다.

```
아래 [맥락] 블록은 앞에서 이미 처리했습니다 — 앞뒤 관계를 이해하는 데만 쓰고,
그 블록에 대한 fact 는 만들지 마세요.

[맥락][w_b245] 12~15도씨, 0.7C(4.55V)
                15~45도씨, 1.2C (4.20V)
[w_b246]                    1.1C(4.28V)
```

**중복 fact 는 원천적으로 안 생긴다.** `_facts_from_blocks()` 는 이미 `batch_ids` 로
`source_ids` 를 검증한다(`fact_extractor.py:165,173`). 맥락 블록 id 를 `batch_ids` 에 넣지
않으면 그 블록만 근거로 하는 fact 는 자동으로 드롭된다. 겹쳐 넣고 사후에 중복을 지우는
방식은 쓰지 않는다 — 중복 판정 기준이 또 하나의 추측이 되기 때문이다.

⚠️ 표 블록이 맥락에 들어가면 토큰이 크게 늘 수 있다. **맥락 블록은 표일 경우 첫 2행만
싣는다**(전체가 아니라).

첫 배치는 맥락이 없다. K 는 상수로 시작하고 설정으로 빼지 않는다(YAGNI).

## 8. §5 — 계측: 뭉쳐도 새는 것을 잡는다

fact 를 뭉치기로 했으므로, **"뭉치면서 버렸는가"** 를 재는 지표가 필수가 된다.

### 8.1 `numeric_coverage` (신설)

근거 원문의 숫자 대비 속성에 담긴 수치의 비율. **판단이 아니라 셈이다.**

```
fact-word-79
  evidence_text 의 숫자:  -5, 5, 0.1, 4.55, 5, 12, 0.3, 4.55, 12, 15, 0.8, 4.55 …
  attributes 의 수치:     0개
  → numeric_coverage = 0.0   ⚠️ 축약 의심

fact-word-8 (Nominal Capacity)
  evidence: "1150 mAh" → 1개 · attributes: {nominal_capacity: 1150} → 1개
  → numeric_coverage = 1.0   ✅
```

가드 두 개로 오탐을 막는다.

- **`warn` 으로만** 둔다(`error` 아님) — 버전 번호(`ver.4.7`)나 조항 번호가 근거에 섞이는
  경우가 흔하다.
- **근거에 숫자가 4개 이상일 때만** 켠다 — 숫자 한둘은 서술문에서 자연스럽다.

이것은 설계 문서가 *"`conditional_series` 게이트의 몫"* 이라고 미뤄 둔 문제의 **가장 단순한
관측 가능한 형태**다. 완전한 게이트는 아니지만, **지금 완전히 안 보이는 것을 보이게** 한다.

### 8.2 나머지

| 무엇 | 어디 | 왜 |
|---|---|---|
| `Fact.inherited_from: list[str]` | `facts.json` | 다른 블록·줄의 레이블을 이어받아 채운 조건이 있음을 표시. `decided_by`·`quote_verified` 와 같은 원리 — **추론한 것은 추론했다고 남긴다** |
| `facts_inherited` | `run_stats.json` | 상속이 과하지 않은지 |
| `numeric_coverage` 분포 | `run_stats.json` | 축약이 얼마나 남았는지 |
| **표를 줄 커버리지 분모에 포함** | `facts_by_block.json` | 지금은 제외한다. 근거였던 *"표는 블록이 이미 최소 단위"* 가정을 `w_b289` 가 반박했다 |

`Fact` 는 `to_dict`/`from_dict`/`from_llm` 세 곳을 함께 고친다. 새 키는 additive 라 기존
산출물을 읽을 때는 빈 값으로 채워진다.

## 9. 위험

| 위험 | 크기 | 완화 |
|---|---|---|
| **LLM 이 과하게 이어받아 없는 조건을 만든다** | 큼 | ① 프롬프트에 "서지 않으면 이어받지 마라" ② `inherited_from` 으로 전수 검수 ③ F4a 근거 검증 |
| **표 렌더 변경이 지금 잘 나오던 표의 fact 를 흔든다** | 큼 | 샘플의 `w_b012` 는 현재 방식으로 fact 5건을 정확히 만든다. **`golden/` 대조가 이번엔 선택이 아니라 필수 관문** |
| 전 문서 F3 재추출 비용 | 중 | 불가피. 결정 0 덕분에 F1·Excel 캐시는 살아 있어 범위가 최소 |
| 프롬프트 토큰 증가(줄 렌더 + 맥락 3블록 + 표 행 렌더) | 중 | 측정해 보고. ⚠️ Ollama 는 컨텍스트 초과 시 오류가 아니라 **빈 응답**을 준다 — `num_ctx` 확인 |
| `numeric_coverage` 오탐 | 낮음 | `warn` 전용 + 숫자 4개 이상 가드 |
| `<w:ind>` 가 없어 §4 의 문단 부분이 헛일 | 미확정 | 구현 전 `inspect_paragraph.py` 확인(§4 경고). 표 경로는 무관 |

## 10. 테스트

전부 `FakeLLM`/순수 파서로 — Office·네트워크 없이 돈다(기존 규약).

| 대상 | 검증 |
|---|---|
| `_split_lines` | 선행 공백·탭이 `indent` 로 잡히고 `raw_text` 는 여전히 strip 된다 |
| `_parse_paragraph` | `<w:ind w:left="720">` → `indent` 6칸. 없으면 0 |
| `_parse_table` | 셀 안 `<w:br/>`·여러 `<w:p>` 가 줄 목록으로 남는다 |
| `build_word_doc` | **`block.text` 와 표 `rows` 가 변경 전과 바이트 동일** (결정 0 회귀 테스트) |
| `compact_word` | 출력이 변경 전과 바이트 동일 |
| `_render_unit` | 문단: 줄 1개는 기존 형태, 2개 이상은 들여쓴 형태 / 표: 행 단위 렌더, **행은 펼치지 않는다** |
| `_pack_batches` + 렌더 | 맥락 블록이 `[맥락]` 으로 붙고 **`batch_ids` 에는 없다**. 표 맥락은 첫 2행만 |
| `_facts_from_blocks` | 맥락 블록만 근거로 한 fact 는 드롭된다 |
| 지문 | 같은 compact + 다른 줄 정보 → **다른** 지문 |
| `numeric_coverage` | 숫자 3개 이하면 안 켜진다 · 문자열 뭉침 fact 에서 0.0 |
| e2e | `FakeLLM` 이 조건별 속성 + `inherited_from` 을 낸 시나리오가 `facts.json`·`run_stats` 까지 흐른다 |
| **golden 대조** | `golden/` 정답과 대조해 **기존에 맞던 fact 가 깨지지 않았는지** |

기존 828개는 전부 유지한다.

## 11. 롤백

`FACT_VERSION` 을 `fact-v2` 로 되돌리면 프롬프트와 캐시가 원복된다. `raw/` 의 새 필드
(`indent`·`cell_lines`)는 additive 이고 `text`/`rows` 를 건드리지 않으므로 남아 있어도
무해하다. `lines_by_block` 인자는 기본 `None` 이라 넘기지 않으면 기존 경로 그대로다.

## 12. 후속 (이번 범위 밖)

- **속성 이름 정합** — 기준의 `lower_limit`/`upper_limit` 과 대상의
  `charge_temp_range_3` 은 이름이 겹치지 않아 `_decide_by_code()` 가 값을 나란히 놓지
  못한다. 뭉쳐 담기로 한 이상 이 항목의 F5 는 **LLM 위임**이 된다. 온톨로지의 속성
  매핑이나 F5 의 속성 정규화로 따로 풀어야 한다. **이 설계가 남기는 가장 큰 빚이다.**
- 이어받은 조건이 실제로 옳은지 2차 Evidence 재검사
  (`docs/FACT_LINKED_GRAPH_RAG_DESIGN.md` 의 `conditional_series` 게이트)
- `concept_recall_top_k` 상향(`73ad11c`)과의 상호작용 — 뭉쳐 담기로 했으므로 형제 경쟁이
  늘지는 않는다. 재추출 후 실측으로 확인
