# Word 블록 경계 재구성 — 손버릇이 만든 경계를 LLM 이 다시 긋게 한다

- 작성일: 2026-08-14
- 상태: **설계 합의 완료 — 구현 전**
- 관련 문서: `docs/FACT_LINKED_GRAPH_RAG_DESIGN.md` §2, `docs/FACT_PIPELINE_PLAN.md`,
  `docs/understanding/2026-08-10-explanation-charge-temperature-merge.html`

## 1. 문제

Word 문서에서 **한 항목이 조건별로 여러 줄에 걸쳐 적히면 일부가 통째로 사라진다.**

실측 원문(사용자 문서):

```
12~15도씨, 0.7C(4.55V)
15~45도씨, 1.2C (4.20V)
           1.1C(4.28V)        ← 앞 줄의 "15~45도씨" 를 생략한 연속행
           0.8C (4.55V)
```

`physical_raw.json` 결과:

| block | text |
|---|---|
| `w_b245` | `12~15도씨, 0.7C(4.55V) 15~45도씨, 1.2C (4.20V)` |
| `w_b246` | `1.1C(4.28V) 0.8C (4.55V)` |

F3 는 `w_b246` 에서 fact 를 만들지 못한다. **만들 수 없는 것이 맞다** — 그 블록에는
주어가 없고, 프롬프트는 *"입력에 실제로 있는 문구만"*, *"지어내기 금지"* 를 요구한다.
LLM 은 규칙에 충실했다.

### 1.1 근본 원인 — 블록 경계는 의미 경계가 아니다

Word 작성자는 Enter 와 Shift+Enter 를 **자기 편한 대로** 쓴다. 같은 표를 두 사람이
쓰면 한 사람은 문단 하나에 `<w:br/>` 로, 다른 사람은 문단 여러 개로 만든다. 우리
파서는 `<w:body>` 의 직계 자식만 훑어 `<w:p>` 하나 = 블록 하나로 옮기므로
(`raw/word_raw.py:190-195`), **블록 경계가 곧 손버릇의 기록**이 된다.

파이프라인은 그 경계를 의미 단위로 취급한다:

| 자리 | 코드 | 무엇을 가정하나 |
|---|---|---|
| 배치 구성 | `fact_extractor.py:205-225` | Word 는 **블록 1개 = 1그룹** |
| 프롬프트 렌더 | `prompts.py` `_render_unit()` | 블록 = `[id]` 가 붙은 하나의 항목 |
| 프롬프트 규칙 | `FACT_SYSTEM` | *"흩어진 서술이 같은 대상이면 하나로 **병합**"* — 병합 방향만 있고 분해·상속이 없다 |

### 1.2 "LLM 에게 한꺼번에 보여주면 된다"는 이미 하고 있다

`fact_batch_blocks: 20` 이고 `_pack_batches` 는 그룹을 순서대로 채우므로 배치 경계는
정확히 1–20, 21–40, …, **241–260** 이다. `w_b245` 와 `w_b246` 은 **같은 배치 = 같은 LLM
호출**에 들어갔다. 즉 LLM 은 두 블록을 나란히 보고도 잇지 못했다.

그러므로 원인은 맥락 부족이 아니라 **둘**이다.

1. **프롬프트가 블록을 원자로 못 박는다** (§1.1 표 3행)
2. **판단 근거를 우리가 먼저 지운다** — `_render_unit()` 이 넘기는 값은 `compact` 의
   `text` 이고, 그것은 `build_word_doc()` 의 `" ".join(text.split())`
   (`word_raw.py:86`)와 `_split_lines()` 의 `piece.strip()` (`word_raw.py:129`)을 거쳐
   **줄바꿈도 들여쓰기도 없는 한 줄**이다. 원본의 시각적 정렬은 프롬프트에 닿지 못한다.

실측으로 확인했다 — `ParaProbe.text` 단계까지는 선행 공백 23칸과 탭이 **그대로 살아
있다**. 정보는 파일에서 나올 때 존재했고, 우리 코드가 두 번 버린다.

### 1.3 기각한 접근 — 코드가 연속행을 판별하기

"선행 공백이 있으면 앞 줄의 레이블을 상속한다" 같은 규칙을 코드에 넣는 안을 먼저
검토했고 **기각했다.** 작성자 습관을 규칙으로 예측하는 시도이기 때문이다. 들여쓰기의
의미는 문서마다 다르고(각주·인용·목록), 오탐은 **없는 내용을 만들어내는** 가장 위험한
실패가 된다. 표의 `vMerge` 처리(`_parse_table`)가 안전한 이유는 Word 가 `<w:vMerge/>`
로 **명시**해 주기 때문이지, 우리가 잘 추측해서가 아니다.

## 2. 목표와 비목표

**목표**

- 블록 경계를 **의미 경계로 취급하지 않는다.** 한 블록이 여러 fact 가 될 수도, 레이블
  없는 블록이 앞 블록의 레이블을 이어받을 수도 있게 한다.
- 그 판단은 **LLM 이** 한다. 코드는 판단하지 않고, 판단에 필요한 원문을 덜 훼손해서
  넘기는 데까지만 관여한다.
- 이어받은 사실을 산출물에 남겨 **사람이 전수 검수**할 수 있게 한다.
- 배치 경계가 의미를 자르지 않게 한다.

**비목표**

- 연속행 판별 규칙을 코드에 넣지 않는다(§1.3).
- `compact_raw` 스키마를 바꾸지 않는다(결정 0).
- Excel 경로(F1 schema → F2 records → F3 코드 추출)는 건드리지 않는다.
- PPT 경로의 그룹 구성(슬라이드=1그룹)은 바꾸지 않는다.

## 3. 결정 0 — `compact_raw` 는 한 글자도 바꾸지 않는다

초안은 "`compact` 에 `lines` 를 실어 LLM 에 구조를 보여주자"였다. **틀린 자리였다.**

```
compact_raw 를 지문·프롬프트로 쓰는 곳
  ├ profiler.py:32        PROFILER_VERSION  ← F1 캐시 + build_profiler_user(compact)
  ├ schema_inducer.py:39  SCHEMA_VERSION    ← Excel 전용
  └ fact_extractor.py:61  FACT_VERSION      ← 우리가 고치려는 곳
```

compact 에 필드를 더하면 **F1 프로파일러의 캐시까지 무효화되고 그 프롬프트도
부풀어 오른다.** 고칠 것은 F3 하나인데 세 단계가 흔들린다.

대신 **이미 있는 패턴**을 쓴다. `build_facts_by_block()` 은 `physical_raw` 를 별도
인자로 받는다 — `pipeline.py:413` 의 `lines_by_block=raw_obj.to_dict()`. `extract_facts()`
도 같은 방식으로 받는다. `raw_obj` 는 그 스코프에 이미 있다.

```python
# pipeline.py — Word/PPT 분기
facts = extract_facts(
    compact, profile=profile, runner=runner, store=store,
    batch_blocks=self.fact.fact_batch_blocks, stats=fact_stats,
    lines_by_block=raw_obj.to_dict(),      # ← 추가 (build_facts_by_block 과 같은 인자)
)
```

### 3.1 지문에 반드시 반영할 것

`extract_facts` 의 지문은 지금 `fingerprint_for(json.dumps(compact), FACT_VERSION)` 이다.
새 입력이 지문에 없으면 **같은 compact + 다른 lines 인 두 실행이 캐시를 공유**해 옛
결과를 준다. 따라서 지문을 다음으로 바꾼다.

```python
payload = json.dumps(compact, ensure_ascii=False)
if lines_payload:                       # Word 만 — 없으면 기존과 바이트 동일
    payload += json.dumps(lines_payload, ensure_ascii=False, sort_keys=True)
fingerprint_for(payload, FACT_VERSION)
```

`lines_payload` 는 `physical_raw` 전체가 아니라 **F3 렌더에 실제로 쓰는 부분만**
추린 것이다(`{block_id: [{raw_text, indent}], block_indent}`). 전체를 넣으면 렌더에
안 쓰는 필드가 바뀌어도 캐시가 깨진다.

## 4. §1 — raw 가 들여쓰기를 버리지 않는다

| 파일 | 변경 |
|---|---|
| `raw/models.py` | `RawLine.indent: int = 0` — 그 줄의 선행 공백 칸 수(탭은 4칸 환산)<br>`RawWordBlock.indent: int = 0` — 문단 자체의 들여쓰기 |
| `raw/word_raw.py` | `_split_lines()` 가 `strip()` **전에** `indent` 를 센다<br>`_parse_paragraph()` 가 `<w:pPr><w:ind w:left>` 를 읽어 `ParaProbe.indent` 에 담는다 |

**바꾸지 않는 것 두 가지** — 이게 이 절의 안전장치다.

- `RawLine.raw_text` 는 계속 양끝을 strip 한다. 인용 검증(`_quote_in_evidence`,
  `evidence_coverage`)이 이 문자열을 기준으로 하므로 규약을 흔들면 안 된다.
- `RawWordBlock.text` 도 계속 `" ".join(split())` 이다. 이 값이 `compact` 로 가므로
  결정 0 이 성립하려면 불변이어야 한다.

`<w:ind w:left>` 의 단위는 twips(1/1440 인치)다. `indent` 는 **칸 수로 환산**해 담는다
(`round(twips / 120)`, 720 twips ≈ 6칸). 원값을 그대로 두지 않는 이유는 §2 의 렌더가
줄 내부 공백과 같은 축에서 비교해야 하기 때문이다.

> ⚠️ **미확정**: 사용자 문서의 연속행 신호가 `<w:ind>` 인지 선행 공백인지 아직
> 확인되지 않았다(`scripts/inspect_paragraph.py` 미실행). 둘 다 읽으므로 어느 쪽이든
> 동작하지만, 둘 다 없으면(㉢ 신호 없음) §2 의 렌더는 지금과 같은 모양이 되고 이
> 설계의 효과는 §3·§4 에만 의존하게 된다. **구현 전에 반드시 확인한다.**

## 5. §2 — F3 입력을 원문 모양 그대로 렌더한다

`prompts.py` 의 `_render_unit()` 이 블록에 줄 정보가 있으면 줄 단위로 렌더한다.

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

규칙:

- 줄이 1개면 지금과 같은 한 줄 형태 — **대부분의 블록은 출력이 안 바뀐다.**
- 줄이 2개 이상이면 `[id]` 를 첫 줄에만 붙이고 나머지는 자리를 맞춰 들여쓴다.
- 들여쓰기는 `block.indent + line.indent` 칸. 상한을 둔다(예: 40칸) — 비정상적으로 큰
  값이 프롬프트를 망가뜨리지 않게.
- `lines_by_block` 이 없으면(PPT·Excel·옛 산출물) **기존 렌더 그대로**.

**코드는 여기서 아무 판단도 하지 않는다.** 원문을 덜 훼손해 넘길 뿐이다.

## 6. §3 — 프롬프트 재프레임

`FACT_SYSTEM` 규칙에 반대 방향을 넣는다.

```
- 블록 경계는 작성자가 Enter 를 눌렀는지 Shift+Enter 를 눌렀는지의 결과일 뿐,
  의미 경계가 아닙니다. 한 블록이 여러 fact 일 수 있고, 여러 블록이 한 fact 일
  수 있습니다.
- 들여쓴 줄이나 블록이 앞의 레이블(조건·항목명)을 생략한 것으로 보이면, 그 레이블을
  이어받아 **독립된 fact** 로 만드세요. 이어받았으면 inherited_from 에 그 [id] 를
  적으세요.
- 이어받을지 판단이 서지 않으면 이어받지 말고 confidence 를 낮추세요.
  틀린 상속은 없는 내용을 만들어내는 것이라 누락보다 나쁩니다.
```

출력 스키마에 `"inherited_from": ["<앞 블록/줄의 id>"]` 를 추가한다.

`FACT_VERSION` 을 `fact-v2` → **`fact-v3`** 로 올린다(`prompts.py:229`). 이 값이
재추출을 일으키는 스위치이자 롤백 스위치다.

## 7. §4 — 배치 맥락

`_pack_batches`(`fact_extractor.py:259-270`)는 겹침 없이 20개씩 자른다. 이번 사례는
운 좋게 안 걸렸지만 **20블록마다 한 번씩 같은 사고가 난다.**

각 배치 앞에 **직전 배치의 마지막 K(=3) 블록을 `[맥락]` 으로** 덧붙인다.

```
다음은 word 문서의 블록입니다. [id] 는 source_ids 에 넣을 식별자입니다.
아래 [맥락] 표시가 붙은 블록은 앞에서 이미 처리했습니다 — 앞뒤 관계를 이해하는
데만 쓰고, 그 블록에 대한 fact 는 만들지 마세요.

[맥락][w_b243] ...
[맥락][w_b244] ...
[맥락][w_b245] 12~15도씨, 0.7C(4.55V)
                15~45도씨, 1.2C (4.20V)
[w_b246]                    1.1C(4.28V)
```

**중복 fact 는 원천적으로 안 생긴다.** `_facts_from_blocks()` 는 이미 `batch_ids` 로
`source_ids` 를 검증하는데(`fact_extractor.py:165,173`), 맥락 블록 id 를 `batch_ids` 에
넣지 않으면 그 블록만 근거로 하는 fact 는 자동으로 드롭된다. 겹쳐 넣고 사후에 중복을
지우는 방식은 쓰지 않는다 — 중복 판정 기준이 또 하나의 추측이 되기 때문이다.

첫 배치는 맥락이 없다. K 는 상수로 시작하고 설정으로 빼지 않는다(YAGNI).

## 8. §5 — 계측

| 무엇 | 어디 | 왜 |
|---|---|---|
| `Fact.inherited_from: list[str]` | `facts.json` | "이 fact 의 온도 범위는 LLM 이 앞 블록에서 이어받은 것"을 사람이 안다. `decided_by`·`quote_verified` 와 같은 원리 — **추론한 것은 추론했다고 남긴다** |
| `facts_inherited` | `run_stats.json` | 상속이 실제로 일어나는지, 과하지 않은지 |
| `context_blocks_per_batch` | `run_stats.json` | §4 가 실제로 붙는지 |
| `units_uncited` 변화 | `facts_by_block.json` | **재추출 비용을 치르고도 안 나아졌으면 알아야 한다** |

`Fact` 는 `to_dict`/`from_dict`/`from_llm` 세 곳을 함께 고친다. 새 키는 additive 라
기존 산출물을 읽을 때는 빈 리스트로 채워진다.

## 9. 위험

| 위험 | 크기 | 완화 |
|---|---|---|
| **LLM 이 과하게 이어받아 없는 조건을 만든다** | 가장 큼 | ① 프롬프트에 "서지 않으면 이어받지 마라" 명시 ② `inherited_from` 으로 전수 검수 ③ F4a 근거 검증이 evidence 없는 fact 를 저신뢰로 표시 |
| 전 문서 F3 재추출 비용 | 중 | 불가피. 단 결정 0 덕분에 F1·Excel 캐시는 살아 있어 범위가 최소 |
| 프롬프트 토큰 증가(줄 렌더 + 맥락 3블록) | 중 | 측정해 보고. ⚠️ Ollama 는 컨텍스트 초과 시 오류가 아니라 **빈 응답**을 준다 — `num_ctx` 확인 필요 |
| 줄 렌더가 기존 추출 결과를 흔든다 | 중 | 줄 1개 블록은 출력 불변이므로 대부분의 블록은 그대로. 다만 fact 수·이름이 달라질 수 있으므로 `golden/` 대조 필수 |
| `<w:ind>` 가 없어 §1 이 헛일 | 미확정 | 구현 전 `inspect_paragraph.py` 로 확인(§4 경고) |

## 10. 테스트

전부 `FakeLLM`/순수 파서로 — Office·네트워크 없이 돈다(기존 규약).

| 대상 | 검증 |
|---|---|
| `_split_lines` | 선행 공백·탭이 `indent` 로 잡히고 `raw_text` 는 여전히 strip 된다 |
| `_parse_paragraph` | `<w:ind w:left="720">` → `indent` 6칸 환산. `<w:ind>` 없으면 0 |
| `build_word_doc` | **`block.text` 가 변경 전과 바이트 동일** (결정 0 의 회귀 테스트) |
| `compact_word` | 출력이 변경 전과 바이트 동일 |
| `_render_unit` | 줄 1개는 기존 형태, 2개 이상은 들여쓴 형태. `lines_by_block` 없으면 기존 형태 |
| `_pack_batches` + 렌더 | 맥락 블록이 `[맥락]` 으로 붙고, **`batch_ids` 에는 없다** |
| `_facts_from_blocks` | 맥락 블록만 근거로 한 fact 는 드롭된다 |
| 지문 | 같은 compact + 다른 lines → **다른** 지문 |
| e2e | `FakeLLM` 이 `inherited_from` 을 낸 시나리오가 `facts.json`·`run_stats` 까지 흐른다 |

기존 828개는 전부 유지한다.

## 11. 롤백

`FACT_VERSION` 을 `fact-v2` 로 되돌리면 프롬프트와 캐시가 원복된다. `raw/` 의 새 필드
(`indent`)는 additive 이고 `text` 계열을 건드리지 않으므로 남아 있어도 무해하다.
`lines_by_block` 인자는 기본 `None` 이라 넘기지 않으면 기존 경로 그대로다.

## 12. 후속 (이번 범위 밖)

- 이어받은 fact 가 실제로 옳은지 2차 Evidence 검사로 확인하기
  (`docs/FACT_LINKED_GRAPH_RAG_DESIGN.md` 의 `conditional_series` 게이트)
- 표(`<w:tbl>`)로 그려진 같은 내용과의 일관성 — 표는 `vMerge` 로 이미 채워지므로
  문단 경로와 결과가 같아야 한다
- `concept_recall_top_k` 상향(`73ad11c`)과의 상호작용 — fact 수가 늘면 형제 경쟁이
  더 심해진다
