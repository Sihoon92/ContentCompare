# ContentCompare 구현 계획 (Implementation Plan)

> 결정 사항 (2026-05-29 브레인스토밍)
> - **엑셀 분해**: `hybrid` — 행 단위 검색 + 셀(필드) 단위 판정
> - **유사 검색**: 하이브리드(임베딩 + BM25 RRF) + MMR + 디스크 캐시 (재랭커는 토글, 기본 off)
> - **프론트엔드**: Streamlit 로컬 앱 (엔진은 CLI/UI 공용)

핵심 관점: **비교 = 사실 검증(fact verification).** 엑셀의 각 필드는 하나의 주장이고,
대상 문서가 이를 same / partial / different / not_found 로 검증한다.

---

## 데이터 흐름 (확정본)

```
기준 엑셀 ──ExcelReader(hybrid)──▶ RecordItem[]            (행 = 레코드)
                                      └ FieldClaim[]        (셀 = 주장, 키컬럼+헤더+값)

대상 문서들 ──Reader──▶ DocItem[] ──chunk──▶ HybridIndex
                                              ├ EmbeddingStore (코사인, 디스크 캐시)
                                              └ BM25Store (lexical)

각 RecordItem:
  ① HybridIndex.search(record.text, recall_k=30)
        → RRF(embedding, bm25) → MMR + per_doc_cap → top_k=10 Candidate
  ② Comparator.compare_record(record, fields, candidates)
        → LLM 1회 호출: 후보 10개 + 필드 목록 투입
        → 필드별 verdict/source/reasoning JSON
  ③ RecordResult(필드별 FieldResult[])

→ MarkdownReport (레코드 섹션 + 필드별 표)
```

행 단위로 **검색은 1회**, 판정은 **필드별**로 분해 → 정밀도와 비용 동시 확보.

---

## Phase 1 — 엑셀 hybrid 분해 + 리더 하드닝 ✅ (완료)

> 구현 요약: COM I/O(`_extract_grid`)와 순수 파싱(`_parse_sheet(SheetGrid)`)을 분리해
> Excel 없이 단위테스트 가능. `tests/test_excel_reader.py` 18케이스 통과.
> 표시문자/병합셀은 COM 계층에서 best-effort 처리(예외 시 폴백).


### 모델 추가 (`models.py`)
```python
@dataclass
class FieldClaim:
    field_id: str          # 기준.xlsx#Sheet1!D5
    header: str            # "매출액"
    value_raw: Any         # 원본 셀 값
    value_norm: str        # 비교용 정규화 문자열(콤마/단위 제거)
    cell_ref: str          # "D5"

@dataclass
class RecordItem(DocItem):  # text=레코드 전체 검색용 텍스트
    key_context: str        # "[제품명=A, 연도=2023]"
    fields: list[FieldClaim]
```

### `config.ExcelConfig` 확장
```yaml
excel:
  granularity: hybrid      # row | field | hybrid
  header_rows: 1
  key_columns: []          # 비면 첫 비어있지 않은 열 자동 추정
  compare_columns: null    # null=키 제외 전체
  skip_columns: []
  value_as_displayed: true
  max_rows: null
```

### `ExcelReader` 작업
- [ ] `header_rows` 다단 헤더 → 헤더 라벨 결합("2024>매출")
- [ ] 키 컬럼으로 `key_context` 생성, 미지정 시 자동 추정(첫 텍스트 열)
- [ ] 각 비교 대상 셀 → `FieldClaim` (cell_ref `xw.utils` 로 A1 표기)
- [ ] `value_as_displayed`: `range.api.Text`(표시문자) vs `.value`(원시값) 동시 보관
- [ ] 숫자 정규화 헬퍼: 콤마/단위/공백 제거, 퍼센트·통화 인식
- [ ] 병합셀: 좌상단 값 채워 내리기(`MergeArea`)
- [ ] COM 정리: `try/finally` 로 book.close/app.quit 보장(예외시 누수 방지)
- [ ] `granularity` 분기: row=기존, field=레코드 없이 FieldClaim 평면화, hybrid=RecordItem+fields

### 테스트
- [ ] 가짜 시트값(2D 리스트) 주입형 단위테스트로 키컨텍스트/필드 생성 검증(Excel 불필요하게 `_read_sheet` 를 값-주입 가능 구조로 분리)

---

## Phase 2 — 하이브리드 검색 + 캐시 ✅ (완료)

> 구현 요약: `tokenize`/`bm25`/`fusion`(RRF)/`mmr`/`cache`/`hybrid_index` 신규.
> 모두 순수 파이썬(numpy 무의존). `HybridIndex` = 임베딩 코사인 + BM25 → RRF → MMR+per_doc_cap.
> `CachedEmbedder` 로 재실행 시 임베딩 0비용. 파이프라인이 `HybridIndex`+캐시 사용하도록 교체.
> `tests/test_hybrid_search.py` 13케이스 통과(어휘매칭/RRF합의/MMR다양성·상한/캐시히트).
> 재랭커(rerank)는 토글만 마련(기본 off) — Phase 2.5.


### 새 모듈 `similarity/`
- [ ] `bm25.py`: 경량 BM25(Okapi). 한글 토크나이즈는 공백+자모 분해 최소화(의존성 없이), 추후 형태소기 교체 가능 인터페이스.
- [ ] `fusion.py`: RRF `score = Σ 1/(k + rank_i)` (k=60 기본).
- [ ] `mmr.py`: MMR 재정렬 + `per_doc_cap`.
- [ ] `cache.py`: 파일 내용 해시 → 임베딩 `.npy`/json 캐시(`cache_dir`). 모델명·청크설정도 키에 포함.
- [ ] `hybrid_index.py` (`VectorIndex` 대체/확장):
  ```python
  class HybridIndex:
      def add(items): ...            # 임베딩(캐시) + BM25 색인 동시 구축
      def search(query, *, recall_k, top_k, mmr_lambda, per_doc_cap) -> list[Candidate]
  ```

### `config.SimilarityConfig` 확장
```yaml
similarity:
  recall_k: 30
  top_k: 10
  fusion: rrf            # cosine | rrf
  rrf_k: 60
  mmr_lambda: 0.5
  per_doc_cap: 4
  rerank: false          # bge-reranker (Phase 2.5, 토글)
  cache_dir: .cache/embeddings
  chunk_chars: 800
  min_score: 0.0         # rrf 사용 시 사실상 미사용, cosine 폴백용
```

### 테스트
- [ ] FakeEmbedder + 텍스트로 RRF/MMR/per_doc_cap 동작, 캐시 히트 검증

---

## Phase 3 — LLM 비교 강화 (필드별 판정) ✅ (완료)

> 구현 요약: `RECORD_SYSTEM_PROMPT`+`build_record_prompt`(레코드+필드목록+후보→필드별 JSON),
> `Comparator.compare_record() -> RecordResult`(필드별 verdict/매칭/사유, 잘못된 매칭id 필터,
> 누락 필드는 different 표시, 후보 0개면 LLM 없이 전필드 not_found), `_complete_json` 1회 재요청 폴백.
> `models`: `FieldResult`/`RecordResult`(필드 판정 집계 verdict). 리포트는 레코드별 필드 표 렌더.
> 파이프라인이 RecordItem이면 compare_record 로 분기. `tests/test_field_comparison.py` 7케이스.


### `comparison/`
- [ ] `prompts.py`: 레코드+필드목록+후보10개 → **필드별 JSON 배열** 요구.
  ```json
  {"fields":[
     {"field_id":"...","verdict":"same|partial|different|not_found",
      "matched_item_ids":["..."],"reasoning":"..."}
  ]}
  ```
- [ ] `Comparator.compare_record(record, candidates) -> RecordResult`
- [ ] JSON 강제: 파싱 실패 시 "JSON만 출력" 지시로 1회 재요청 → 그래도 실패면 필드 전체 different+raw 첨부
- [ ] 배칭/비용: 후보 텍스트 길이 상한, 토큰 예측 로그, 동시성(옵션) 자리만 마련
- [ ] `models.py`: `FieldResult`, `RecordResult(fields: list[FieldResult])`

### 테스트
- [ ] FakeLLM 으로 필드별 판정 파싱/폴백 검증

---

## Phase 4 — Streamlit 로컬 UI

### 새 파일 `app/streamlit_app.py`
- [ ] 사이드바: 백엔드 선택(ollama/internal), 모델명, granularity, top_k, rerank 토글 → 런타임 `AppConfig` 구성
- [ ] 입력: 기준 1개 업로더 + 대상 다중 업로더 **또는** 폴더 경로 입력(로컬 실행이므로 경로 직접 지정 허용)
- [ ] 업로드 파일 → `tempfile` 저장 후 경로로 파이프라인 호출(COM은 경로 필요)
- [ ] 진행률: `pipeline.run(..., progress=)` 콜백 → `st.progress`/표 실시간 갱신
- [ ] 결과: 레코드/필드 표 + verdict 색상 + 출처 링크표기, 마크다운 렌더, **다운로드 버튼**(.md)
- [ ] 실행 가이드 README: `streamlit run app/streamlit_app.py` (사용자 Windows PC, Office 필요)

> 제약 명시: COM 자동화는 데스크톱 세션 필요 → 앱은 사용자 PC localhost 에서 구동.

### `pyproject.toml`
- [ ] optional-deps `ui = ["streamlit>=1.30"]`

---

## Phase 5 — 백엔드 마감 / 패키징 / 문서

- [ ] Ollama·Internal HTTP 재시도(지수백오프)·타임아웃·에러 메시지
- [ ] 사내 프록시 우회 실검증 체크리스트(요청 직전 env 확인 로깅 옵션)
- [ ] (선택) PyInstaller 또는 xlwings 버튼 추가 옵션
- [ ] 사용자 매뉴얼(설치/설정/실행/트러블슈팅: COM 권한, Office 버전)

---

## 비용/성능 메모
- LLM 호출 수 ≈ 기준 행 수(hybrid). 필드는 호출당 묶음 처리 → field 모드 대비 대폭 절감.
- 대상 임베딩은 파일 해시 캐시로 재실행 0 비용. 검색은 numpy 브루트포스(수만 청크까지). 그 이상은 hnswlib 교체(HybridIndex 내부만 수정).
- 숫자는 임베딩으로 매칭하지 않음(의도적): 엔티티로 후보를 찾고 LLM이 수치 차이를 판정. 코드/ID는 BM25가 보완.

## 작업 순서 제안
Phase 1 → 2 → 3 (엔진 완성) → 4 (UI) → 5 (마감). 각 Phase 종료 시 테스트 통과 + 커밋.
