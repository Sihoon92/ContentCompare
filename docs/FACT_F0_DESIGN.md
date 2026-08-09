# Phase F0 상세 설계 — 기반 정비 (Foundation)

> 작성일: 2026-06-29
> 상태: **설계(미구현)** — 본 문서는 F0 "어떻게 만들지"를 파일·인터페이스·테스트 단위로 고정한다.
> 상위 계획: [`FACT_PIPELINE_PLAN.md`](FACT_PIPELINE_PLAN.md) (§9 Phase F0, §10 결정사항)

---

## 0. F0 범위와 목표

상위 계획 §9의 **Phase F0 — 기반 정비**를 구체화한다. F0는 LLM 단계(F1~)를 위한 **토대**만 만든다.

### 0.1 In-scope (F0가 만드는 것)

1. **PPT Raw Extractor** — `.pptx`를 `physical_raw`/`compact_raw` json으로 추출. 결정 #6에 따라 **텍스트박스/표/스피커노트만**(차트·이미지·OCR 제외).
2. **`contentcompare/fact/` 패키지 골격** — `FactPipeline`(워킹 스켈레톤), `ArtifactStore`(중간 산출물 저장·캐싱), `FactConfig`(설정).
3. **엔진 선택 스위치** — CLI `--engine fact|rag`(기본 `rag`, 결정 #1). 기본 동작은 **현행과 100% 동일**.
4. **artifacts 저장 유틸 + `.gitignore`** — 결정 #5(`artifacts/<문서>/<단계>.json` 항상 저장).

### 0.2 Out-of-scope (F1 이후)

- Document Profiler / Schema Inducer / Record Normalizer / Fact Extractor / Validator / Repair / Comparator — **모두 F1~F6**.
- F0의 `FactPipeline`은 **raw 추출 + compact + artifacts 저장**까지만 수행하고, 이후 단계는 `NotImplementedError`(명시적 미구현)로 막아 "골격이 실제 문서에서 동작함"만 증명한다.

### 0.3 제약 (상위 계획 §0 준수)

- 🔒 **RAG 엔진 코드 무수정**: `pipeline.py`, `similarity/`, `comparison/`, `readers/`(=`get_reader`/`DocItem` 경로)는 건드리지 않는다.
- 🔧 **편집 허용(추가만)**: `raw/`(신규 독립 경로 — 계획 §3에서 "PPT 분기 추가"로 명시), `cli.py`(플래그 추가, 기본값 불변), `config.py`(`fact` 블록 추가, 하위호환).
- COM I/O와 순수 빌더를 분리 → **Office 없이 단위테스트** 가능(기존 `excel_raw`/`word_raw` 패턴 그대로).

---

## 1. F0 산출물 — 파일 변경 개요

| 파일 | 구분 | 내용 |
|---|---|---|
| `contentcompare/raw/ppt_raw.py` | ✅ 신규 | `ShapeProbe`/`SlideProbe`/`build_ppt_doc`(순수) + `extract_ppt_raw`(COM) |
| `contentcompare/raw/models.py` | 🔧 추가 | `RawPptShape`/`RawPptSlide`/`RawPptDocument`, `RawDocument` 유니온에 PPT 추가 |
| `contentcompare/raw/extract.py` | 🔧 추가 | `_EXT_MAP`에 `.pptx`/`.ppt` → `ppt` 분기 |
| `contentcompare/raw/compact.py` | 🔧 추가 | `compact_ppt()` + `compact_raw` isinstance 분기 |
| `contentcompare/raw/__init__.py` | 🔧 추가 | PPT 모델/함수 export |
| `contentcompare/fact/__init__.py` | ✅ 신규 | `FactPipeline`, `ArtifactStore`, `make_pipeline` export |
| `contentcompare/fact/artifacts.py` | ✅ 신규 | `ArtifactStore`(save/load/exists/cached_or_compute) |
| `contentcompare/fact/pipeline.py` | ✅ 신규 | `FactPipeline`(F0 워킹 스켈레톤) |
| `contentcompare/fact/engine.py` | ✅ 신규 | `make_pipeline(config, engine)` — 엔진 팩토리(테스트 가능) |
| `contentcompare/config.py` | 🔧 추가 | `FactConfig` dataclass + `AppConfig.fact` 필드(하위호환) |
| `contentcompare/cli.py` | 🔧 추가 | `--engine {rag,fact}` 플래그 + 분기(기본 rag=현행) |
| `.gitignore` | 🔧 추가 | `artifacts/` |
| `tests/test_raw_ppt.py` | ✅ 신규 | PPT 추출/빌더(COM-free) |
| `tests/test_raw_compact.py` | 🔧 추가 | PPT compact 케이스 추가 |
| `tests/test_fact_artifacts.py` | ✅ 신규 | ArtifactStore 단위테스트 |
| `tests/test_fact_pipeline_smoke.py` | ✅ 신규 | FactPipeline 골격(가짜 추출기 주입) |
| `tests/test_fact_engine.py` | ✅ 신규 | 엔진 선택(기본 rag) |

> 신규 의존성 없음 — PPT는 **win32com**(`PowerPoint.Application`)으로 추출한다(`office` extra의 `pywin32`로 충족). 사내 제약상 `python-pptx`는 쓰지 않는다(Word를 `python-docx` 대신 win32com으로 처리한 것과 동일 정책).

---

## 2. PPT Raw Extractor 상세

### 2.1 설계 원칙 (기존 raw 패턴 계승)

- **해석 금지**: 셀이 헤더인지, 도형이 무슨 의미인지 판단하지 않는다. 보이는 물리 정보(슬라이드 번호, 도형 종류·위치·텍스트, 표 셀, 노트)만 담는다.
- **COM 최소화**: `extract_ppt_raw`만 PowerPoint COM을 만지고, 즉시 순수 dataclass(`SlideProbe`/`ShapeProbe`)로 변환한다. 빌더(`build_ppt_doc`)와 compact는 **순수 함수** → Office 없이 테스트.
- **physical은 풍부하게, compact는 노이즈 제거**: 위치(`position`)는 physical_raw엔 보존하되 compact_raw에선 생략(또는 반올림)해 LLM 입력 토큰을 줄인다(기존 Excel side-map 철학과 동일).

### 2.2 데이터 모델 (`raw/models.py` 추가)

`_drop_none` 패턴으로 `None`/기본값은 `to_dict`에서 생략한다(기존 `RawCell`/`RawWordBlock`과 동일).

```python
@dataclass
class RawPptShape:
    """슬라이드 위 도형 1개의 물리 정보(텍스트박스 또는 표)."""

    shape_id: str            # 슬라이드-도형 안정 식별자 (예: "p001_s002")
    order: int               # 슬라이드 내 등장 순서(1-based)
    type: str                # "text" | "table"
    name: Optional[str] = None        # PPT 도형 이름(예: "Title 1") — 구조 힌트
    text: Optional[str] = None        # type=text 의 텍스트(문단 결합)
    rows: Optional[list[list[str]]] = None   # type=table 의 셀 텍스트 2D
    position: Optional[dict[str, float]] = None  # {left,top,width,height} (pt)
    style: Optional[dict[str, Any]] = None       # {placeholder, bold, font_size}

    def to_dict(self) -> dict[str, Any]:
        return _drop_none({
            "shape_id": self.shape_id, "order": self.order, "type": self.type,
            "name": self.name, "text": self.text, "rows": self.rows,
            "position": self.position, "style": self.style,
        })


@dataclass
class RawPptSlide:
    """슬라이드 1장."""

    slide_id: str            # 예: "p001"
    slide_no: int            # 1-based
    layout_name: Optional[str] = None   # 슬라이드 레이아웃 이름(구조 힌트)
    shapes: list[RawPptShape] = field(default_factory=list)
    notes: Optional[str] = None         # 스피커 노트 텍스트

    def to_dict(self) -> dict[str, Any]:
        return _drop_none({
            "slide_id": self.slide_id, "slide_no": self.slide_no,
            "layout_name": self.layout_name,
            "notes": self.notes,
            "shapes": [s.to_dict() for s in self.shapes],
        })


@dataclass
class RawPptDocument:
    """PPT 파일 1개의 raw json 루트."""

    file_name: str
    slides: list[RawPptSlide] = field(default_factory=list)
    doc_type: str = "ppt"

    def to_dict(self) -> dict[str, Any]:
        return {"doc_type": self.doc_type, "file_name": self.file_name,
                "slides": [s.to_dict() for s in self.slides]}
```

`RawDocument` 유니온 문자열을 `"RawExcelDocument | RawWordDocument | RawPptDocument"`로 갱신.

### 2.3 Probe + 순수 빌더 (`raw/ppt_raw.py`)

COM이 채워 주는(또는 테스트가 직접 주입하는) 중간 표현. COM 객체를 들고 있지 않는 순수 데이터다.

```python
@dataclass
class ShapeProbe:
    kind: str                 # "text" | "table" | (그 외는 빌더가 무시 → 차트/이미지 제외)
    name: Optional[str] = None
    text: Optional[str] = None
    rows: Optional[list[list[str]]] = None
    left: Optional[float] = None
    top: Optional[float] = None
    width: Optional[float] = None
    height: Optional[float] = None
    bold: Optional[bool] = None
    font_size: Optional[float] = None
    placeholder: Optional[str] = None   # "title" | "body" | "subtitle" ...

@dataclass
class SlideProbe:
    slide_no: int
    layout_name: Optional[str] = None
    shapes: list[ShapeProbe] = field(default_factory=list)
    notes: Optional[str] = None


def build_ppt_doc(file_name: str, slides: list[SlideProbe]) -> RawPptDocument:
    """probe 리스트 → RawPptDocument. 빈 도형/지원외 종류는 제외, id 부여."""
    doc = RawPptDocument(file_name=file_name)
    for sp in slides:
        slide_no = sp.slide_no
        slide = RawPptSlide(
            slide_id=f"p{slide_no:03d}", slide_no=slide_no,
            layout_name=sp.layout_name,
            notes=_clean(sp.notes) or None,
        )
        order = 0
        for shp in sp.shapes:
            if shp.kind == "text":
                text = _clean(shp.text)
                if not text:
                    continue
                order += 1
                slide.shapes.append(RawPptShape(
                    shape_id=f"{slide.slide_id}_s{order:03d}", order=order,
                    type="text", name=shp.name, text=text,
                    position=_position(shp), style=_style(shp)))
            elif shp.kind == "table":
                rows = [[_clean(c) for c in row] for row in (shp.rows or [])]
                if not rows or all(not any(r) for r in rows):
                    continue
                order += 1
                slide.shapes.append(RawPptShape(
                    shape_id=f"{slide.slide_id}_s{order:03d}", order=order,
                    type="table", name=shp.name, rows=rows,
                    position=_position(shp)))
            # 그 외(kind="chart"/"picture" 등)는 침묵 제외 → 결정 #6.
        # 도형도 노트도 없으면 슬라이드 자체를 생략(노이즈 감소).
        if slide.shapes or slide.notes:
            doc.slides.append(slide)
    return doc
```

보조 순수 함수: `_clean(s)`(공백 정돈, 기존 `" ".join(s.split())`), `_position(shp)`(left/top/width/height가 모두 None이면 None 반환), `_style(shp)`(placeholder/bold/font_size 중 있는 것만, 전부 없으면 None — `word_raw._style_dict` 패턴).

### 2.4 COM 진입점 (`raw/ppt_raw.py`)

`word_raw.extract_word_raw`의 try/finally·`com_util` 사용을 그대로 따른다. `com_util.close_app("ppt", app)`는 비-excel 경로(`app.Quit()`)로 **이미 지원**되므로 `readers/` 수정 불필요.

```python
def extract_ppt_raw(path: str) -> RawPptDocument:
    """pptx 경로 → RawPptDocument (win32com PowerPoint). 텍스트박스/표/노트만."""
    try:
        import pythoncom
        import win32com.client as win32
    except ImportError as exc:  # pragma: no cover - 환경 의존
        raise RuntimeError("pywin32 가 필요합니다(Windows + PowerPoint). pip install pywin32") from exc

    file_name = os.path.basename(path)
    abspath = os.path.abspath(path)
    pythoncom.CoInitialize()
    app = None
    pres = None
    try:
        app = win32.DispatchEx("PowerPoint.Application")
        com_util.track("ppt", app)
        # PowerPoint 는 Visible=False 로 열면 예외가 나는 버전이 있어 WithWindow=False 로 연다.
        pres = app.Presentations.Open(abspath, ReadOnly=True, Untitled=False, WithWindow=False)
        slides = [_probe_slide(s, i) for i, s in enumerate(pres.Slides, start=1)]
        return build_ppt_doc(file_name, slides)
    except Exception:
        logger.exception("[RawPpt] 처리 실패: %s", abspath)
        raise
    finally:
        if pres is not None:
            try: pres.Close()
            except Exception as exc: logger.warning("[RawPpt] pres.Close 실패(무시): %s", exc)
        com_util.close_app("ppt", app)
        try: pythoncom.CoUninitialize()
        except Exception: pass
```

COM→probe 변환(`_probe_slide`, `_probe_shape`, `_notes_text`) 규칙:

| 항목 | COM 접근 | 처리 |
|---|---|---|
| 슬라이드 번호 | `enumerate(pres.Slides, 1)` | `slide_no` |
| 레이아웃 이름 | `slide.CustomLayout.Name` (실패 시 생략) | `layout_name` |
| 텍스트 도형 | `shape.HasTextFrame` & `shape.TextFrame.HasText` | `kind="text"`, `TextRange.Text`(공백 정돈) |
| 표 도형 | `shape.HasTable` | `kind="table"`, `Table.Cell(r,c).Shape.TextFrame.TextRange.Text` 2D |
| 위치 | `shape.Left/Top/Width/Height` (pt) | `position`(반올림 1자리) |
| placeholder | `shape.Type==msoPlaceholder` → `shape.PlaceholderFormat.Type` | `placeholder` 라벨 매핑 |
| 노트 | `slide.NotesPage.Shapes` 중 본문 텍스트 프레임 | `notes`(슬라이드 썸네일 placeholder 제외) |
| **차트/이미지** | `HasChart`/`Type==msoPicture` 등 | **건너뜀(결정 #6)** |

> 표 병합셀: F0는 각 셀 텍스트만 그대로 읽는다(가로/세로 병합 전파는 Word처럼 후속 개선 여지로 남김 — physical_raw 정확도 이슈로 §8 리스크에 기록).

### 2.5 디스패처/compact 연동

- `raw/extract.py` `_EXT_MAP`에 `".pptx": "ppt"`, `".ppt": "ppt"` 추가, `extract_raw`에 `if kind == "ppt": from .ppt_raw import extract_ppt_raw; return extract_ppt_raw(path)` 분기. 미지원 에러 메시지에 PPT 포함.
- `raw/compact.py`에 `compact_ppt(doc)` 추가, `compact_raw`에 `isinstance(doc, RawPptDocument)` 분기.
- `raw_to_json`/`compact_to_json`은 타입 불문(`doc.to_dict()`/`compact_raw(doc)`)이라 **수정 불필요**. `scripts/dump_raw.py`도 자동으로 PPT 지원(인자 help 문구만 선택적 갱신).

`compact_ppt` 규칙(노이즈 제거):

```python
def compact_ppt(doc: RawPptDocument) -> dict[str, Any]:
    slides = []
    for s in doc.slides:
        shapes = []
        for sh in s.shapes:
            if sh.type == "text":
                item = {"id": sh.shape_id, "type": "text", "text": sh.text}
            else:
                item = {"id": sh.shape_id, "type": "table", "rows": sh.rows}
            if sh.name:
                item["name"] = sh.name          # 구조 힌트는 유지
            if sh.style:
                item["style"] = sh.style
            shapes.append(item)                 # position 은 compact 에서 생략
        out = {"slide_no": s.slide_no, "shapes": shapes}
        if s.layout_name:
            out["layout"] = s.layout_name
        if s.notes:
            out["notes"] = s.notes
        slides.append(out)
    return {"doc_type": "ppt", "file_name": doc.file_name, "slides": slides}
```

### 2.6 산출물 예시

physical_raw (발췌):
```json
{ "doc_type": "ppt", "file_name": "deck.pptx",
  "slides": [ { "slide_id": "p001", "slide_no": 1, "layout_name": "Title and Content",
    "notes": "0.1C, 4.55V 조건 기준",
    "shapes": [
      { "shape_id": "p001_s001", "order": 1, "type": "text", "name": "Title 1",
        "text": "충전환경온도", "position": {"left":38.0,"top":30.0,"width":640.0,"height":80.0},
        "style": {"placeholder": "title"} },
      { "shape_id": "p001_s002", "order": 2, "type": "text", "name": "Content",
        "text": "-5~55℃, 중심치 25℃", "position": {"left":38.0,"top":140.0,"width":640.0,"height":300.0} }
    ] } ] }
```
compact_raw (같은 슬라이드, position 제거):
```json
{ "doc_type": "ppt", "file_name": "deck.pptx",
  "slides": [ { "slide_no": 1, "layout": "Title and Content", "notes": "0.1C, 4.55V 조건 기준",
    "shapes": [
      { "id": "p001_s001", "type": "text", "text": "충전환경온도", "name": "Title 1", "style": {"placeholder":"title"} },
      { "id": "p001_s002", "type": "text", "text": "-5~55℃, 중심치 25℃", "name": "Content" }
    ] } ] }
```

---

## 3. `contentcompare/fact/` 패키지 골격

### 3.1 디렉터리

```
contentcompare/fact/
  __init__.py      # FactPipeline, ArtifactStore, make_pipeline 재노출
  artifacts.py     # ArtifactStore — 중간 산출물 저장/로드/캐싱
  pipeline.py      # FactPipeline — F0 워킹 스켈레톤(raw→compact→저장)
  engine.py        # make_pipeline(config, engine) — rag/fact 팩토리
  # models.py      # (F1+에서 추가: document_profile/schema/record/fact 모델)
```

### 3.2 `FactConfig` (`config.py` 추가, 하위호환)

```python
@dataclass
class FactConfig:
    artifacts_dir: str = "artifacts"     # 결정 #5: artifacts/<문서>/<단계>.json
    save_artifacts: bool = True          # 항상 저장(테스트에서 off 가능)
    cache: bool = True                   # 결정 #2: 단계별 산출물 캐싱(재실행 0비용)
    max_llm_calls_per_doc: int = 50      # 결정 #2: 문서당 호출 예산 (F1+ 사용)
    max_repair_iters: int = 2            # F4 Repair Loop 상한 (F1+ 사용)
```

`AppConfig`에 `fact: FactConfig = field(default_factory=FactConfig)` 추가, `from_dict`에 `fact=FactConfig(**data.get("fact", {}) or {})`. **기존 config(`fact:` 없음)는 전부 기본값** → 하위호환. F0는 `artifacts_dir`/`save_artifacts`/`cache`만 사용, 나머지는 F1+용 자리.

### 3.3 `ArtifactStore` (`fact/artifacts.py`)

중간 산출물을 `artifacts/<doc_slug>/<stage>.json`에 **깨끗한 JSON**(다운스트림이 그대로 읽는 순수 데이터)으로 저장한다. 캐시 유효성은 **사이드카** `<stage>.fingerprint`로 분리해 산출물 가독성을 해치지 않는다.

```python
class ArtifactStore:
    def __init__(self, root, doc_name, *, enabled=True, cache=True): ...

    @staticmethod
    def slug(doc_name: str) -> str:
        """basename 의 경로불가 문자/점을 '_' 로 치환(한글 보존). 예: '요약.pptx'→'요약_pptx'."""

    def path(self, stage: str) -> Path:        # root/<slug>/<stage>.json
    def exists(self, stage: str) -> bool
    def save(self, stage: str, data: dict | str) -> Optional[Path]:
        """enabled=False 면 None 반환·파일 없음. dict 는 json.dumps(ensure_ascii=False, indent=2)."""
    def load(self, stage: str) -> Optional[dict]:   # 없으면 None
    def cached_or_compute(self, stage, compute, *, fingerprint=None) -> dict:
        """cache 이고 산출물 존재 + (fingerprint None 또는 사이드카 일치) → load.
        아니면 compute() 호출 → save(+fingerprint 기록) → 반환. (결정 #2의 재실행 0비용 핵심)"""
```

- `fingerprint`: 입력 해시(예: 파일 mtime+size, 또는 상위 단계 산출물 해시). F0에선 raw 추출에 파일 지문을 쓸 수 있으나(아래 §3.4 주석 참조), **주 소비처는 F1+ LLM 단계**(dict→dict).
- 한글 보존(`ensure_ascii=False`)·UTF-8 고정(기존 raw json 직렬화와 일관).

### 3.4 `FactPipeline` 골격 (`fact/pipeline.py`)

F0의 목표는 **"실제 문서에서 raw→compact→artifacts 저장이 끝까지 돈다"**의 증명. LLM 단계는 명시적 미구현.

```python
class FactPipeline:
    def __init__(self, config, *, extractor=None, compactor=None):
        self.config = config
        self.fact = getattr(config, "fact", FactConfig())
        self._extract = extractor or extract_raw       # 테스트는 가짜 주입(COM 회피)
        self._compact = compactor or compact_raw

    def run(self, reference, targets, progress=None):
        docs = [reference, *targets]
        summaries = []
        try:
            for i, path in enumerate(docs, start=1):
                store = ArtifactStore(self.fact.artifacts_dir, os.path.basename(path),
                                      enabled=self.fact.save_artifacts, cache=self.fact.cache)
                raw_obj = self._extract(path)                 # COM(또는 주입)
                store.save("physical_raw", raw_obj.to_dict())
                compact = self._compact(raw_obj)
                store.save("compact_raw", compact)
                summaries.append({"path": path, "doc_type": compact.get("doc_type"),
                                  "artifacts": str(store.path("compact_raw").parent)})
                if progress: progress(i, len(docs), path)
            # --- F1 이후 단계는 아직 없음 ---
            self._not_yet_implemented()
            return summaries
        finally:
            close_all_office()    # readers.com_util.close_all — RAG 파이프라인과 동일한 정리

    def _not_yet_implemented(self):
        raise NotImplementedError(
            "FactPipeline: Profiler~Comparator 는 Phase F1~F6 에서 구현됩니다. "
            "현재(F0)는 raw/compact artifacts 저장까지만 동작합니다.")
```

> 설계 주: F0 파이프라인은 `physical_raw`를 단순 `save`한다(추출 객체를 compact에 그대로 재사용해야 하므로). `cached_or_compute`(재실행 0비용)는 **dict→dict인 F1+ LLM 단계에서 본격 사용**한다 — F0에선 `artifacts.py`에 구현·단위테스트만 완료해 둔다.
>
> `progress` 시그니처: RAG의 `_progress(i, total, result)`와 달리 F0은 결과 객체가 없어 `progress(i, total, path)`로 둔다. F6에서 공통 결과 인터페이스로 수렴할 때 정리한다.

### 3.5 엔진 선택 (`fact/engine.py` + `cli.py`)

테스트 가능하도록 분기 로직을 순수 팩토리로 분리한다.

```python
# fact/engine.py
def make_pipeline(config, engine: str = "rag"):
    """engine 이름 → 파이프라인 인스턴스. 알 수 없으면 ValueError."""
    if engine == "rag":
        from ..pipeline import ComparePipeline   # RAG(현행) — 무수정
        return ComparePipeline(config)
    if engine == "fact":
        from .pipeline import FactPipeline
        return FactPipeline(config)
    raise ValueError(f"알 수 없는 engine: {engine!r} (rag|fact)")
```

`cli.py` 변경(추가만, 기본값 불변):

```python
p.add_argument("--engine", choices=["rag", "fact"], default="rag",
               help="비교 엔진 선택(rag=현행 임베딩, fact=신규 fact 파이프라인). 기본 rag")
...
# main(): ComparePipeline 직접 생성 대신
pipeline = make_pipeline(config, args.engine)
results = pipeline.run(args.reference, args.targets, progress=_progress)
```

> ⚠️ F0에서 `--engine fact` 실행 시 리포트 단계는 아직 없다. F0의 CLI 분기는 fact일 때 **"artifacts 저장 후 미구현 안내"**로 끝낸다(아래 처리). `--engine rag`(기본)은 기존 경로 그대로라 **현행 동작 바이트 동일**.
>
> CLI fact 처리(예외를 사용자 메시지로):
> ```python
> if args.engine == "fact":
>     try:
>         pipeline.run(args.reference, args.targets, progress=_progress)
>     except NotImplementedError as e:
>         log_print(f"[fact 엔진 F0] {e}")
>     log_print("artifacts 저장 완료. fact 비교/리포트는 Phase F1~F6 에서 제공됩니다.")
>     return 0
> ```
> (이 분기는 신규 코드 경로에만 영향. rag 경로의 `render_markdown`/`save_report`는 그대로.)

---

## 4. 신규 공개 인터페이스 요약 (Quick Reference)

| 심볼 | 위치 | 시그니처 |
|---|---|---|
| `RawPptShape/Slide/Document` | `raw/models.py` | dataclass + `to_dict()` |
| `ShapeProbe`/`SlideProbe` | `raw/ppt_raw.py` | dataclass(순수) |
| `build_ppt_doc` | `raw/ppt_raw.py` | `(file_name:str, slides:list[SlideProbe]) -> RawPptDocument` |
| `extract_ppt_raw` | `raw/ppt_raw.py` | `(path:str) -> RawPptDocument` (COM) |
| `compact_ppt` | `raw/compact.py` | `(doc:RawPptDocument) -> dict` |
| `FactConfig` | `config.py` | dataclass |
| `ArtifactStore` | `fact/artifacts.py` | `save/load/exists/path/cached_or_compute/slug` |
| `FactPipeline` | `fact/pipeline.py` | `__init__(config, *, extractor=None, compactor=None)`, `run(reference, targets, progress=None)` |
| `make_pipeline` | `fact/engine.py` | `(config, engine:str="rag") -> ComparePipeline | FactPipeline` |

---

## 5. 테스트 계획 (전부 COM-free, 모든 OS)

기존 원칙(가짜 클라이언트/probe 주입) 준수. `pytest` 한 번에 통과해야 한다.

### 5.1 `tests/test_raw_ppt.py` (신규) — 빌더/모델
probe를 직접 만들어 `build_ppt_doc` 검증(COM 미사용, `excel_raw`/`word_raw` 테스트 패턴).

- `test_text_shape_extracted` — text probe → `type=text`, 텍스트 정돈.
- `test_table_shape_extracted` — table probe → `rows` 2D 보존.
- `test_notes_extracted` — `SlideProbe.notes` → 슬라이드 `notes`.
- `test_empty_text_shape_skipped` — 빈 텍스트 도형 제외.
- `test_empty_slide_dropped` — 도형·노트 모두 없으면 슬라이드 생략.
- `test_chart_picture_kind_excluded` — `kind="chart"/"picture"` probe → 결과에 없음(결정 #6).
- `test_slide_and_shape_ids_and_order` — `p001`, `p001_s001…`, order 1-based.
- `test_position_in_physical_only` — physical_raw엔 `position` 존재.
- `test_no_interpretation_only_physical` — json에 `entity`/`lower_limit` 없음, `shape_id` 있음.
- `test_json_serializable_korean_preserved` — `raw_to_json` 한글 보존, `doc_type=="ppt"`.

### 5.2 `tests/test_raw_compact.py` (추가) — PPT compact
- `_ppt_doc()` 헬퍼(`build_ppt_doc`로 생성).
- `test_ppt_slides_compacted` — slide_no/shapes/notes 보존, text/table 구분.
- `test_ppt_position_dropped_in_compact` — compact json에 `position`/`left` 없음.
- `test_ppt_korean_preserved`.

### 5.3 `tests/test_fact_artifacts.py` (신규) — ArtifactStore (`tmp_path`)
- `test_save_creates_file_utf8` — `root/<slug>/physical_raw.json` 생성, 한글 보존.
- `test_load_roundtrip` — save→load 동일 dict.
- `test_load_missing_returns_none`.
- `test_disabled_store_no_write` — `enabled=False` → save None, 파일 없음.
- `test_slug_sanitizes` — `"요약.pptx"` → `"요약_pptx"`.
- `test_cached_or_compute_skips_recompute` — compute 카운터, 2회차 캐시 히트(호출 0).
- `test_cached_or_compute_fingerprint_mismatch_recomputes` — 지문 변경 시 재계산.
- `test_cache_off_always_computes` — `cache=False` → 매번 compute.

### 5.4 `tests/test_fact_pipeline_smoke.py` (신규) — 골격
가짜 `extractor`(COM 회피)로 미리 만든 `RawExcelDocument`(`build_raw_sheet`) 반환.
- `test_artifacts_written` — `physical_raw.json`/`compact_raw.json` 두 파일 생성(reference+targets 각 폴더).
- `test_run_raises_not_implemented_for_llm_stages` — F1+ 단계에서 `NotImplementedError`(또는 CLI 래퍼가 잡는 신호) 확인.
- `test_save_artifacts_false` — 저장 off면 파일 없음.
- `test_close_office_called` — `finally` 정리 호출(가짜 `close_all` 패치로 호출 여부).

### 5.5 `tests/test_fact_engine.py` (신규) — 엔진 선택
- `test_default_engine_is_rag` — `build_parser().parse_args([...]).engine == "rag"`.
- `test_make_pipeline_rag` — `make_pipeline(cfg,"rag")` → `ComparePipeline` 인스턴스.
- `test_make_pipeline_fact` — `make_pipeline(cfg,"fact")` → `FactPipeline` 인스턴스.
- `test_make_pipeline_unknown_raises` — `ValueError`.

> RAG 회귀 방지: 기존 `tests/test_pipeline_smoke.py`가 변경 없이 그대로 통과해야 한다(엔진 기본=rag이므로 영향 없음).

---

## 6. 공존·안전성 점검

- **RAG 무수정 확인**: `pipeline.py`/`similarity/`/`comparison/`/`readers/(get_reader 계열)` 변경 0. `make_pipeline`은 `ComparePipeline`을 **호출만** 한다.
- **기본 동작 불변**: `--engine` 기본 `rag` → 기존 CLI 명령은 동일 경로·동일 출력.
- **config 하위호환**: `fact:` 블록 없는 기존 `config.yaml`도 `FactConfig` 기본값으로 동작.
- **테스트 격리**: PPT/fact 신규 테스트는 모두 probe/가짜 주입 → Office·네트워크 불필요(CLAUDE.md 규칙 준수).
- **COM 정리 일관**: `extract_ppt_raw`는 `com_util.track/close_app("ppt",…)` + `FactPipeline.run`의 `finally: close_all_office()`로 고아 프로세스 방지.

---

## 7. 구현 순서(체크리스트) & 완료 기준(DoD)

순서(의존도 낮은 것부터, 각 단계 TDD):
1. `raw/models.py` PPT 모델 + `__init__` export → 직렬화 단위테스트.
2. `raw/ppt_raw.py` probe/`build_ppt_doc`(순수) → `test_raw_ppt.py`.
3. `raw/extract.py`/`compact.py` PPT 분기 → `test_raw_compact.py` 확장.
4. `extract_ppt_raw`(COM) — 실기기 수동 검증(Windows+PowerPoint), CI/단위테스트는 순수부만.
5. `fact/artifacts.py` → `test_fact_artifacts.py`.
6. `config.py` `FactConfig` → 로드 하위호환 확인.
7. `fact/pipeline.py`(골격) + `fact/engine.py` → `test_fact_pipeline_smoke.py`.
8. `cli.py` `--engine` + 분기 → `test_fact_engine.py`.
9. `.gitignore`에 `artifacts/`.

**DoD** (2026-06-29 달성):
- [x] `pytest` 전체: F0 신규 36개 전부 통과, RAG 무회귀. (단, `test_local_onnx.py` 1건은 **F0 이전부터 존재한 numpy truth-value 버그** — 본 작업과 무관.)
- [x] (수동, Windows) 실제 `.pptx`(`samples/발표.pptx`)로 `dump_raw.py --compact` → 슬라이드/제목·본문 placeholder/표/노트가 보이는 compact json 출력 확인.
- [x] (수동, Windows) `contentcompare --engine fact --reference samples/기준.xlsx --targets samples/발표.pptx samples/사업보고서.docx` → 3문서 `artifacts/<문서>/physical_raw.json`·`compact_raw.json` 생성 + "F1~F6 예정" 안내.
- [x] `--engine rag`(기본) 동작 동일 — `make_pipeline(config,"rag")`가 동일 `ComparePipeline` 반환, fact 분기는 skip(테스트 `test_fact_engine`·`test_pipeline_smoke`로 보증).
- [x] `FACT_PIPELINE_PLAN.md` §9의 F0 항목에 "완료" 표기 + 본 문서 링크.

> 라이브 검증 중 발견·수정: 노트 페이지의 **슬라이드 번호 placeholder**(`PlaceholderFormat.Type=13`)가 노트에 섞여 들어와, `_notes_text`를 **본문 placeholder(`Type=2`)만** 읽도록 수정함(§8 리스크 "노트 placeholder 식별" 해소).

---

## 8. 리스크 / 미해결

| 항목 | 내용 | 대응 |
|---|---|---|
| PPT 표 병합셀 | F0는 셀 텍스트만 읽음(가로/세로 병합 전파 없음) | physical 정확도 한계 명시. Word 방식(gridSpan/vMerge) 차용한 개선을 후속 이슈로. |
| PowerPoint COM `Open` 옵션 | 일부 버전 `Visible=False`/`WithWindow` 동작 차이 | `WithWindow=False, ReadOnly=True`로 시작, 실기기 검증서 조정. |
| 노트 placeholder 식별 | NotesPage에 슬라이드 썸네일 placeholder가 섞임 | 텍스트 프레임 중 본문만 채택(빈/썸네일 제외) 규칙으로. |
| doc_slug 충돌 | 다른 폴더의 동일 파일명 | F0는 basename 기준(한계 기록). 필요 시 경로 해시 접두 추가. |
| `progress` 시그니처 상이 | RAG=result, fact=path | F6 공통 결과 인터페이스에서 통일. |

---

## 9. 한 줄 요약

> F0는 **PPT raw 추출(텍스트/표/노트) + `fact/` 골격(ArtifactStore·FactPipeline 스켈레톤·`--engine` 스위치)**을 추가해, 현행 RAG를 건드리지 않고 "실제 문서 → 중간 산출물 저장"이 끝까지 도는 토대를 만든다. LLM 단계(F1~)는 이 위에 얹는다.
