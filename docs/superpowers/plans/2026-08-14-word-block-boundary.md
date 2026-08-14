# Word 블록·셀 경계 재구성 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Word 문단·표에서 줄 구조(줄바꿈·들여쓰기)를 버리지 않고 F3 LLM 에 보여줘, 조건이 여럿인 항목이 값을 잃지 않고 **하나의 fact 에 조건별 속성**으로 담기게 한다.

**Architecture:** 코드는 판단하지 않는다 — 원문 구조를 보존해 렌더하고(§raw·§렌더), 판단은 LLM 이 하며(§프롬프트), 축약이 남으면 계측이 드러낸다(§numeric_coverage). `compact_raw` 는 한 글자도 바꾸지 않고 `physical_raw` 를 **별도 인자**로 F3 에 넘긴다 — `build_facts_by_block()` 이 이미 쓰는 패턴이다.

**Tech Stack:** Python 3.10+ · 표준 라이브러리(`xml.etree.ElementTree`, `dataclasses`, `re`, `json`) · pytest. 새 의존성 없음.

**Spec:** `docs/superpowers/specs/2026-08-14-word-block-boundary-design.md`

## Global Constraints

- **`compact_raw` 출력은 바이트 동일해야 한다.** `RawWordBlock.text` 와 표 `rows` 의 셀 문자열을 바꾸지 말 것. 이 계약이 깨지면 F1 프로파일러 캐시까지 무효화된다(스펙 §3).
- **`RawLine.raw_text` 는 계속 양끝 strip 한다.** 근거 인용 검증(`_quote_in_evidence`, `evidence_coverage`)의 기준 문자열이다(스펙 §4).
- **fact 를 조건 단위로 쪼개지 않는다.** 조건이 여럿이면 fact 1건 + 속성 여럿(스펙 §1.4-②).
- **표의 행을 코드가 펼치지 않는다**(스펙 §2 비목표).
- 새 필드는 전부 **additive** 이고 기본값이 있어야 한다 — 옛 산출물을 읽을 때 깨지지 않아야 한다.
- 테스트는 `FakeLLM`/순수 파서로만 — Office·네트워크 없이 모든 OS 에서 돌아야 한다.
- 모든 신규 주석·docstring 은 **한국어**, 식별자는 영어(`CLAUDE.md`).
- 기존 테스트 828개는 전부 통과해야 한다.

---

## File Structure

| 파일 | 책임 | 변경 |
|---|---|---|
| `contentcompare/raw/models.py` | raw 자료구조 | `RawLine.indent`, `RawWordBlock.indent`, `RawWordBlock.cell_lines` 추가 |
| `contentcompare/raw/word_raw.py` | Word XML → raw | 들여쓰기 계측, `<w:ind>` 읽기, 셀 줄 보존 |
| `contentcompare/fact/fact_models.py` | fact 자료구조 | `Fact.inherited_from` 추가 |
| `contentcompare/fact/prompts.py` | F3 프롬프트·렌더 | 줄/행 단위 렌더, `FACT_SYSTEM` 재작성, `FACT_VERSION` 상향 |
| `contentcompare/fact/fact_extractor.py` | 배치·추출·계측 | `lines_by_block` 인자, 맥락 블록, 지문, 표 줄 커버리지 |
| `contentcompare/fact/validator.py` | F4a 코드 검증 | `numeric_coverage` 검사 신설 |
| `contentcompare/fact/pipeline.py` | 단계 배선 | `extract_facts` 에 `lines_by_block` 전달 |

테스트는 기존 파일에 얹는다: `tests/test_raw_word_lines.py`, `tests/test_raw_word.py`, `tests/test_raw_compact.py`, `tests/test_fact_fact_models.py`, `tests/test_fact_prompts.py`, `tests/test_fact_extractor.py`, `tests/test_fact_validator.py`, `tests/test_fact_facts_by_block.py`.

---

### Task 1: 줄 들여쓰기 보존 (`RawLine.indent`)

**Files:**
- Modify: `contentcompare/raw/models.py` (RawLine 정의·`to_dict`)
- Modify: `contentcompare/raw/word_raw.py` (`_split_lines`)
- Test: `tests/test_raw_word_lines.py`

**Interfaces:**
- Consumes: 없음(첫 태스크)
- Produces: `RawLine.indent: int` — 그 줄의 선행 공백 칸 수(탭=4칸). `to_dict()` 는 0 이면 키를 생략한다. Task 5·8 이 이 값을 읽는다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_raw_word_lines.py` 맨 아래에 추가:

```python
def test_split_lines_keeps_indent_width():
    """선행 공백·탭이 indent 로 남고, raw_text 는 여전히 strip 된다."""
    from contentcompare.raw.word_raw import _split_lines

    text = "15~45도씨, 1.2C(4.20V)\n           1.1C(4.28V)\n\t\t0.8C(4.55V)"
    lines = _split_lines("w_b010", text)

    assert [l.indent for l in lines] == [0, 11, 8]      # 탭 하나 = 4칸
    assert [l.raw_text for l in lines] == [
        "15~45도씨, 1.2C(4.20V)",
        "1.1C(4.28V)",
        "0.8C(4.55V)",
    ]


def test_raw_line_to_dict_omits_zero_indent():
    """0 을 싣지 않는다 — physical_raw 가 줄마다 쓸모없는 키로 커진다."""
    from contentcompare.raw.models import RawLine

    plain = RawLine(line_id="w_b001:l01", order=1, raw_text="a", indent=0)
    inset = RawLine(line_id="w_b001:l02", order=2, raw_text="b", indent=7)

    assert "indent" not in plain.to_dict()
    assert inset.to_dict()["indent"] == 7
```

- [ ] **Step 2: 실패를 확인한다**

Run: `python -m pytest tests/test_raw_word_lines.py -k indent -v`
Expected: FAIL — `TypeError: RawLine.__init__() got an unexpected keyword argument 'indent'`

- [ ] **Step 3: `RawLine` 에 필드를 더한다**

`contentcompare/raw/models.py` — `RawLine` 의 `normalized_text` 선언 **바로 아래**에 추가:

```python
    indent: int = 0
    """이 줄의 선행 공백 칸 수(탭은 4칸 환산). 0 이면 ``to_dict`` 에서 생략한다.

    원문에서 열을 맞춰 앞 줄의 레이블을 생략한 연속행은 **이 값으로만** 구분된다 —
    ``raw_text`` 는 인용 검증 규약을 지키려고 양끝을 strip 하므로 흔적이 남지 않는다.
    """
```

같은 클래스의 `to_dict()` 에서, `normalized_text` 를 넣는 `if` 블록 **아래**·`return out` **위**에 추가:

```python
        if self.indent:
            out["indent"] = self.indent
```

- [ ] **Step 4: `_split_lines` 가 들여쓰기를 세게 한다**

`contentcompare/raw/word_raw.py` — `_split_lines` 정의 **바로 위**에 추가:

```python
_TAB_WIDTH = 4
"""탭 하나를 몇 칸으로 셀 것인가.

실제 폭은 문서 설정에 달렸지만 여기서 필요한 것은 절대 폭이 아니라 **줄끼리의 상대
비교**라 고정값으로 충분하다. 값을 바꾸면 렌더의 들여쓰기 모양만 달라진다.
"""


def _indent_width(text: str) -> int:
    """줄의 선행 공백 칸 수. 탭은 :data:`_TAB_WIDTH` 칸으로 센다."""
    n = 0
    for ch in text:
        if ch == " ":
            n += 1
        elif ch == "\t":
            n += _TAB_WIDTH
        else:
            break
    return n
```

같은 파일 `_split_lines` 안의 `out.append(RawLine(...))` 호출을 아래로 교체:

```python
        out.append(RawLine(
            line_id=f"{block_id}:l{len(out) + 1:02d}",
            order=len(out) + 1,
            raw_text=raw,
            normalized_text=" ".join(raw.split()),
            indent=_indent_width(piece),
        ))
```

- [ ] **Step 5: 통과를 확인한다**

Run: `python -m pytest tests/test_raw_word_lines.py -v`
Expected: PASS (신규 2건 + 기존 전부)

- [ ] **Step 6: 커밋**

```bash
git add contentcompare/raw/models.py contentcompare/raw/word_raw.py tests/test_raw_word_lines.py
git commit -m "feat(raw): 줄 선행 공백을 indent 로 보존

raw_text 는 인용 검증 규약 때문에 strip 해야 하는데, 그러면 열을 맞춰 앞 줄의
레이블을 생략한 연속행이라는 사실이 사라진다. 버리는 대신 옆에 적어 둔다.
raw_text 와 block.text 는 건드리지 않는다."
```

---

### Task 2: 문단 들여쓰기 `<w:ind>` 읽기

**Files:**
- Modify: `contentcompare/raw/word_raw.py` (`ParaProbe`, `_parse_paragraph`, `build_word_doc`)
- Modify: `contentcompare/raw/models.py` (`RawWordBlock`)
- Test: `tests/test_raw_word.py`

**Interfaces:**
- Consumes: Task 1 의 `_indent_width` (같은 모듈, 직접 호출은 없음)
- Produces: `ParaProbe.indent: int`, `RawWordBlock.indent: int` — 문단 자체의 들여쓰기 칸 수. `to_dict()` 는 0 이면 생략. Task 5·8 이 읽는다.

- [ ] **Step 1: 선행 확인 — 이 태스크가 실제 문서에 유효한지 본다**

Run: `python scripts/inspect_paragraph.py <실제문서.docx> "1.1C" --context 2`

출력의 `ind :` 줄을 본다.

- `ind : left=1440 …` → 이 태스크가 그 문서에 유효하다.
- `(없음)` 이고 `text(raw)` 의 줄 앞에 공백이 보인다 → Task 1 이 이미 처리한다. **이 태스크는 그래도 진행한다** — 다른 문서에서 `<w:ind>` 를 쓰기 때문이고, 코드가 몇 줄로 끝난다.
- 둘 다 없다 → 그래도 진행하되, 스펙 §4 의 경고대로 **형태 A 의 개선은 §6·§7 에만 의존**함을 인지한다.

어느 경우든 코드는 같다. 확인 결과를 커밋 메시지에 한 줄로 남긴다.

- [ ] **Step 2: 실패하는 테스트를 쓴다**

`tests/test_raw_word.py` 맨 아래에 추가:

```python
def _doc_xml(body: str) -> str:
    return (
        '<?xml version="1.0"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{body}</w:body></w:document>"
    )


def test_paragraph_indent_from_w_ind():
    """<w:ind w:left> 를 칸 수로 환산해 담는다(720 twips = 0.5인치 ≈ 6칸)."""
    from contentcompare.raw.word_raw import parse_word_xml

    xml = _doc_xml(
        '<w:p><w:pPr><w:ind w:left="720"/></w:pPr><w:r><w:t>들여쓴 문단</w:t></w:r></w:p>'
        "<w:p><w:r><w:t>보통 문단</w:t></w:r></w:p>"
    )
    probes = parse_word_xml(xml)

    assert probes[0].indent == 6
    assert probes[1].indent == 0


def test_paragraph_indent_accepts_w_start_alias():
    """w:start 는 w:left 의 신형 이름이다 — 둘 다 읽어야 한다."""
    from contentcompare.raw.word_raw import parse_word_xml

    xml = _doc_xml(
        '<w:p><w:pPr><w:ind w:start="360"/></w:pPr><w:r><w:t>x</w:t></w:r></w:p>'
    )
    assert parse_word_xml(xml)[0].indent == 3


def test_block_indent_reaches_physical_raw():
    """문단 들여쓰기가 블록까지 흐르고, 0 이면 키가 없다."""
    from contentcompare.raw.word_raw import build_word_doc, parse_word_xml

    xml = _doc_xml(
        '<w:p><w:pPr><w:ind w:left="720"/></w:pPr><w:r><w:t>a</w:t></w:r></w:p>'
        "<w:p><w:r><w:t>b</w:t></w:r></w:p>"
    )
    blocks = build_word_doc("t.docx", parse_word_xml(xml)).to_dict()["blocks"]

    assert blocks[0]["indent"] == 6
    assert "indent" not in blocks[1]
```

- [ ] **Step 3: 실패를 확인한다**

Run: `python -m pytest tests/test_raw_word.py -k indent -v`
Expected: FAIL — `AttributeError: 'ParaProbe' object has no attribute 'indent'`

- [ ] **Step 4: `ParaProbe` 와 변환 함수를 더한다**

`contentcompare/raw/word_raw.py` — `ParaProbe` 의 `list_item` 선언 **아래**에 추가:

```python
    indent: int = 0
    """문단 자체의 들여쓰기 칸 수(``<w:pPr><w:ind w:left>``).

    문단이 통째로 밀려 있으면 그 텍스트 안에는 공백이 없어 :func:`_indent_width` 로는
    잡히지 않는다. 별도 문단으로 갈라진 연속행이 이 경로로만 드러난다.
    """
```

같은 파일 `_parse_paragraph` **바로 위**에 추가:

```python
_TWIPS_PER_COL = 120
"""twips(1/1440인치) → 칸 환산 계수. 720 twips(0.5인치) ≈ 6칸.

절대 정확도는 필요 없다 — 렌더에서 **다른 줄과의 상대 비교**에만 쓰이기 때문이다.
"""


def _twips_to_cols(value: Optional[str]) -> int:
    """``<w:ind>`` 속성값(twips 문자열) → 칸 수. 못 읽으면 0(추측하지 않는다)."""
    if not value:
        return 0
    try:
        return max(0, round(int(value) / _TWIPS_PER_COL))
    except (TypeError, ValueError):
        return 0
```

`_parse_paragraph` 본문을 아래로 교체:

```python
def _parse_paragraph(p: ET.Element) -> ParaProbe:
    """``<w:p>`` → :class:`ParaProbe` (텍스트 + 스타일/굵게/크기/들여쓰기)."""
    text = _runs_text(p)

    style_name = None
    list_item = False
    indent = 0
    pPr = p.find(_w("pPr"))
    if pPr is not None:
        pStyle = pPr.find(_w("pStyle"))
        if pStyle is not None:
            style_name = pStyle.get(_w("val"))
        list_item = pPr.find(_w("numPr")) is not None
        ind = pPr.find(_w("ind"))
        if ind is not None:
            # w:start 는 w:left 의 신형 이름 — 둘 다 나타난다.
            indent = _twips_to_cols(ind.get(_w("left")) or ind.get(_w("start")))

    bold, size = _first_run_format(p)
    return ParaProbe(
        text=text, style_name=style_name, bold=bold, font_size=size,
        list_item=list_item, indent=indent,
    )
```

- [ ] **Step 5: 블록까지 흐르게 한다**

`contentcompare/raw/models.py` — `RawWordBlock` 의 `lines` 선언 **아래**에 추가:

```python
    indent: int = 0
    """문단 자체의 들여쓰기 칸 수(type=paragraph). 0 이면 ``to_dict`` 에서 생략한다."""
```

같은 클래스 `to_dict()` 의 dict 리터럴에서 `"lines": ...` **다음 줄**에 추가:

```python
                "indent": self.indent or None,
```

`contentcompare/raw/word_raw.py` — `build_word_doc` 의 `RawWordBlock(...)` 문단 분기에서 `lines=_split_lines(...)` **다음 줄**에 추가:

```python
                    indent=p.indent,
```

- [ ] **Step 6: 통과를 확인한다**

Run: `python -m pytest tests/test_raw_word.py tests/test_raw_word_lines.py -v`
Expected: PASS

- [ ] **Step 7: 커밋**

```bash
git add contentcompare/raw/models.py contentcompare/raw/word_raw.py tests/test_raw_word.py
git commit -m "feat(raw): 문단 들여쓰기(<w:ind>) 를 읽는다

지금까지 아예 안 읽던 값이다. 문단이 통째로 밀려 있으면 텍스트 안에 공백이 없어
_indent_width 로는 안 잡힌다 — 별도 문단으로 갈라진 연속행이 이 경로로만 드러난다.
w:start 는 w:left 의 신형 이름이라 둘 다 읽는다."
```

---

### Task 3: 표 셀 줄 보존 (`cell_lines`)

**Files:**
- Modify: `contentcompare/raw/word_raw.py` (`TableProbe`, `_parse_table`, `_cell_lines`, `parse_word_xml`, `build_word_doc`)
- Modify: `contentcompare/raw/models.py` (`RawWordBlock`)
- Test: `tests/test_raw_word.py`, `tests/test_raw_compact.py`

**Interfaces:**
- Consumes: 없음
- Produces: `RawWordBlock.cell_lines: Optional[list[list[list[str]]]]` — `rows`(행×열)에 한 겹 더한 **행 × 열 × 줄**. 줄이 1개뿐인 셀은 `[]`(빈 리스트)로 둬서 `if cell_lines[r][c]` 한 줄로 "여러 줄인 셀"을 가려낸다. 전 셀이 1줄이면 필드 자체가 `None`. Task 5·8 이 읽는다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_raw_word.py` 맨 아래에 추가(`_doc_xml` 은 Task 2 에서 만든 것을 재사용):

```python
_TBL_MULTILINE = """
<w:tbl><w:tblGrid><w:gridCol/><w:gridCol/></w:tblGrid>
  <w:tr>
    <w:tc><w:p><w:r><w:t>충전 프로토콜</w:t></w:r></w:p></w:tc>
    <w:tc>
      <w:p><w:r><w:t>-5~5도씨, 0.1C</w:t><w:br/><w:t>5~12도씨, 0.3C</w:t></w:r></w:p>
      <w:p><w:r><w:t>12~15도씨, 0.8C</w:t></w:r></w:p>
    </w:tc>
  </w:tr>
</w:tbl>
"""


def test_table_cell_lines_are_preserved():
    """셀 안 <w:br/> 과 여러 <w:p> 가 줄 목록으로 남는다."""
    from contentcompare.raw.word_raw import build_word_doc, parse_word_xml

    doc = build_word_doc("t.docx", parse_word_xml(_doc_xml(_TBL_MULTILINE)))
    block = doc.blocks[0]

    assert block.type == "table"
    assert block.cell_lines[0][0] == []          # 1줄뿐인 셀은 빈 리스트
    assert block.cell_lines[0][1] == [
        "-5~5도씨, 0.1C", "5~12도씨, 0.3C", "12~15도씨, 0.8C",
    ]


def test_table_rows_stay_flattened():
    """rows 는 compact 로 나가므로 절대 바뀌면 안 된다(결정 0 회귀 테스트)."""
    from contentcompare.raw.word_raw import build_word_doc, parse_word_xml

    doc = build_word_doc("t.docx", parse_word_xml(_doc_xml(_TBL_MULTILINE)))

    assert doc.blocks[0].rows == [[
        "충전 프로토콜",
        "-5~5도씨, 0.1C 5~12도씨, 0.3C 12~15도씨, 0.8C",
    ]]


def test_table_without_multiline_cells_has_no_cell_lines():
    """모든 셀이 1줄이면 필드를 아예 싣지 않는다 — physical_raw 를 부풀리지 않는다."""
    from contentcompare.raw.word_raw import build_word_doc, parse_word_xml

    xml = _doc_xml(
        "<w:tbl><w:tblGrid><w:gridCol/></w:tblGrid>"
        "<w:tr><w:tc><w:p><w:r><w:t>a</w:t></w:r></w:p></w:tc></w:tr></w:tbl>"
    )
    block = build_word_doc("t.docx", parse_word_xml(xml)).blocks[0]

    assert block.cell_lines is None
    assert "cell_lines" not in block.to_dict()
```

`tests/test_raw_compact.py` 맨 아래에 추가:

```python
def test_compact_word_ignores_cell_lines():
    """cell_lines 가 있어도 compact 출력은 그대로다(결정 0)."""
    from contentcompare.raw.compact import compact_word
    from contentcompare.raw.models import RawWordBlock, RawWordDocument

    doc = RawWordDocument(file_name="t.docx")
    doc.blocks.append(RawWordBlock(
        block_id="w_b001", order=1, type="table",
        rows=[["a", "b c"]],
        cell_lines=[[[], ["b", "c"]]],
    ))

    assert compact_word(doc)["blocks"] == [
        {"id": "w_b001", "type": "table", "rows": [["a", "b c"]]}
    ]
```

- [ ] **Step 2: 실패를 확인한다**

Run: `python -m pytest tests/test_raw_word.py -k table_cell -v`
Expected: FAIL — `AttributeError: 'RawWordBlock' object has no attribute 'cell_lines'`

- [ ] **Step 3: 모델에 필드를 더한다**

`contentcompare/raw/models.py` — `RawWordBlock` 의 `rows` 선언 **아래**에 추가:

```python
    cell_lines: Optional[list[list[list[str]]]] = None
    """표 셀의 줄 목록(type=table). ``rows``(행×열)에 한 겹 더한 **행 × 열 × 줄**.

    줄이 하나뿐인 셀은 ``[]`` 로 둔다 — 원소 1개짜리 리스트로 채우면 "여러 줄인
    셀"을 가려내는 판정이 길이 비교가 되어야 하는데, 빈 리스트면 ``if`` 한 줄로
    끝난다. 전 셀이 1줄이면 필드 자체가 ``None`` 이라 ``physical_raw`` 가 안 커진다.

    ⚠️ ``rows`` 의 셀 문자열은 **여기 있는 줄을 공백으로 이어 붙인 값 그대로** 둔다.
    그 값이 ``compact_raw`` 로 나가기 때문이다(설계 결정 0).
    """
```

같은 클래스 `to_dict()` 의 dict 리터럴에서 `"rows": self.rows,` **다음 줄**에 추가:

```python
                "cell_lines": self.cell_lines,
```

- [ ] **Step 4: 파서가 셀 줄을 만들게 한다**

`contentcompare/raw/word_raw.py` — `TableProbe` 를 아래로 교체:

```python
@dataclass
class TableProbe:
    """표 1개(셀 텍스트 2D, 병합은 이미 채워진 상태)."""

    rows: list[list[str]] = field(default_factory=list)
    cell_lines: list[list[list[str]]] = field(default_factory=list)
    """``rows`` 와 같은 모양에 한 겹 더한 행 × 열 × 줄."""
```

`_cell_text` **바로 아래**에 추가:

```python
def _cell_lines(tc: ET.Element) -> list[str]:
    """셀 안 줄 목록. 문단이 여럿이거나 ``<w:br/>`` 이 있으면 여러 줄이 된다.

    :func:`_cell_text` 는 이 줄들을 공백으로 이어 붙인 값을 만드는데, 그 값은
    ``compact_raw`` 로 나가므로 **바꾸지 않는다**(설계 결정 0). 원문 구조는 여기서
    따로 남긴다 — 한 셀에 조건표가 통째로 들어간 문서를 위한 것이다.
    """
    out: list[str] = []
    for p in tc.findall(_w("p")):
        for piece in _runs_text(p).splitlines():
            s = piece.strip()
            if s:
                out.append(s)
    return out
```

`_parse_table` 의 시그니처와 본문을 아래로 교체(반환이 튜플이 된다):

```python
def _parse_table(tbl: ET.Element) -> tuple[list[list[str]], list[list[list[str]]]]:
    """``<w:tbl>`` → (셀 텍스트 2D, 셀 줄 3D). 가로/세로 병합을 모두 채운다.

    각 행의 ``<w:tc>`` 를 왼쪽부터 순회하며 현재 그리드 컬럼을 ``gridSpan`` 만큼
    전진시킨다. ``vMerge`` 연속 셀은 같은 컬럼의 시작 셀 값을 상속한다 — **줄 목록도
    똑같이 상속**해야 두 표현이 어긋나지 않는다.
    """
    rows_el = tbl.findall(_w("tr"))
    if not rows_el:
        return [], []

    grid = tbl.find(_w("tblGrid"))
    n_cols = len(grid.findall(_w("gridCol"))) if grid is not None else 0
    if n_cols == 0:
        n_cols = max(
            (sum(_grid_span(tc) for tc in tr.findall(_w("tc"))) for tr in rows_el),
            default=0,
        )
    if n_cols == 0:
        return [], []

    vmerge_text: list[Optional[str]] = [None] * n_cols
    vmerge_lines: list[Optional[list[str]]] = [None] * n_cols
    out: list[list[str]] = []
    out_lines: list[list[list[str]]] = []
    for tr in rows_el:
        row_vals = [""] * n_cols
        row_lines: list[list[str]] = [[] for _ in range(n_cols)]
        col = 0
        for tc in tr.findall(_w("tc")):
            span = _grid_span(tc)
            vmerge = _vmerge_state(tc)

            if vmerge == "continue":
                text = (vmerge_text[col] if col < n_cols else "") or ""
                lines = list(vmerge_lines[col] or []) if col < n_cols else []
            else:
                text = _cell_text(tc)
                lines = _cell_lines(tc)
                for k in range(col, min(col + span, n_cols)):
                    vmerge_text[k] = text if vmerge == "restart" else None
                    vmerge_lines[k] = lines if vmerge == "restart" else None

            for k in range(col, min(col + span, n_cols)):
                row_vals[k] = text
                row_lines[k] = list(lines)
            col += span
        out.append(row_vals)
        out_lines.append(row_lines)
    return out, out_lines
```

`parse_word_xml` 의 표 분기를 교체:

```python
        elif el.tag == _w("tbl"):
            rows, cell_lines = _parse_table(el)
            probes.append(TableProbe(rows=rows, cell_lines=cell_lines))
```

- [ ] **Step 5: 빌더가 여러 줄인 셀만 남기게 한다**

`contentcompare/raw/word_raw.py` — `build_word_doc` 의 `TableProbe` 분기를 아래로 교체:

```python
        elif isinstance(p, TableProbe):
            rows = [[" ".join((c or "").split()) for c in row] for row in p.rows]
            if not rows or all(not any(r) for r in rows):
                continue
            # 줄이 하나뿐인 셀은 빈 리스트로 둔다 — rows 가 이미 같은 내용을 담고
            # 있어 두 번 실을 이유가 없다. 전 셀이 그러면 필드 자체를 생략한다.
            cell_lines = [
                [list(lines) if len(lines) > 1 else [] for lines in row]
                for row in (p.cell_lines or [])
            ]
            order += 1
            doc.blocks.append(
                RawWordBlock(
                    block_id=f"w_b{order:03d}",
                    order=order,
                    type="table",
                    rows=rows,
                    cell_lines=cell_lines if any(any(r) for r in cell_lines) else None,
                )
            )
```

- [ ] **Step 6: 통과를 확인한다**

Run: `python -m pytest tests/test_raw_word.py tests/test_raw_compact.py tests/test_raw_ppt.py -v`
Expected: PASS

- [ ] **Step 7: 전체 회귀를 돌린다**

Run: `python -m pytest -q`
Expected: 기존 828건 + 신규 전부 PASS. `_parse_table` 반환이 튜플이 됐으므로 다른 호출부가 있으면 여기서 드러난다.

- [ ] **Step 8: 커밋**

```bash
git add contentcompare/raw/models.py contentcompare/raw/word_raw.py tests/test_raw_word.py tests/test_raw_compact.py
git commit -m "feat(raw): 표 셀 안 줄을 cell_lines 로 보존

한 셀에 조건표가 통째로 들어간 문서(w_b289)에서 _cell_text 의 공백 병합이 다섯
구간을 한 문자열로 만든다. rows 는 compact 로 나가므로 그대로 두고, 줄 구조만
따로 남긴다. vMerge 연속 셀은 줄 목록도 함께 상속해야 두 표현이 안 어긋난다.

줄이 1개뿐인 셀은 빈 리스트로 둔다 — 여러 줄인 셀을 if 한 줄로 가려내려는 것이다."
```

---

### Task 4: `Fact.inherited_from` 필드

**Files:**
- Modify: `contentcompare/fact/fact_models.py` (`Fact`)
- Test: `tests/test_fact_fact_models.py`

**Interfaces:**
- Consumes: 없음
- Produces: `Fact.inherited_from: list[str]` — 다른 블록·줄의 레이블을 이어받아 조건을 채웠을 때 그 `[id]` 목록. `to_dict`/`from_dict`/`from_llm` 왕복. Task 6(프롬프트)이 채우고 Task 9(계측)가 센다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_fact_fact_models.py` 맨 아래에 추가:

```python
def test_fact_roundtrips_inherited_from():
    """LLM 이 표시한 상속 출처가 산출물까지 왕복한다."""
    from contentcompare.fact.fact_models import Fact

    fact = Fact.from_llm({
        "entity_name": "충전온도범위",
        "attributes": {"lower_limit": {"value": 15, "unit": "℃"}},
        "inherited_from": ["w_b245", 7, "", None],
    })

    assert fact.inherited_from == ["w_b245", "7"]      # 문자열화 + 빈 값 제거
    assert fact.to_dict()["inherited_from"] == ["w_b245", "7"]
    assert Fact.from_dict(fact.to_dict()).inherited_from == ["w_b245", "7"]


def test_fact_without_inherited_from_defaults_empty():
    """옛 산출물을 읽어도 깨지지 않는다."""
    from contentcompare.fact.fact_models import Fact

    assert Fact.from_dict({"entity_name": "x"}).inherited_from == []
```

- [ ] **Step 2: 실패를 확인한다**

Run: `python -m pytest tests/test_fact_fact_models.py -k inherited -v`
Expected: FAIL — `AttributeError: 'Fact' object has no attribute 'inherited_from'`

- [ ] **Step 3: 필드를 더한다**

`contentcompare/fact/fact_models.py` — `Fact` 의 `confidence: float = 0.0` **아래**에 추가:

```python
    inherited_from: list[str] = field(default_factory=list)
    """다른 블록·줄의 레이블을 이어받아 조건을 채웠으면 그 ``[id]`` 들.

    ``decided_by``·``quote_verified`` 와 같은 원리다 — **추론한 것은 추론했다고
    남긴다.** 사람이 이 목록이 붙은 fact 만 골라 전수 검수할 수 있어야, LLM 에게
    상속을 허용한 대가를 관리할 수 있다.
    """
```

같은 클래스 `to_dict()` 의 dict 리터럴에서 `"confidence": self.confidence,` **다음 줄**에 추가:

```python
            "inherited_from": self.inherited_from,
```

`from_dict()` 의 `cls(...)` 호출에서 `confidence=_as_float(d.get("confidence")),` **다음 줄**에 추가:

```python
            inherited_from=[
                _as_str(i) for i in (d.get("inherited_from") or []) if _as_str(i)
            ],
```

- [ ] **Step 4: 통과를 확인한다**

Run: `python -m pytest tests/test_fact_fact_models.py -v`
Expected: PASS

- [ ] **Step 5: 커밋**

```bash
git add contentcompare/fact/fact_models.py tests/test_fact_fact_models.py
git commit -m "feat(fact): Fact.inherited_from — 이어받은 조건의 출처를 남긴다

LLM 에게 '앞의 레이블을 이어받아라'를 허용하는 대가는 잘못 이어받을 위험이다.
그 위험을 관리하려면 이어받은 fact 만 골라 검수할 수 있어야 한다."
```

---

### Task 5: F3 렌더 — 문단 줄·표 행

**Files:**
- Modify: `contentcompare/fact/prompts.py` (`_render_unit`, 신규 `_render_table`·`_cell_lines_at`)
- Test: `tests/test_fact_prompts.py`

**Interfaces:**
- Consumes: unit dict 의 **선택 키** — `lines: list[{raw_text: str, indent: int}]`(문단), `indent: int`(문단 블록), `cell_lines: list[list[list[str]]]`(표), `context: bool`(Task 7). 없으면 기존 동작.
- Produces: `_render_unit(u) -> str` — 여러 줄이면 개행이 포함된 문자열. `build_fact_user` 는 그대로 `"\n".join(...)` 으로 잇는다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_fact_prompts.py` 맨 아래에 추가:

```python
def test_render_unit_single_line_is_unchanged():
    """줄이 1개면 기존 형태 그대로 — 대부분의 블록은 출력이 안 바뀐다."""
    from contentcompare.fact.prompts import _render_unit

    assert _render_unit({"id": "w_b001", "type": "text", "text": "공칭전압은 3.85V"}) == (
        "[w_b001] 공칭전압은 3.85V"
    )


def test_render_unit_expands_paragraph_lines_with_indent():
    """여러 줄이면 줄마다 펼치고 들여쓰기를 살린다."""
    from contentcompare.fact.prompts import _render_unit

    out = _render_unit({
        "id": "w_b246", "type": "text", "text": "1.1C(4.28V) 0.8C(4.55V)",
        "lines": [
            {"raw_text": "1.1C(4.28V)", "indent": 11},
            {"raw_text": "0.8C(4.55V)", "indent": 11},
        ],
    })

    assert out.splitlines() == [
        "[w_b246]            1.1C(4.28V)",
        "                    0.8C(4.55V)",
    ]


def test_render_unit_caps_absurd_indent():
    """비정상적으로 큰 들여쓰기가 프롬프트를 망가뜨리지 않게 자른다."""
    from contentcompare.fact.prompts import _RENDER_INDENT_CAP, _render_unit

    out = _render_unit({
        "id": "w_b001", "type": "text", "text": "a b",
        "lines": [{"raw_text": "a", "indent": 0}, {"raw_text": "b", "indent": 9999}],
    })

    assert out.splitlines()[1].count(" ") <= len("[w_b001] ") + _RENDER_INDENT_CAP


def test_render_unit_table_is_row_wise():
    """표는 파이썬 repr 이 아니라 행 단위로 렌더한다."""
    from contentcompare.fact.prompts import _render_unit

    out = _render_unit({
        "id": "w_b289", "type": "table",
        "rows": [["항목", "-5~5도씨, 0.1C 5~12도씨, 0.3C"]],
        "cell_lines": [[[], ["-5~5도씨, 0.1C", "5~12도씨, 0.3C"]]],
    })

    assert out.splitlines() == [
        "[w_b289] 표 (1행 × 2열)",
        "  행1 | 항목",
        "      | -5~5도씨, 0.1C",
        "        5~12도씨, 0.3C",
    ]


def test_render_unit_table_without_cell_lines_still_row_wise():
    """cell_lines 가 없어도(옛 산출물) 행 단위로 낸다 — repr 은 쓰지 않는다."""
    from contentcompare.fact.prompts import _render_unit

    out = _render_unit({"id": "w_b012", "type": "table", "rows": [["a", "b"], ["c", "d"]]})

    assert out.splitlines() == [
        "[w_b012] 표 (2행 × 2열)",
        "  행1 | a",
        "      | b",
        "  행2 | c",
        "      | d",
    ]
```

- [ ] **Step 2: 실패를 확인한다**

Run: `python -m pytest tests/test_fact_prompts.py -k render_unit -v`
Expected: FAIL — 표 테스트는 `표 [['a', 'b'], ...]` 를 받아 불일치, 줄 테스트는 한 줄로 나와 불일치

- [ ] **Step 3: 렌더를 다시 쓴다**

`contentcompare/fact/prompts.py` — 기존 `_render_unit` 전체를 아래로 교체:

```python
_RENDER_INDENT_CAP = 40
"""렌더에서 허용할 최대 들여쓰기 칸 수.

원문에 비정상적으로 큰 들여쓰기가 있으면 한 줄이 화면을 넘어가 LLM 이 구조를 오히려
더 못 읽는다. 자르는 것이 정보 손실이지만, 여기서 필요한 것은 **줄끼리의 상대 위치**라
상한을 둬도 그 신호는 남는다.
"""


def _render_unit(u: dict) -> str:
    """블록/도형 하나를 프롬프트 한 덩어리로. 여러 줄이면 개행이 들어간다.

    **코드는 여기서 아무 판단도 하지 않는다** — 원문 구조를 덜 훼손해 옮길 뿐이고,
    "저 줄들이 앞 레이블에 딸린 것인가"는 LLM 이 본다(설계 §5).
    """
    uid = u.get("id")
    loc = f" slide={u['slide_no']}" if u.get("slide_no") else ""
    note = " (스피커노트)" if u.get("is_note") else ""
    tag = "[맥락]" if u.get("context") else ""
    head = f"{tag}[{uid}]{loc}{note}"

    if u.get("type") == "table":
        return f"{head} {_render_table(u)}"

    lines = u.get("lines") or []
    if len(lines) < 2:
        return f"{head} {_as_str(u.get('text'))}"

    block_ind = int(u.get("indent") or 0)
    pad = " " * (len(head) + 1)
    out = []
    for i, ln in enumerate(lines):
        ind = " " * min(block_ind + int(ln.get("indent") or 0), _RENDER_INDENT_CAP)
        out.append((f"{head} " if i == 0 else pad) + ind + _as_str(ln.get("raw_text")))
    return "\n".join(out)


def _render_table(u: dict) -> str:
    """표를 행 단위로. 여러 줄인 셀은 줄을 살린다.

    예전에는 ``표 [['a','b'], …]`` 라는 파이썬 리스트 repr 이었다. 그 모양은 마지막
    칸이 아무리 길어도 **하나의 값**처럼 보여서, 한 셀에 조건표가 들어간 문서에서
    속성이 한 개만 나왔다(설계 §1.2).
    """
    rows = u.get("rows") or []
    cell_lines = u.get("cell_lines") or []
    n_cols = len(rows[0]) if rows else 0
    out = [f"표 ({len(rows)}행 × {n_cols}열)"]
    for r, row in enumerate(rows):
        for c, cell in enumerate(row):
            prefix = f"  행{r + 1} | " if c == 0 else "      | "
            lines = _cell_lines_at(cell_lines, r, c)
            if len(lines) < 2:
                out.append(prefix + _as_str(cell))
                continue
            out.append(prefix + lines[0])
            out.extend("        " + s for s in lines[1:])
    return "\n".join(out)


def _cell_lines_at(cell_lines: Any, r: int, c: int) -> list[str]:
    """``cell_lines[r][c]`` 를 안전하게 꺼낸다. 모양이 어긋나면 빈 리스트."""
    try:
        return list(cell_lines[r][c] or [])
    except (IndexError, KeyError, TypeError):
        return []
```

- [ ] **Step 4: 통과를 확인한다**

Run: `python -m pytest tests/test_fact_prompts.py -v`
Expected: PASS. 기존 프롬프트 테스트가 표 repr 문자열을 기대하고 있었다면 함께 갱신한다 — **기대값만 새 형식으로 바꾸고 검사 의도는 유지**할 것.

- [ ] **Step 5: 커밋**

```bash
git add contentcompare/fact/prompts.py tests/test_fact_prompts.py
git commit -m "feat(fact): F3 입력을 원문 모양대로 렌더 — 문단 줄·표 행

표를 파이썬 리스트 repr 한 줄로 넘기던 것이 문제였다. 마지막 칸이 아무리 길어도
'하나의 값'처럼 보여서, 한 셀에 조건표가 든 문서에서 속성이 한 개만 나왔다.
행 단위로 바꾸고 여러 줄인 셀은 줄을 살린다.

줄이 1개인 블록·셀은 출력이 그대로라 대부분의 블록은 안 바뀐다."
```

---

### Task 6: 프롬프트 재작성 + `FACT_VERSION` 상향

**Files:**
- Modify: `contentcompare/fact/prompts.py` (`FACT_SYSTEM`, `FACT_VERSION`)
- Test: `tests/test_fact_prompts.py`

**Interfaces:**
- Consumes: Task 4 의 `Fact.inherited_from`
- Produces: `FACT_VERSION == "fact-v3"` — Task 8 의 지문이 이 값을 쓴다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_fact_prompts.py` 맨 아래에 추가:

```python
def test_fact_system_requires_per_condition_attributes():
    """조건이 여럿이면 fact 를 나누지 말고 속성을 나누라고 지시해야 한다."""
    from contentcompare.fact.prompts import FACT_SYSTEM

    assert "의미 경계가 아닙니다" in FACT_SYSTEM
    assert "fact 를 나누지 말고" in FACT_SYSTEM
    assert "inherited_from" in FACT_SYSTEM
    # 분해 방향을 넣지 않는다(설계 §1.4-②) — 쪼개면 top_k·동명·과병합을 누른다.
    assert "독립된 fact" not in FACT_SYSTEM


def test_fact_version_bumped():
    """프롬프트가 바뀌면 버전도 올라야 캐시가 옛 결과를 안 준다."""
    from contentcompare.fact.prompts import FACT_VERSION

    assert FACT_VERSION == "fact-v3"
```

- [ ] **Step 2: 실패를 확인한다**

Run: `python -m pytest tests/test_fact_prompts.py -k "fact_system_requires or version_bumped" -v`
Expected: FAIL — `assert '의미 경계가 아닙니다' in FACT_SYSTEM`, `assert 'fact-v2' == 'fact-v3'`

- [ ] **Step 3: `FACT_SYSTEM` 의 규칙 목록을 교체한다**

`contentcompare/fact/prompts.py` — `FACT_SYSTEM` 의 `규칙:` 블록에서 기존 첫 줄
(`- 흩어진 서술(본문+스피커노트, 표+설명)이 같은 대상이면 하나의 fact 로 병합합니다.`)을
아래 **네 줄**로 교체한다. 나머지 규칙 줄은 그대로 둔다.

```
- 흩어진 서술(본문+스피커노트, 표+설명)이 같은 대상이면 하나의 fact 로 병합합니다.
- 블록·셀 경계는 작성자가 Enter 를 눌렀는지, 표로 그렸는지의 결과일 뿐 의미 경계가
  아닙니다. 레이블이 생략된 줄·블록은 앞의 레이블에 딸린 것일 수 있습니다.
- 한 항목에 조건이 여럿이면(온도 구간별 충전전류 등) fact 를 나누지 말고 하나로 두되,
  조건마다 속성을 나눠 담으세요: charge_temp_range_1 / charge_rate_1 /
  charge_temp_range_2 / … 처럼 번호를 붙입니다. 조건 하나만 담고 나머지를 버리거나,
  값을 한 문자열로 뭉쳐 담으면 둘 다 비교가 불가능해집니다.
- 다른 블록·줄의 레이블을 이어받아 조건을 채웠으면 inherited_from 에 그 [id] 를
  적으세요. 판단이 서지 않으면 이어받지 말고 confidence 를 낮추세요 — 틀린 상속은
  없는 내용을 만들어내는 것이라 누락보다 나쁩니다.
```

같은 문자열의 출력 JSON 예시에서 `"confidence": <0~1 실수>` **앞 줄**에 추가:

```
      "inherited_from": ["<레이블을 이어받은 [id]>"],
```

같은 파일의 버전 상수를 교체:

```python
FACT_VERSION = "fact-v3"  # v3: 블록·셀 경계 재구성 — 조건별 속성 + inherited_from
```

- [ ] **Step 4: 통과를 확인한다**

Run: `python -m pytest tests/test_fact_prompts.py -v`
Expected: PASS

- [ ] **Step 5: 커밋**

```bash
git add contentcompare/fact/prompts.py tests/test_fact_prompts.py
git commit -m "feat(fact): F3 프롬프트에 조건별 속성 요구 + FACT_VERSION fact-v3

기존 규칙에는 병합 방향만 있었다. '한 블록이 여러 조건일 수 있다'는 말이 없어서
LLM 이 한 셀을 하나의 값으로 읽었다. 분해(fact 쪼개기) 방향은 넣지 않는다 —
쪼개면 top_k 경쟁·동명 fact·개념 노드 과병합을 동시에 누른다(설계 §1.4-②).

FACT_VERSION 상향이 전 문서 재추출을 일으키는 스위치이자 롤백 스위치다."
```

---

### Task 7: 배치 맥락 블록

**Files:**
- Modify: `contentcompare/fact/fact_extractor.py` (`_with_context`, `_as_context`, `_facts_from_blocks`)
- Modify: `contentcompare/fact/prompts.py` (`build_fact_user` 헤더)
- Test: `tests/test_fact_extractor.py`

**Interfaces:**
- Consumes: Task 5 의 `_render_unit` 이 읽는 `context: bool` 키
- Produces: `_with_context(batches) -> list[list[dict]]` — 각 배치 앞에 직전 배치의 마지막 3블록을 `context=True` 로 붙인 새 배치 목록.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_fact_extractor.py` 맨 아래에 추가:

```python
def test_with_context_prefixes_previous_tail():
    """두 번째 배치부터 직전 배치의 꼬리 3블록이 맥락으로 붙는다."""
    from contentcompare.fact.fact_extractor import _with_context

    batches = [
        [{"id": f"w_b{i:03d}", "type": "text", "text": "x"} for i in range(1, 6)],
        [{"id": "w_b006", "type": "text", "text": "y"}],
    ]
    out = _with_context(batches)

    assert [u["id"] for u in out[0]] == ["w_b001", "w_b002", "w_b003", "w_b004", "w_b005"]
    assert [u["id"] for u in out[1]] == ["w_b003", "w_b004", "w_b005", "w_b006"]
    assert [u.get("context") for u in out[1]] == [True, True, True, None]


def test_with_context_does_not_mutate_input():
    """원본 unit dict 에 context 를 찍으면 앞 배치의 렌더까지 오염된다."""
    from contentcompare.fact.fact_extractor import _with_context

    first = [{"id": "w_b001", "type": "text", "text": "x"}]
    _with_context([first, [{"id": "w_b002", "type": "text", "text": "y"}]])

    assert "context" not in first[0]


def test_context_table_is_truncated():
    """표를 통째로 맥락에 실으면 배치 토큰을 삼킨다 — 앞 2행만."""
    from contentcompare.fact.fact_extractor import _with_context

    tbl = {"id": "w_b001", "type": "table",
           "rows": [["a"], ["b"], ["c"], ["d"]],
           "cell_lines": [[[]], [[]], [[]], [[]]]}
    out = _with_context([[tbl], [{"id": "w_b002", "type": "text", "text": "y"}]])

    assert out[1][0]["rows"] == [["a"], ["b"]]
    assert len(out[1][0]["cell_lines"]) == 2
    assert tbl["rows"] == [["a"], ["b"], ["c"], ["d"]]      # 원본 불변


def test_context_blocks_cannot_be_fact_sources():
    """맥락 블록만 근거로 든 fact 는 기존 batch_ids 검증이 드롭한다."""
    from contentcompare.fact.fact_extractor import _facts_from_blocks

    compact = {"doc_type": "word", "blocks": [
        {"id": f"w_b{i:03d}", "type": "paragraph", "text": f"내용 {i}"}
        for i in range(1, 4)
    ]}

    class _Runner:
        def __init__(self):
            self.calls = 0

        def complete_json(self, system, user):
            self.calls += 1
            # 두 번째 배치에서 앞 배치(맥락) 블록만 근거로 든 fact 를 낸다.
            if self.calls == 2:
                return {"facts": [{"entity_name": "유령", "source_ids": ["w_b001"]}]}
            return {"facts": []}

    drops = {}
    out = _facts_from_blocks(compact, None, _Runner(), 2, drops)

    assert out.facts == []
    assert drops["dropped_no_valid_source_id"] == 1
```

- [ ] **Step 2: 실패를 확인한다**

Run: `python -m pytest tests/test_fact_extractor.py -k context -v`
Expected: FAIL — `ImportError: cannot import name '_with_context'`

- [ ] **Step 3: 맥락 헬퍼를 만든다**

`contentcompare/fact/fact_extractor.py` — `_pack_batches` **바로 아래**에 추가:

```python
_CONTEXT_BLOCKS = 3
"""배치 앞에 붙일 직전 배치의 꼬리 블록 수.

배치가 20개씩 겹침 없이 잘리므로 **20블록마다 한 번씩** 의미가 경계에서 갈린다.
겹쳐 넣고 사후에 중복을 지우는 방식은 쓰지 않는다 — 중복 판정 기준이 또 하나의
추측이 되기 때문이다. 대신 맥락 블록을 ``batch_ids`` 에서 빼서 원천적으로 막는다.
"""

_CONTEXT_TABLE_ROWS = 2
"""맥락으로 실을 표의 최대 행 수. 표 하나가 배치 토큰을 통째로 삼키는 것을 막는다."""


def _with_context(batches: list[list[dict]]) -> list[list[dict]]:
    """각 배치 앞에 직전 배치의 꼬리 블록을 ``context`` 표시로 덧붙인다.

    원본 unit dict 를 건드리지 않고 **얕은 복사**를 붙인다 — 원본에 표시를 찍으면
    그 블록이 자기 배치에서도 맥락으로 렌더돼 fact 가 통째로 사라진다.
    """
    out: list[list[dict]] = []
    for i, batch in enumerate(batches):
        if i == 0:
            out.append(list(batch))
            continue
        tail = batches[i - 1][-_CONTEXT_BLOCKS:]
        out.append([_as_context(u) for u in tail] + list(batch))
    return out


def _as_context(u: dict) -> dict:
    """맥락용 사본. 표는 앞 몇 행만 남긴다."""
    ctx = dict(u)
    ctx["context"] = True
    if ctx.get("type") == "table":
        if ctx.get("rows"):
            ctx["rows"] = ctx["rows"][:_CONTEXT_TABLE_ROWS]
        if ctx.get("cell_lines"):
            ctx["cell_lines"] = ctx["cell_lines"][:_CONTEXT_TABLE_ROWS]
    return ctx
```

- [ ] **Step 4: 추출 루프가 맥락을 쓰게 한다**

같은 파일 `_facts_from_blocks` 의 배치 루프 첫 두 줄을 교체:

```python
    for batch in _with_context(_pack_batches(groups, batch_blocks)):
        # 맥락 블록은 근거 id 로 인정하지 않는다 — 중복 fact 를 원천 차단한다.
        batch_ids = {u["id"] for u in batch if not u.get("context")}
```

- [ ] **Step 5: 프롬프트 헤더에 맥락 설명을 넣는다**

`contentcompare/fact/prompts.py` — `build_fact_user` 안에서 `header` 를 만든 직후,
`purpose` 처리 **위**에 추가:

```python
    if any(u.get("context") for u in units):
        header += (
            "\n[맥락] 표시가 붙은 블록은 앞에서 이미 처리했습니다 — 앞뒤 관계를 "
            "이해하는 데만 쓰고, 그 블록에 대한 fact 는 만들지 마세요."
        )
```

- [ ] **Step 6: 통과를 확인한다**

Run: `python -m pytest tests/test_fact_extractor.py tests/test_fact_prompts.py -v`
Expected: PASS

- [ ] **Step 7: 커밋**

```bash
git add contentcompare/fact/fact_extractor.py contentcompare/fact/prompts.py tests/test_fact_extractor.py
git commit -m "feat(fact): 배치마다 앞 3블록을 [맥락]으로 덧붙인다

배치가 20개씩 겹침 없이 잘려 20블록마다 한 번씩 의미가 경계에서 갈린다. 맥락
블록을 batch_ids 에서 빼므로 그것만 근거로 든 fact 는 기존 검증이 드롭한다 —
중복 fact 가 원천적으로 안 생긴다. 표는 앞 2행만 실어 토큰 폭발을 막는다."
```

---

### Task 8: 배선과 캐시 지문

**Files:**
- Modify: `contentcompare/fact/fact_extractor.py` (`extract_facts`, `_facts_from_blocks`, `_units_by_group`, 신규 `_lines_index`)
- Modify: `contentcompare/fact/pipeline.py` (Word/PPT F3 호출)
- Test: `tests/test_fact_extractor.py`

**Interfaces:**
- Consumes: Task 1~3 의 `physical_raw` 필드(`lines[].indent`, `indent`, `cell_lines`)
- Produces: `extract_facts(..., lines_by_block: Optional[dict] = None)` — `physical_raw.to_dict()` 를 받는다. `_lines_index(raw) -> dict[str, dict]` 가 렌더에 쓰는 부분만 추려 지문에 섞는다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_fact_extractor.py` 맨 아래에 추가:

```python
_RAW_WITH_LINES = {"blocks": [{
    "block_id": "w_b001", "type": "paragraph", "text": "a b",
    "indent": 3,
    "lines": [{"raw_text": "a", "indent": 0}, {"raw_text": "b", "indent": 11}],
}]}


def test_units_carry_line_structure():
    """physical_raw 를 주면 unit 에 줄 구조가 실린다."""
    from contentcompare.fact.fact_extractor import _units_by_group

    compact = {"doc_type": "word",
               "blocks": [{"id": "w_b001", "type": "paragraph", "text": "a b"}]}
    groups, _ = _units_by_group(compact, lines_by_block=_RAW_WITH_LINES)
    unit = groups[0][0]

    assert unit["indent"] == 3
    assert [l["raw_text"] for l in unit["lines"]] == ["a", "b"]
    assert unit["lines"][1]["indent"] == 11


def test_units_unchanged_without_lines():
    """안 주면 예전 그대로 — PPT·Excel·옛 산출물 경로가 무변경이다."""
    from contentcompare.fact.fact_extractor import _units_by_group

    compact = {"doc_type": "word",
               "blocks": [{"id": "w_b001", "type": "paragraph", "text": "a b"}]}
    groups, _ = _units_by_group(compact)

    assert "lines" not in groups[0][0]
    assert "indent" not in groups[0][0]


def test_lines_index_ignores_single_line_blocks():
    """줄이 1개인 블록은 지문에 넣지 않는다 — 무관한 변경으로 캐시가 깨진다."""
    from contentcompare.fact.fact_extractor import _lines_index

    raw = {"blocks": [{"block_id": "w_b001", "type": "paragraph",
                       "lines": [{"raw_text": "only", "indent": 0}]}]}

    assert _lines_index(raw) == {}


def test_fingerprint_changes_with_line_structure(tmp_path):
    """같은 compact + 다른 줄 정보 → 다른 지문(안 그러면 옛 결과를 준다)."""
    from contentcompare.fact.artifacts import ArtifactStore
    from contentcompare.fact.fact_extractor import extract_facts

    compact = {"doc_type": "word",
               "blocks": [{"id": "w_b001", "type": "paragraph", "text": "a b"}]}

    class _Runner:
        def __init__(self):
            self.calls = 0

        def complete_json(self, system, user):
            self.calls += 1
            return {"facts": [{"entity_name": f"호출{self.calls}",
                               "source_ids": ["w_b001"]}]}

    runner = _Runner()
    store = ArtifactStore(str(tmp_path))
    extract_facts(compact, runner=runner, store=store, lines_by_block=_RAW_WITH_LINES)
    assert runner.calls == 1

    # 같은 입력 → 캐시 히트
    extract_facts(compact, runner=runner, store=store, lines_by_block=_RAW_WITH_LINES)
    assert runner.calls == 1

    # 줄 정보만 바뀜 → 재계산
    changed = {"blocks": [{"block_id": "w_b001", "type": "paragraph",
                           "lines": [{"raw_text": "a", "indent": 0},
                                     {"raw_text": "b", "indent": 20}]}]}
    extract_facts(compact, runner=runner, store=store, lines_by_block=changed)
    assert runner.calls == 2
```

> `ArtifactStore` 의 생성 시그니처가 위와 다르면 `tests/test_fact_artifacts.py` 의 사용법을 그대로 따를 것.

- [ ] **Step 2: 실패를 확인한다**

Run: `python -m pytest tests/test_fact_extractor.py -k "lines or fingerprint" -v`
Expected: FAIL — `TypeError: _units_by_group() got an unexpected keyword argument 'lines_by_block'`

- [ ] **Step 3: 인덱스 헬퍼를 만든다**

`contentcompare/fact/fact_extractor.py` — `_units_by_group` **바로 위**에 추가:

```python
def _lines_index(raw: Optional[dict]) -> dict[str, dict]:
    """``physical_raw`` → ``{block_id: {lines, indent, cell_lines}}``.

    **F3 렌더가 실제로 쓰는 것만 추린다.** 이 값이 캐시 지문에 들어가므로, 렌더에
    안 쓰는 필드까지 넣으면 무관한 변경에도 전 문서가 재추출된다.

    줄이 1개뿐인 블록은 아예 담지 않는다 — 렌더가 그때는 기존 한 줄 형태를 쓰므로
    지문에 넣을 이유가 없다.
    """
    out: dict[str, dict] = {}
    for b in (raw or {}).get("blocks") or []:
        bid = str(b.get("block_id") or "")
        if not bid:
            continue
        entry: dict[str, Any] = {}
        lines = [
            {"raw_text": str(l.get("raw_text") or ""), "indent": int(l.get("indent") or 0)}
            for l in (b.get("lines") or [])
        ]
        if len(lines) > 1:
            entry["lines"] = lines
        if b.get("indent"):
            entry["indent"] = int(b["indent"])
        if b.get("cell_lines"):
            entry["cell_lines"] = b["cell_lines"]
        if entry:
            out[bid] = entry
    return out
```

- [ ] **Step 4: `_units_by_group` 이 줄 구조를 싣게 한다**

같은 파일 `_units_by_group` 의 시그니처와 Word 분기를 교체:

```python
def _units_by_group(
    compact: dict, lines_by_block: Optional[dict] = None
) -> tuple[list[list[dict]], dict[str, dict]]:
```

docstring 끝에 한 문단 추가:

```
    ``lines_by_block`` 에 ``physical_raw`` 를 주면 Word unit 에 줄 구조를 얹는다
    (``lines``/``indent``/``cell_lines``). 안 주면 예전 그대로다 — PPT·Excel·옛
    산출물 경로가 무변경이어야 한다.
```

Word 분기를 교체:

```python
    if doc_type == "word":
        index_lines = _lines_index(lines_by_block)
        for b in compact.get("blocks") or []:
            u = {
                "id": b.get("id"),
                "type": "table" if b.get("type") == "table" else "text",
                "text": b.get("text"),
                "rows": b.get("rows"),
            }
            u.update(index_lines.get(str(b.get("id") or ""), {}))
            groups.append([u])
            index[u["id"]] = u
```

- [ ] **Step 5: `extract_facts` 가 인자를 받고 지문에 섞게 한다**

같은 파일 `extract_facts` 시그니처의 `stats` 파라미터 **아래**에 추가:

```python
    lines_by_block: Optional[dict] = None,
```

docstring 끝에 추가:

```
    ``lines_by_block`` 은 ``physical_raw`` 다(Word 전용). ``build_facts_by_block`` 이
    쓰는 것과 같은 인자이며, **캐시 지문에 반드시 섞는다** — 안 섞으면 같은 compact +
    다른 줄 정보인 두 실행이 캐시를 공유해 옛 결과를 준다.
```

`else:` 분기(Word/PPT)의 `compute`/`fp` 를 교체:

```python
    else:
        def compute() -> dict:
            computed["ran"] = True
            return _facts_from_blocks(
                compact, profile, runner, batch_blocks, drops,
                lines_by_block=lines_by_block,
            ).to_dict()

        lines_payload = _lines_index(lines_by_block)
        payload = json.dumps(compact, ensure_ascii=False)
        if lines_payload:
            payload += json.dumps(lines_payload, ensure_ascii=False, sort_keys=True)
        fp = fingerprint_for(payload, FACT_VERSION) if store else None
```

`_facts_from_blocks` 시그니처에 인자를 더하고 `_units_by_group` 호출을 교체:

```python
def _facts_from_blocks(
    compact: dict,
    profile: Any,
    runner: Any,
    batch_blocks: int,
    drops: Optional[dict] = None,
    *,
    lines_by_block: Optional[dict] = None,
) -> FactSet:
    doc_type = compact.get("doc_type")
    groups, unit_index = _units_by_group(compact, lines_by_block=lines_by_block)
```

- [ ] **Step 6: 파이프라인에서 넘긴다**

`contentcompare/fact/pipeline.py` — Word/PPT F3 호출(`extract_facts(compact, profile=profile, ...)`)에
`stats=fact_stats,` **다음 줄**로 추가:

```python
                        lines_by_block=raw_obj.to_dict(),
```

- [ ] **Step 7: 통과를 확인한다**

Run: `python -m pytest tests/test_fact_extractor.py tests/test_fact_pipeline_smoke.py -v`
Expected: PASS

- [ ] **Step 8: 커밋**

```bash
git add contentcompare/fact/fact_extractor.py contentcompare/fact/pipeline.py tests/test_fact_extractor.py
git commit -m "feat(fact): physical_raw 를 F3 에 별도 인자로 넘기고 지문에 섞는다

compact 에 줄 정보를 실으면 F1 프로파일러의 캐시와 프롬프트까지 흔들린다. 고칠
것은 F3 하나이므로 build_facts_by_block 이 이미 쓰는 패턴을 그대로 쓴다.

새 입력을 지문에 안 섞으면 같은 compact + 다른 줄 정보가 캐시를 공유해 옛 결과를
준다 — 조용히 틀리는 종류라 테스트로 고정한다. 지문에는 렌더가 실제로 쓰는 부분만
넣는다."
```

---

### Task 9: `numeric_coverage` 검사

**Files:**
- Modify: `contentcompare/fact/validator.py` (`_check_numeric_coverage`, `validate_facts`)
- Test: `tests/test_fact_validator.py`

**Interfaces:**
- Consumes: `Fact.attributes`, `Fact.evidence_text`
- Produces: `check="numeric_coverage"`, `severity=WARN` 인 `CheckResult`

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_fact_validator.py` 맨 아래에 추가:

```python
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
```

- [ ] **Step 2: 실패를 확인한다**

Run: `python -m pytest tests/test_fact_validator.py -k numeric_coverage -v`
Expected: FAIL — `ImportError: cannot import name '_check_numeric_coverage'`

- [ ] **Step 3: 검사를 구현한다**

`contentcompare/fact/validator.py` — 파일 상단 import 에 추가(이미 있으면 생략):

```python
import re
```

`_check_attributes` **바로 아래**에 추가:

```python
_NUMERIC_MIN_IN_EVIDENCE = 4
"""근거에 서로 다른 숫자가 이만큼은 있어야 검사를 켠다.

버전 번호(``ver.4.7``)나 조항 번호가 근거에 섞이는 경우가 흔해, 숫자 한둘로 켜면
오탐이 신호를 덮는다.
"""

_NUM_RE = re.compile(r"-?\d+(?:\.\d+)?")


def _check_numeric_coverage(fact: Fact) -> list[CheckResult]:
    """근거에는 숫자가 여럿인데 속성에 수치가 **하나도** 없는가.

    **판단이 아니라 셈이다** — "어느 조건이 맞는가"를 묻지 않고 "근거에 숫자가 몇
    개인데 속성엔 몇 개인가"만 센다.

    이 검사가 필요한 이유는 한 셀·한 문단에 조건이 여럿인데 fact 가 그것을 한
    문자열로 뭉쳐 담으면 **기존 검사가 전부 통과**하기 때문이다. 속성이 0개가 아니라
    ``no_attributes`` 가 안 걸리고, 값이 문자열이라 ``_as_number`` 가 ``None`` 을 줘
    단위 검사도 안 걸리고, 인용은 원문에 실재하니 근거 검사도 통과한다(설계 §1.2).

    ``warn`` 인 이유는 서술형 fact 가 정당하게 걸릴 수 있어서다. 임계를 "수치 0개"로
    좁게 잡은 것도 같은 이유다 — 비율 임계는 데이터가 쌓인 뒤 정할 일이다(YAGNI).
    """
    in_evidence = set(_NUM_RE.findall(fact.evidence_text or ""))
    if len(in_evidence) < _NUMERIC_MIN_IN_EVIDENCE:
        return []
    for attr in (fact.attributes or {}).values():
        if _NUM_RE.search(str(attr.value)):
            return []
    return [CheckResult(
        check="numeric_coverage", severity=WARN, fact_id=fact.fact_id,
        reason=f"근거에 숫자가 {len(in_evidence)}개인데 속성에 수치가 없음(축약 의심)",
        suggestion="조건마다 속성을 나눠 담으세요(charge_temp_range_1 / _2 …).",
    )]
```

`validate_facts` 의 검사 목록에서 `report.checks.extend(_check_attributes(fact))` **다음 줄**에 추가:

```python
        report.checks.extend(_check_numeric_coverage(fact))
```

- [ ] **Step 4: 통과를 확인한다**

Run: `python -m pytest tests/test_fact_validator.py -v`
Expected: PASS

- [ ] **Step 5: 커밋**

```bash
git add contentcompare/fact/validator.py tests/test_fact_validator.py
git commit -m "feat(fact): numeric_coverage — 뭉쳐 담으며 수치를 버린 fact 를 잡는다

한 셀에 조건표가 든 문서에서 fact 가 속성 하나에 문자열로 뭉쳐 담겼는데 기존 검사가
전부 통과했다. 속성이 0개가 아니라 no_attributes 가 안 걸리고, 값이 문자열이라
단위 검사도 안 걸리고, 인용은 원문에 실재하니 근거 검사도 통과한다.

판단이 아니라 셈이다. warn 전용에 숫자 4개 이상 가드를 건다."
```

---

### Task 10: 표 줄 커버리지 + `facts_inherited` 계측

**Files:**
- Modify: `contentcompare/fact/fact_extractor.py` (`build_facts_by_block`, `_line_coverage`, `_facts_from_blocks`)
- Test: `tests/test_fact_facts_by_block.py`

**Interfaces:**
- Consumes: Task 3 의 `cell_lines`, Task 4 의 `Fact.inherited_from`
- Produces: `facts_by_block.json` 의 표 블록에도 `units_in`/`units_linked`/`units_uncited`. `run_stats.json` 에 `facts_inherited`.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_fact_facts_by_block.py` 맨 아래에 추가:

```python
def test_table_cells_join_line_coverage():
    """표도 줄 커버리지 분모에 들어간다 — '표는 이미 최소 단위'가 아니었다."""
    from contentcompare.fact.fact_extractor import build_facts_by_block
    from contentcompare.fact.fact_models import Fact, FactSet

    compact = {"doc_type": "word", "blocks": [
        {"id": "w_b001", "type": "table", "rows": [["항목", "-5~5도씨 5~12도씨"]]},
    ]}
    raw = {"blocks": [{
        "block_id": "w_b001", "type": "table",
        "cell_lines": [[[], ["-5~5도씨", "5~12도씨"]]],
    }]}
    facts = FactSet(facts=[Fact(
        fact_id="f1", entity_name="온도", evidence_text="-5~5도씨",
        source={"doc_type": "word", "block_ids": ["w_b001"]},
    )])

    out = build_facts_by_block(compact, facts, {}, lines_by_block=raw)
    block = out["blocks"][0]

    assert block["units_in"] == 2
    assert block["units_linked"] == 1
    assert block["units_uncited"] == 1
```

`tests/test_fact_extractor.py` 맨 아래에 추가:

```python
def test_facts_inherited_is_counted():
    """상속이 얼마나 일어났는지 세지 않으면 과한지 알 수 없다."""
    from contentcompare.fact.fact_extractor import _facts_from_blocks

    compact = {"doc_type": "word", "blocks": [
        {"id": "w_b001", "type": "paragraph", "text": "a"},
        {"id": "w_b002", "type": "paragraph", "text": "b"},
    ]}

    class _Runner:
        def complete_json(self, system, user):
            return {"facts": [
                {"entity_name": "이어받음", "source_ids": ["w_b002"],
                 "inherited_from": ["w_b001"]},
                {"entity_name": "보통", "source_ids": ["w_b001"]},
            ]}

    drops = {}
    _facts_from_blocks(compact, None, _Runner(), 20, drops)

    assert drops["facts_inherited"] == 1
```

- [ ] **Step 2: 실패를 확인한다**

Run: `python -m pytest tests/test_fact_facts_by_block.py -k table_cells tests/test_fact_extractor.py -k inherited -v`
Expected: FAIL — `KeyError: 'units_in'`, `KeyError: 'facts_inherited'`

- [ ] **Step 3: 표 셀을 줄 커버리지 단위로 편다**

`contentcompare/fact/fact_extractor.py` — `_lines_of` 를 아래로 교체:

```python
def _lines_of(raw: Optional[dict]) -> dict[str, list[dict]]:
    """``physical_raw`` → ``{block_id: [{line_id, raw_text}, …]}``.

    문단은 ``lines``, 표는 ``cell_lines`` 를 편다. 표를 분모에서 빼던 예전 근거는
    *"표는 블록이 이미 최소 단위"* 였는데, **한 셀에 조건표가 통째로 들어간 문서가
    그 가정을 반박했다**(설계 §8.2). 줄이 하나뿐인 셀은 담지 않는다.
    """
    out: dict[str, list[dict]] = {}
    for b in (raw or {}).get("blocks") or []:
        bid = str(b.get("block_id") or "")
        if not bid:
            continue
        lines = b.get("lines")
        if lines:
            out[bid] = list(lines)
            continue
        units: list[dict] = []
        for r, row in enumerate(b.get("cell_lines") or [], start=1):
            for c, cell in enumerate(row or [], start=1):
                for i, text in enumerate(cell or [], start=1):
                    units.append({"line_id": f"{bid}:r{r:02d}c{c:02d}l{i:02d}",
                                  "raw_text": text})
        if units:
            out[bid] = units
    return out
```

- [ ] **Step 4: 상속 수를 센다**

같은 파일 `_facts_from_blocks` 의 `facts.append(fact)` **다음 줄**에 카운터를 더한다.
루프 위쪽 `seen = 0` 옆에 `inherited = 0` 을 선언하고, `facts.append(fact)` 앞에 추가:

```python
            inherited += 1 if fact.inherited_from else 0
```

`drops.update({...})` 딕셔너리에 추가:

```python
            "facts_inherited": inherited,
```

- [ ] **Step 5: 통과를 확인한다**

Run: `python -m pytest tests/test_fact_facts_by_block.py tests/test_fact_extractor.py -v`
Expected: PASS

- [ ] **Step 6: 커밋**

```bash
git add contentcompare/fact/fact_extractor.py tests/test_fact_facts_by_block.py tests/test_fact_extractor.py
git commit -m "feat(fact): 표 셀도 줄 커버리지 분모에 + facts_inherited 계측

표를 분모에서 빼던 근거는 '표는 블록이 이미 최소 단위'였는데, 한 셀에 조건표가
통째로 든 문서가 그 가정을 반박했다. 이제 cell_lines 를 셀 단위로 펴서 센다.

상속을 허용했으므로 얼마나 일어나는지도 세야 한다 — 과한지 알 방법이 없으면
프롬프트 수위를 조정할 근거가 없다."
```

---

### Task 11: 전체 회귀와 골든 대조

**Files:**
- Test: 전체
- Modify: 필요 시 `golden/*.jsonl`(정답이 실제로 바뀌었을 때만)

**Interfaces:**
- Consumes: Task 1~10 전부
- Produces: 없음(검증 태스크)

- [ ] **Step 1: 전체 테스트를 돌린다**

Run: `python -m pytest -q`
Expected: 기존 828건 + 신규 전부 PASS. 실패가 있으면 **여기서 멈추고** 원인을 고친다.

- [ ] **Step 2: compact 바이트 동일을 실측으로 확인한다 (결정 0 의 최종 관문)**

변경 **전에** 저장돼 있던 `compact_raw.json` 의 해시를 먼저 적어 둔다.

```bash
python -c "
import hashlib, sys; sys.stdout.reconfigure(encoding='utf-8')
print(hashlib.sha256(open('artifacts/spec_en_docx/compact_raw.json','rb').read()).hexdigest())
"
```

그 다음 같은 문서로 raw 단계만 다시 만들어 비교한다(LLM 불필요).

```bash
python -c "
import hashlib, json, sys; sys.stdout.reconfigure(encoding='utf-8')
from contentcompare.raw.word_raw import build_word_doc, parse_word_xml
from contentcompare.raw.compact import compact_word
import zipfile
xml = zipfile.ZipFile('samples/spec_en.docx').read('word/document.xml').decode('utf-8')
doc = build_word_doc('spec_en.docx', parse_word_xml(xml))
blob = json.dumps(compact_word(doc), ensure_ascii=False).encode('utf-8')
old = json.load(open('artifacts/spec_en_docx/compact_raw.json', encoding='utf-8'))
new = compact_word(doc)
print('blocks 동일:', old['blocks'] == new['blocks'])
"
```

Expected: `blocks 동일: True`.
**False 면 결정 0 이 깨진 것이다** — Task 1~3 에서 `block.text` 나 표 `rows` 를 건드렸다는
뜻이므로 되짚는다. (`file_name`·`doc_type` 은 호출 방식에 따라 달라질 수 있으므로
`blocks` 만 비교한다.)

- [ ] **Step 3: 샘플로 fact 엔진을 재실행한다**

Run: `contentcompare --config config/config.yaml --engine fact --reference samples/자표준문서.xlsx --targets samples/spec_en.docx --out out/regression.md`

> LLM 이 필요하다. 사내 엔드포인트가 없으면 이 단계는 **사람이 수행**하고, 결과만
> 다음 단계에 넘긴다. `FACT_VERSION` 이 올랐으므로 전 문서가 재추출된다.

- [ ] **Step 4: 골든과 대조한다**

Run: `python scripts/compare_engines.py` (또는 `golden/README.md` 가 안내하는 대조 명령)

확인할 것:

- `golden/spec_en_골든셋.jsonl` 대비 **match/mismatch/missing 분포가 나빠지지 않았는가**
- 특히 `w_b012`(표) 에서 나오던 fact 5건이 유지되는가 — 렌더가 바뀌었으므로 이 표가 회귀의 최전선이다
- `facts_by_block` 의 `units_uncited` 가 **줄었는가**(이 변경의 목적)
- `run_stats` 의 `facts_inherited` 가 비정상적으로 크지 않은가(프롬프트가 과한 신호)
- `validation_report` 의 `numeric_coverage` 경고가 어디에 붙었는가

- [ ] **Step 5: 결과를 기록한다**

`docs/FACT_F3_5_LIVE_REPORT.md` 에 재실행 결과를 한 절로 덧붙인다 — 변경 전후의
`units_uncited`, `facts_inherited`, `numeric_coverage` 경고 수, 골든 분포.

**수치가 나아지지 않았다면 그 사실을 그대로 적는다.** 재추출 비용을 치르고도 효과가
없었다는 것은 설계 가정이 틀렸다는 뜻이고, 그것을 숨기면 다음 사람이 같은 비용을
다시 낸다.

- [ ] **Step 6: 커밋**

```bash
git add docs/FACT_F3_5_LIVE_REPORT.md
git commit -m "docs(report): 블록·셀 경계 재구성 재실행 실측

units_uncited / facts_inherited / numeric_coverage / 골든 분포를 변경 전후로 적는다."
```

---

## Self-Review

**1. 스펙 커버리지**

| 스펙 절 | 태스크 |
|---|---|
| §3 결정 0(compact 불변) | Task 3 Step 1 회귀 테스트 · Task 11 Step 2 해시 대조 |
| §3.1 지문 | Task 8 |
| §4 raw 들여쓰기·`<w:ind>`·셀 줄 | Task 1 · 2 · 3 |
| §5.1 문단 렌더 | Task 5 |
| §5.2 표 렌더 | Task 5 |
| §6 프롬프트 + `FACT_VERSION` | Task 6 |
| §7 배치 맥락(표 2행 절단 포함) | Task 7 |
| §8.1 `numeric_coverage` | Task 9 |
| §8.2 `inherited_from`·`facts_inherited`·표 줄 커버리지 | Task 4 · 10 |
| §9 위험(골든 대조) | Task 11 |
| §11 롤백 | Task 6 커밋 메시지에 스위치 명시 |

**2. 플레이스홀더** — 없음. 모든 코드 단계에 실제 코드가 들어 있다. Task 11 Step 3 은
LLM 이 필요해 사람이 수행할 수 있음을 명시했다(TBD 가 아니라 실행 주체의 문제).

**3. 타입 일관성**

- `RawLine.indent: int` (Task 1) → `_lines_index` 가 `int(l.get("indent") or 0)` 로 읽음 (Task 8) → `_render_unit` 이 `int(ln.get("indent") or 0)` 로 읽음 (Task 5) ✅
- `RawWordBlock.cell_lines: Optional[list[list[list[str]]]]` (Task 3) → `_render_table` 의 `_cell_lines_at` (Task 5) → `_lines_of` 의 표 분기 (Task 10) 모두 행×열×줄 ✅
- `_parse_table` 반환이 튜플로 바뀜 (Task 3) → 호출부는 `parse_word_xml` 하나이며 같은 태스크에서 함께 고침 ✅
- `Fact.inherited_from: list[str]` (Task 4) → `_facts_from_blocks` 의 카운터 (Task 10) ✅
- `_units_by_group(compact, lines_by_block=None)` (Task 8) → `_blocks_of` 도 같은 함수를 호출하는데 **인자 없이** 부르므로 기본값으로 동작 ✅

---

## Execution Handoff

**Plan complete and saved to `docs/superpowers/plans/2026-08-14-word-block-boundary.md`. Two execution options:**

**1. Subagent-Driven (recommended)** - 태스크마다 새 서브에이전트를 띄우고 사이사이 검토, 반복이 빠름

**2. Inline Execution** - 이 세션에서 executing-plans 로 배치 실행, 체크포인트마다 검토

**Which approach?**
