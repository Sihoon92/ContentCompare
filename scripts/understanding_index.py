"""docs/understanding/ 의 계층 색인을 만든다 — 목차 · README · 각 문서의 네비게이션.

19개 설명 문서가 쌓였는데 **어디에서도 색인되지 않았다**. index 도 README 도 없었고
최상위 README·CLAUDE.md 어디에도 이 폴더 언급이 없었다. 게다가 문서 간 링크망이 두
덩어리로 끊겨 있었다 — 08-02~08-06 개념/온톨로지 군과 08-10~08-14 판정/운영 군 사이에
링크가 **0개**였다.

세 산출물이 아래 ``CATALOG`` **하나**에서 나온다:

1. ``docs/understanding/index.html`` — 읽을 수 있는 최상위 개괄서(전체 흐름 → 심화 링크)
2. ``docs/understanding/README.md`` — GitHub 용 색인(HTML 은 웹에서 렌더링되지 않는다)
3. 각 문서 상·하단의 네비게이션 블록 19건

문서를 추가하고 색인을 안 고치는 드리프트를 막으려는 것이다. 폴더의 HTML 과 ``CATALOG``
가 어긋나면 **생성 전에 중단**한다.

**기존 본문은 한 글자도 바꾸지 않는다.** 삽입물은 HTML 주석 마커로 감싸고, 재실행 시
마커 안쪽만 교체한다. 클래스는 전부 ``uh-`` 접두라 문서별 CSS(19개 전부 해시가 다르다)와
충돌하지 않는다.

실행::

    python scripts/understanding_index.py           # 생성 + 삽입
    python scripts/understanding_index.py --check   # 최신인지만 확인(수정 없음)
"""

from __future__ import annotations

import argparse
import html
import os
import re
import sys

DOCS_DIR = os.path.join("docs", "understanding")
INDEX_FILE = os.path.join(DOCS_DIR, "index.html")
README_FILE = os.path.join(DOCS_DIR, "README.md")
ROOT_README = "README.md"

TOP_START, TOP_END = "<!-- uh-nav:top:start -->", "<!-- uh-nav:top:end -->"
BOT_START, BOT_END = "<!-- uh-nav:bottom:start -->", "<!-- uh-nav:bottom:end -->"


# --------------------------------------------------------------------------- #
# 계층 — 파이프라인 단계가 척추다. 그래야 "전체 흐름을 먼저 잡고 내려간다"가 된다.
# --------------------------------------------------------------------------- #
LAYERS = [
    ("intro",   "0. 입문",            "전체를 한 번에",
     "이 시스템이 무엇을 하고 어떻게 굴러가는지 한 편으로 훑는 글들."),
    ("read",    "1. F0 읽기·경계",     "문서 → 블록",
     "문서를 해석 없이 뜯어내고 덩어리로 자르는 자리. 여기서 잃은 것은 뒤에서 복구되지 않는다."),
    ("extract", "2. F1~F3 추출",       "블록 → fact",
     "덩어리를 비교 가능한 주장(fact)으로 바꾼다. LLM 이 가장 많이 개입하는 구간."),
    ("concept", "3. F7 개념 그래프",   "짝 찾기",
     "\"이 둘을 비교해도 되는가\"만 답한다. 값이 같은지는 다음 단계의 몫이다."),
    ("compare", "4. F5 값 대조",       "판정",
     "코드가 확실한 것만 단정하고 애매하면 LLM 에 넘긴다. 최종 판정이 여기서 나온다."),
    ("report",  "5. 리포트·RAG 엔진",  "결과와 다른 경로",
     "판정을 사람이 읽을 형태로 내는 자리, 그리고 별도로 존재하는 RAG 엔진."),
    ("ops",     "6. 운영·진단",        "산출물과 숫자",
     "실행이 남기는 파일과 계측을 읽는 법. 오판을 추적하려면 여기부터 본다."),
    ("strategy","7. 전략",             "앞으로",
     "지금 구조를 어디로 끌고 갈 것인가."),
]
LAYER_TITLE = {k: t for k, t, _, _ in LAYERS}

KIND_LABEL = {
    "overview": ("개괄", "#1565c0"),
    "deep":     ("심화", "#00796b"),
    "incident": ("사건", "#c62828"),
    "design":   ("설계", "#6a1b9a"),
    "strategy": ("전략", "#e65100"),
}


def D(file, title, summary, layer, kind, *, status=None, related=()):
    return dict(file=file, title=title, summary=summary, layer=layer, kind=kind,
                status=status, related=list(related))


# ``related`` 는 **끊긴 클러스터를 잇는 데** 쓴다 — 앞뒤 문서는 계층 순서에서 자동으로
# 나오므로, 여기에는 계층을 가로지르는 연결만 적는다.
CATALOG = [
    # ---- 0. 입문 ---------------------------------------------------------
    D("2026-08-06-explanation-what-the-llm-sees.html",
      "LLM 은 무엇을 보고 판단할까",
      "밑바닥부터 시작하는 fact 엔진 전 구간 개괄. 처음 읽기에 가장 좋다.",
      "intro", "overview",
      related=["2026-08-05-explanation-ontology-concept-graph.html",
               "2026-08-14-explanation-missing-and-same-as.html"]),
    D("2026-08-03-explanation-how-contentcompare-compares-documents.html",
      "ContentCompare 는 문서를 어떻게 비교하는가",
      "RAG 경로와 fact 경로를 코드 단위로 끝까지 따라가는 마스터 개괄. 가장 길고 가장 넓다.",
      "intro", "overview",
      related=["2026-08-07-explanation-rag-verdict-stage.html",
               "2026-08-05-explanation-fact-pipeline-artifacts.html"]),
    D("2026-08-02-explain-fact-pipeline.html",
      "엑셀과 워드는 같은 말을 하고 있을까?",
      "왜 RAG 를 버리고 fact 방식으로 가려 하는가 — 비유 중심의 첫 설명.",
      "intro", "overview",
      status=("legacy",
              "이 글은 <b>초기판</b>입니다. 같은 내용을 더 넓고 정확하게 다룬 "
              "<a href=\"2026-08-03-explanation-how-contentcompare-compares-documents.html\">"
              "「ContentCompare 는 문서를 어떻게 비교하는가」</a>를 먼저 권합니다."),
      related=["2026-08-03-explanation-how-contentcompare-compares-documents.html"]),

    # ---- 1. F0 읽기·경계 --------------------------------------------------
    D("2026-08-14-explanation-word-block-boundary.html",
      "사라진 두 줄 — 워드의 Enter 가 fact 를 지우는 자리",
      "문단이 나뉘었다는 이유로 fact 2건이 통째로 사라진 사건과 그 수정.",
      "read", "incident",
      related=["2026-08-05-explanation-fact-llm-io-walkthrough.html"]),
    D("2026-08-10-explanation-charge-temperature-merge.html",
      "한 문단, 네 개의 조건",
      "Word 줄바꿈이 네 구간을 한 덩어리로 삼키는 자리 — raw/compact 경계 추적.",
      "read", "incident",
      # 본문에 이미 정정판 배너가 있다 → 목차에는 배지만 달고 문서에는 배너를 넣지 않는다
      # (배너 문구가 None). 같은 말을 두 번 하면 독자가 어느 쪽이 최신인지 되묻는다.
      status=("revised", None),
      related=["2026-08-13-explanation-multi-candidate-1n-comparison.html"]),

    # ---- 2. F1~F3 추출 ----------------------------------------------------
    D("2026-08-05-explanation-fact-llm-io-walkthrough.html",
      "fact 엔진의 LLM 사용 지점",
      "LLM 이 호출되는 여섯 자리에 각각 무엇이 들어가고 무엇이 나오는가.",
      "extract", "deep",
      related=["2026-08-05-explanation-fact-pipeline-artifacts.html",
               "2026-08-14-explanation-missing-and-same-as.html"]),

    # ---- 3. F7 개념 그래프 ------------------------------------------------
    D("2026-08-05-explanation-ontology-concept-graph.html",
      "온톨로지 개념 그래프",
      "유사도로는 '다르다'를 말할 수 없다 — F7 설계 전체. 이 계층의 정본.",
      "concept", "design",
      related=["2026-08-14-explanation-missing-and-same-as.html"]),
    D("2026-08-06-explanation-concept-merge-and-veto.html",
      "바구니에 담기와 금지표",
      "맞는 판정이 `rejected_by: differs_by` 로 거부된 이유 — 병합 순서와 veto.",
      "concept", "incident"),
    D("2026-08-06-explanation-evidence-gate-redesign.html",
      "증거가 주장을 뒷받침하지 못할 때",
      "근거 검문소가 '이름이 같다'를 값으로 증명시키는 구조적 오류와 3단계 해법.",
      "concept", "design"),
    D("2026-08-06-explanation-cross-language-recall-bottleneck.html",
      "언어가 다르면 왜 비교가 무너지는가",
      "교차언어 recall 경로 해부 — 반전은 임베딩이 범인이 아니었다는 것.",
      "concept", "deep",
      related=["2026-08-14-explanation-missing-and-same-as.html",
               "2026-08-13-explanation-run-stats-anatomy.html"]),
    D("2026-08-05-explanation-english-document-fix.html",
      "영어로 쓴 문서는 왜 비교가 안 됐을까",
      "영어 대상 문서에서 매칭이 0이 된 사건 — 범인은 언어가 아니라 검문소였다.",
      "concept", "incident",
      related=["2026-08-06-explanation-cross-language-recall-bottleneck.html"]),

    # ---- 4. F5 값 대조 ----------------------------------------------------
    D("2026-08-14-explanation-missing-and-same-as.html",
      "⚪ 대상에 없음 은 어떻게 정해지는가",
      "`same_as` 에서 `findings` 까지 판정 경로 전체. 이 계층의 정본.",
      "compare", "deep",
      related=["2026-08-05-explanation-ontology-concept-graph.html",
               "2026-08-13-explanation-run-stats-anatomy-followup.html"]),
    D("2026-08-10-explanation-fact-acceptance-gate.html",
      "Acceptance Gate — 코드의 '일치' 판정을 믿어도 되는가",
      "조용한 오판을 막는 라우팅 게이트 7규칙과, 왜 기본값이 shadow 인가.",
      "compare", "design",
      related=["2026-08-14-explanation-missing-and-same-as.html",
               "2026-08-13-explanation-run-stats-anatomy.html"]),
    D("2026-08-13-explanation-multi-candidate-1n-comparison.html",
      "후보가 넷일 때 — 1:N Fact 비교 문제와 해법",
      "`candidates[0]` 축약이 만든 임의 선택을 1:N 종합 판정으로 바꾼 설계.",
      "compare", "design",
      status=("implemented",
              "본문에 <b>“설계 승인 · 구현 전”</b>이라 적혀 있으나 이 설계는 "
              "<code>aba326e</code> 로 <b>이미 구현되었습니다</b>. 현행 동작은 "
              "<a href=\"2026-08-14-explanation-missing-and-same-as.html#findings\">"
              "「⚪ 대상에 없음 은 어떻게 정해지는가」 §5</a> 를 보세요."),
      related=["2026-08-14-explanation-missing-and-same-as.html",
               "2026-08-10-explanation-charge-temperature-merge.html"]),

    # ---- 5. 리포트·RAG ----------------------------------------------------
    D("2026-08-07-explanation-rag-verdict-stage.html",
      "판정 단계의 해부",
      "`--engine rag` 의 판정 LLM 호출 — verdict·findings 네 필드는 어디서 오는가.",
      "report", "deep",
      related=["2026-08-03-explanation-how-contentcompare-compares-documents.html"]),

    # ---- 6. 운영·진단 -----------------------------------------------------
    D("2026-08-05-explanation-fact-pipeline-artifacts.html",
      "fact 파이프라인 해부 — 생성되는 파일들",
      "실행이 남기는 JSON 산출물 지도, 그리고 '왜 LLM 위임이 2건뿐인가'.",
      "ops", "deep",
      related=["2026-08-13-explanation-run-stats-anatomy.html"]),
    D("2026-08-13-explanation-run-stats-anatomy.html",
      "실행 통계 읽는 법",
      "run stats 의 스무 개 숫자가 각각 무엇을 분모로 무엇을 세는가.",
      "ops", "deep"),
    D("2026-08-13-explanation-run-stats-anatomy-followup.html",
      "missing 76건은 정상인가",
      "위 글 독자의 질문 셋에 실측으로 답하는 후속편.",
      "ops", "deep",
      related=["2026-08-14-explanation-missing-and-same-as.html"]),

    # ---- 7. 전략 ----------------------------------------------------------
    D("2026-08-14-explanation-pipeline-to-agent.html",
      "이 파이프라인을 AI 에이전트로 만들 수 있는가",
      "루프 · 그래프 엔지니어링 · 권한 경계, 그리고 무엇이 중요한 역량인가.",
      "strategy", "strategy",
      related=["2026-08-14-explanation-missing-and-same-as.html",
               "2026-08-13-explanation-run-stats-anatomy.html"]),
]

BY_FILE = {d["file"]: d for d in CATALOG}


def date_of(entry) -> str:
    return entry["file"][:10]


def layer_order(entry) -> int:
    return [k for k, *_ in LAYERS].index(entry["layer"])


def ordered() -> list:
    """계층 순서 → 카탈로그 등재 순서. 이전/다음 네비가 이 순서를 따른다."""
    return sorted(CATALOG, key=lambda d: (layer_order(d), CATALOG.index(d)))


def in_layer(key) -> list:
    return [d for d in ordered() if d["layer"] == key]


# --------------------------------------------------------------------------- #
# 삽입 블록
# --------------------------------------------------------------------------- #
NAV_CSS = """<style>
.uh-nav{max-width:%(w)dpx;margin:0 auto;padding:10px 20px;font-size:.86rem;line-height:1.7;
  font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","Malgun Gothic",sans-serif;
  color:#5b6570;border-bottom:1px solid #e3e8ee}
.uh-nav a{color:#1565c0;text-decoration:none}
.uh-nav a:hover{text-decoration:underline}
.uh-sep{opacity:.45;margin:0 6px}
.uh-cur{color:#1a1a1a;font-weight:600}
.uh-bottom{border-bottom:0;border-top:1px solid #e3e8ee;margin-top:44px;padding-top:16px;padding-bottom:26px}
.uh-row{margin:5px 0}
.uh-pn{display:flex;gap:14px;flex-wrap:wrap;justify-content:space-between}
.uh-pn a{display:inline-block;max-width:46%%}
.uh-banner{max-width:%(w)dpx;margin:14px auto 0;padding:11px 16px;border-radius:8px;font-size:.9rem;
  line-height:1.65;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","Malgun Gothic",sans-serif}
.uh-banner a{color:inherit;font-weight:600}
.uh-legacy{background:#fff6e5;border-left:4px solid #b26a00;color:#4a3520}
.uh-implemented{background:#e8f5e9;border-left:4px solid #2e7d32;color:#1b3a20}
.uh-revised{background:#e8f1fb;border-left:4px solid #1565c0;color:#12324f}
@media (prefers-color-scheme:dark){
  .uh-nav{color:#9aa8b6;border-color:#2a3441}
  .uh-nav a{color:#6ea8fe}
  .uh-cur{color:#e6edf3}
  .uh-legacy{background:#3a2f18;color:#f0e2c8}
  .uh-implemented{background:#1c3423;color:#d6ecd9}
  .uh-revised{background:#17293d;color:#d6e6f7}
}
@media (max-width:640px){ .uh-pn a{max-width:100%%} }
</style>"""


def esc(s: str) -> str:
    return html.escape(s, quote=False)


def read_text(path: str) -> tuple[str, str]:
    """(내용, 줄바꿈) — **줄바꿈을 변환하지 않고 읽는다.**

    이 폴더는 CRLF 7건 · LF 12건이 섞여 있다. 파이썬 기본 모드로 읽고 쓰면 파일 전체의
    줄바꿈이 바뀌어, "삽입물 밖은 무변경"이라는 이 스크립트의 약속이 깨진다.
    """
    with open(path, encoding="utf-8", newline="") as fh:
        text = fh.read()
    return text, ("\r\n" if "\r\n" in text else "\n")


def write_text(path: str, text: str) -> None:
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)


def nl(block: str, eol: str) -> str:
    """삽입 블록의 줄바꿈을 대상 파일에 맞춘다(블록에는 ``\\r`` 이 없다)."""
    return block if eol == "\n" else block.replace("\n", eol)


def _wrap_width(source: str) -> int:
    m = re.search(r"\.wrap\s*\{[^}]*?max-width\s*:\s*(\d+)px", source, re.S)
    return int(m.group(1)) if m else 900


def top_block(entry, source: str) -> str:
    layer = LAYER_TITLE[entry["layer"]]
    parts = [
        NAV_CSS % {"w": _wrap_width(source)},
        '<div class="uh-nav uh-top">'
        '<a href="index.html">📚 understanding</a>'
        f'<span class="uh-sep">›</span><a href="index.html#{entry["layer"]}">{esc(layer)}</a>'
        f'<span class="uh-sep">›</span><span class="uh-cur">{esc(entry["title"])}</span>'
        '</div>',
    ]
    if entry["status"] and entry["status"][1]:
        kind, text = entry["status"]
        icon = {"legacy": "⚠️", "implemented": "✅", "revised": "🔧"}[kind]
        parts.append(f'<div class="uh-banner uh-{kind}">{icon} {text}</div>')
    return f"{TOP_START}\n" + "\n".join(parts) + f"\n{TOP_END}\n"


def bottom_block(entry) -> str:
    seq = ordered()
    i = seq.index(entry)
    prev_d = seq[i - 1] if i > 0 else None
    next_d = seq[i + 1] if i < len(seq) - 1 else None

    rows = ['<div class="uh-row"><a href="index.html">← 전체 목차로</a></div>']
    pn = []
    if prev_d:
        pn.append(f'<a href="{prev_d["file"]}">◀ 이전 · {esc(prev_d["title"])}</a>')
    if next_d:
        pn.append(f'<a href="{next_d["file"]}">다음 · {esc(next_d["title"])} ▶</a>')
    if pn:
        rows.append('<div class="uh-row uh-pn">' + "".join(pn) + "</div>")
    rel = [BY_FILE[f] for f in entry["related"] if f in BY_FILE and f != entry["file"]]
    if rel:
        links = " · ".join(f'<a href="{d["file"]}">{esc(d["title"])}</a>' for d in rel)
        rows.append(f'<div class="uh-row uh-rel">관련 · {links}</div>')
    return (f"{BOT_START}\n" + '<div class="uh-nav uh-bottom">'
            + "".join(rows) + "</div>\n" + f"{BOT_END}\n")


def strip_blocks(source: str) -> str:
    """이전 실행의 삽입물을 제거한다 — 마커 밖은 건드리지 않는다.

    끝의 줄바꿈을 ``\\r?\\n?`` 로 받는 것이 중요하다. 이 폴더는 CRLF 8건 · LF 11건이
    섞여 있어 ``\\n?`` 만 쓰면 CRLF 파일에서 ``\\r`` 이 남고, 재실행마다 빈 줄이 쌓인다.
    """
    for a, b in ((TOP_START, TOP_END), (BOT_START, BOT_END)):
        source = re.sub(re.escape(a) + r".*?" + re.escape(b) + r"\r?\n?", "",
                        source, flags=re.S)
    return source


def inject(entry, source: str, eol: str) -> str:
    """상단은 첫 가시 요소 앞, 하단은 고정 푸터 앞(없으면 끝)."""
    base = strip_blocks(source)

    spots = [m.start() for m in
             (re.search(r"<header[\s>]", base), re.search(r'<div class="wrap"', base))
             if m]
    if not spots:
        sys.exit(f"[중단] 삽입 지점을 찾지 못했습니다: {entry['file']}")
    at = min(spots)
    out = base[:at] + nl(top_block(entry, base), eol) + base[at:]

    m = re.search(r'<div id="qmemo-bar"', out)
    tail = nl(bottom_block(entry), eol)
    if m:
        out = out[:m.start()] + tail + out[m.start():]
    else:
        out = out.rstrip("\r\n") + eol + tail
    return out


# --------------------------------------------------------------------------- #
# index.html
# --------------------------------------------------------------------------- #
SYMPTOMS = [
    ("<span class='v v-none'>⚪ 대상에 없음</span> 이 너무 많다",
     "후보가 아예 없었는지, 후보는 있었는데 LLM 이 없다고 했는지부터 가른다.",
     ["2026-08-14-explanation-missing-and-same-as.html",
      "2026-08-13-explanation-run-stats-anatomy-followup.html",
      "2026-08-06-explanation-cross-language-recall-bottleneck.html"]),
    ("영어(외국어) 문서에서 매칭이 안 된다",
     "범인은 대개 언어가 아니라 근거 검문소이거나 recall 뒤쪽 구멍이다.",
     ["2026-08-05-explanation-english-document-fix.html",
      "2026-08-06-explanation-cross-language-recall-bottleneck.html",
      "2026-08-06-explanation-evidence-gate-redesign.html"]),
    ("맞는 판정인데 거부됐다",
     "<code>rejected_by</code> 가 <code>evidence</code> 인지 <code>differs_by</code> 인지에 따라 원인이 다르다.",
     ["2026-08-06-explanation-concept-merge-and-veto.html",
      "2026-08-06-explanation-evidence-gate-redesign.html"]),
    ("한 문단의 여러 조건이 하나로 뭉쳤다",
     "추출 쪽(경계)과 판정 쪽(1:N)이 각각 다른 이야기를 한다.",
     ["2026-08-10-explanation-charge-temperature-merge.html",
      "2026-08-14-explanation-word-block-boundary.html",
      "2026-08-13-explanation-multi-candidate-1n-comparison.html"]),
    ("어느 시점부터 전부 <span class='v v-unk'>❓ 판단보류</span> 다",
     "대개 예산 고갈이다. 보류된 항목의 <b>순서</b> 자체가 증거다.",
     ["2026-08-14-explanation-missing-and-same-as.html",
      "2026-08-13-explanation-run-stats-anatomy.html"]),
    ("이 숫자들이 무엇을 세는지 모르겠다",
     "분모가 셋이다 — 그것부터 갈라야 지표를 읽을 수 있다.",
     ["2026-08-13-explanation-run-stats-anatomy.html",
      "2026-08-05-explanation-fact-pipeline-artifacts.html"]),
    ("LLM 에 정확히 무엇이 들어가는지 알고 싶다",
     "호출 자리가 여섯 곳이고, 자리마다 보는 데이터가 다르다.",
     ["2026-08-05-explanation-fact-llm-io-walkthrough.html",
      "2026-08-06-explanation-what-the-llm-sees.html"]),
    ("Langfuse trace 를 읽고 싶다",
     "<code>--engine rag</code> 의 판정 호출은 fact 엔진과 완전히 다른 모듈이다.",
     ["2026-08-07-explanation-rag-verdict-stage.html"]),
]

PATHS = [
    ("처음 왔어요", "#1565c0",
     "이 시스템이 뭘 하는 물건인지부터. 60~90분.",
     ["2026-08-06-explanation-what-the-llm-sees.html",
      "2026-08-03-explanation-how-contentcompare-compares-documents.html",
      "2026-08-05-explanation-ontology-concept-graph.html",
      "2026-08-14-explanation-missing-and-same-as.html"]),
    ("판정이 이상해요 (디버깅)", "#c62828",
     "숫자를 먼저 읽고, 그 다음 원인 갈래로 내려간다.",
     ["2026-08-13-explanation-run-stats-anatomy.html",
      "2026-08-13-explanation-run-stats-anatomy-followup.html",
      "2026-08-14-explanation-missing-and-same-as.html",
      "2026-08-05-explanation-fact-pipeline-artifacts.html"]),
    ("왜 이렇게 설계했나", "#6a1b9a",
     "결정과 그 근거를 시간 순으로.",
     ["2026-08-05-explanation-ontology-concept-graph.html",
      "2026-08-06-explanation-evidence-gate-redesign.html",
      "2026-08-10-explanation-fact-acceptance-gate.html",
      "2026-08-13-explanation-multi-candidate-1n-comparison.html",
      "2026-08-14-explanation-pipeline-to-agent.html"]),
]


def _kind_badge(kind) -> str:
    label, color = KIND_LABEL[kind]
    return f'<span class="kind" style="color:{color};border-color:{color}">{label}</span>'


def _status_badge(entry) -> str:
    if not entry["status"]:
        return ""
    kind = entry["status"][0]
    text = {"legacy": "초기판", "implemented": "구현됨", "revised": "정정판"}[kind]
    icon = {"legacy": "⚠️", "implemented": "✅", "revised": "🔧"}[kind]
    return f'<span class="st st-{kind}">{icon} {text}</span>'


def _doc_link(f) -> str:
    d = BY_FILE[f]
    return f'<a href="{f}">{esc(d["title"])}</a>'


def build_index() -> str:
    head = _INDEX_HEAD

    # 계층별 서술 + 문서 카드
    body = []
    for key, title, tagline, blurb in LAYERS:
        docs = in_layer(key)
        if not docs:
            continue
        body.append(f'<section id="{key}">')
        body.append(f'<h2>{esc(title)} <span class="tag">{esc(tagline)}</span></h2>')
        body.append(f"<p>{blurb}</p>")
        body.append('<div class="cards">')
        for d in docs:
            body.append(
                '<a class="card" href="%s">'
                '<div class="ct">%s</div>'
                '<div class="cs">%s</div>'
                '<div class="cm">%s %s <span class="dt">%s</span></div>'
                "</a>" % (d["file"], esc(d["title"]), _md_inline(d["summary"]),
                          _kind_badge(d["kind"]), _status_badge(d), date_of(d))
            )
        body.append("</div></section>")

    # 읽기 경로
    paths = []
    for name, color, note, files in PATHS:
        steps = "".join(
            f'<li>{_doc_link(f)}</li>' for f in files)
        paths.append(
            f'<div class="path" style="border-color:{color}">'
            f'<div class="pt" style="color:{color}">{esc(name)}</div>'
            f'<div class="pn">{esc(note)}</div><ol>{steps}</ol></div>')

    # 증상별 역인덱스
    rows = []
    for symptom, note, files in SYMPTOMS:
        links = " · ".join(_doc_link(f) for f in files)
        rows.append(f"<tr><td><b>{symptom}</b><div class='sn'>{note}</div></td>"
                    f"<td>{links}</td></tr>")

    return head + "\n".join(body) + _INDEX_PATHS_OPEN + "".join(paths) \
        + _INDEX_SYMPTOM_OPEN + "".join(rows) + _INDEX_TAIL


def _md_inline(text: str) -> str:
    """요약문의 백틱만 <code> 로. 그 외 마크업은 쓰지 않는다."""
    out = esc(text)
    return re.sub(r"`([^`]+)`", r"<code>\1</code>", out)


# --------------------------------------------------------------------------- #
# README.md
# --------------------------------------------------------------------------- #
def build_readme() -> str:
    lines = [
        "# 이해 문서 (understanding)",
        "",
        "이 폴더는 **무엇이 왜 그렇게 됐는가**를 남기는 곳이다.",
        "`docs/FACT_*_DESIGN.md` 가 \"무엇을 만들 것인가\"(설계)라면, 여기는 그 결과 실제로",
        "무슨 일이 벌어졌고 어디서 어긋났는지를 사후에 설명한다.",
        "",
        "> 📖 **[`index.html`](index.html) 을 브라우저로 여세요.** 전체 흐름을 한 편으로 읽고",
        "> 각 대목에서 심화 문서로 내려가도록 짜여 있습니다. 아래는 GitHub 용 색인입니다",
        "> (HTML 은 GitHub 웹에서 렌더링되지 않습니다).",
        "",
        "## 계층",
        "",
    ]
    for key, title, tagline, blurb in LAYERS:
        docs = in_layer(key)
        if not docs:
            continue
        lines += [f"### {title} — {tagline}", "", blurb, ""]
        for d in docs:
            badge = ""
            if d["status"]:
                badge = {"legacy": " ⚠️ *초기판*", "implemented": " ✅ *구현됨*",
                         "revised": " 🔧 *정정판*"}[d["status"][0]]
            lines.append(f"- [{d['title']}]({d['file']}){badge}  \n  {d['summary']} "
                         f"<sub>{KIND_LABEL[d['kind']][0]} · {date_of(d)}</sub>")
        lines.append("")

    lines += ["## 읽기 경로", ""]
    for name, _c, note, files in PATHS:
        lines.append(f"**{name}** — {note}")
        lines.append("")
        for i, f in enumerate(files, 1):
            lines.append(f"{i}. [{BY_FILE[f]['title']}]({f})")
        lines.append("")

    lines += [
        "## 문서를 추가하려면",
        "",
        "`scripts/understanding_index.py` 의 `CATALOG` 에 한 줄 추가하고 실행한다.",
        "",
        "```bash",
        "python scripts/understanding_index.py",
        "```",
        "",
        "`index.html` · 이 `README.md` · 각 문서의 상하단 네비가 **함께** 갱신된다.",
        "폴더의 HTML 과 `CATALOG` 가 어긋나면 생성 전에 중단하므로, 문서만 추가하고",
        "색인을 잊는 드리프트가 구조적으로 불가능하다.",
        "",
    ]
    return "\n".join(lines)


ROOT_LINE = ("> 설계 결정과 사건의 사후 설명(왜 그렇게 됐는가)은 "
             "[`docs/understanding/`](docs/understanding/README.md) 참고 — "
             "전체 흐름은 `docs/understanding/index.html` 을 브라우저로 열면 된다.\n")


def patch_root_readme(text: str, eol: str) -> str:
    if "docs/understanding/" in text:
        return text
    anchor = nl("> 차세대(fact 기반) 비교 방식 구현 계획은 "
                "[`docs/FACT_PIPELINE_PLAN.md`](docs/FACT_PIPELINE_PLAN.md) 참고"
                "(현행 방식과 비교·공존).\n", eol)
    if anchor not in text:
        sys.exit("[중단] 최상위 README 에서 삽입 지점을 찾지 못했습니다")
    return text.replace(anchor, anchor + nl(ROOT_LINE, eol), 1)


# --------------------------------------------------------------------------- #
def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true", help="생성물이 최신인지만 확인(수정 없음)")
    args = ap.parse_args()

    on_disk = {f for f in os.listdir(DOCS_DIR)
               if f.endswith(".html") and f != "index.html"}
    listed = set(BY_FILE)
    if on_disk != listed:
        missing = sorted(on_disk - listed)
        extra = sorted(listed - on_disk)
        sys.exit(f"[중단] CATALOG 와 폴더가 어긋납니다.\n"
                 f"  카탈로그에 없는 파일: {missing}\n"
                 f"  파일이 없는 카탈로그 항목: {extra}")

    root_text, root_eol = read_text(ROOT_README)
    planned: dict[str, str] = {
        INDEX_FILE: build_index(),
        README_FILE: build_readme(),
        ROOT_README: patch_root_readme(root_text, root_eol),
    }
    for f, entry in BY_FILE.items():
        path = os.path.join(DOCS_DIR, f)
        source, eol = read_text(path)
        planned[path] = inject(entry, source, eol)

    stale = [p for p, new in planned.items()
             if not os.path.exists(p) or read_text(p)[0] != new]
    if args.check:
        if stale:
            print("최신이 아닙니다:\n  " + "\n  ".join(sorted(stale)))
            sys.exit(1)
        print(f"최신입니다 ({len(planned)} 파일)")
        return

    for path, new in planned.items():
        write_text(path, new)
    print(f"문서 {len(CATALOG)}건 · 계층 {len(LAYERS)}층 → "
          f"{INDEX_FILE} · {README_FILE} · 네비 삽입 {len(BY_FILE)}건"
          + (f" (변경 {len(stale)})" if stale else " (변경 없음)"))


# --------------------------------------------------------------------------- #
# index.html 템플릿 — 색은 ui/diagram.py 의 공용 팔레트를 그대로 쓴다.
# --------------------------------------------------------------------------- #
_INDEX_HEAD = """<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>understanding — ContentCompare 이해 문서 전체 지도</title>
<style>
  :root{
    --code:#1565c0; --code-bg:#e8f1fb;
    --llm:#e65100;  --llm-bg:#fdf0e3;
    --emb:#00796b;  --emb-bg:#e2f2f0;
    --human:#6a1b9a;--human-bg:#f4e9f7;
    --ink:#1a1a1a; --muted:#5b6570; --line:#dfe4ea; --bg:#fff; --soft:#f7f9fb;
    --ok:#2e7d32; --bad:#c62828; --warn:#b26a00;
    --mono:"SFMono-Regular",Consolas,"Liberation Mono",Menlo,"D2Coding",monospace;
  }
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--ink);line-height:1.75;font-size:16.5px;
    font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","Malgun Gothic","Apple SD Gothic Neo",sans-serif}
  .wrap{max-width:940px;margin:0 auto;padding:0 20px 90px}
  header.top{padding:52px 0 26px;border-bottom:1px solid var(--line);margin-bottom:12px}
  header.top h1{font-size:1.95rem;margin:0 0 10px;letter-spacing:-.02em;line-height:1.3}
  header.top .sub{color:var(--muted);margin:0;font-size:1.02rem}
  h2{font-size:1.42rem;margin:52px 0 6px;padding-top:14px;border-top:2px solid var(--ink);letter-spacing:-.01em}
  h2 .tag{font-size:.82rem;font-weight:500;color:var(--muted);letter-spacing:0;margin-left:8px}
  h3{font-size:1.1rem;margin:30px 0 8px;color:#2a3540}
  p{margin:12px 0}
  a{color:var(--code)}
  code{font-family:var(--mono);font-size:.88em;background:var(--soft);border:1px solid var(--line);
    border-radius:4px;padding:1px 5px}
  .lede{font-size:1.06rem;color:#33404d}
  .small{font-size:.9rem;color:var(--muted)}
  .figbox{border:1px solid var(--line);border-radius:10px;padding:16px;background:#fff;overflow-x:auto;margin:22px 0}
  .figbox svg{max-width:100%;height:auto;display:block;margin:0 auto}
  figcaption{font-size:.86rem;color:var(--muted);text-align:center;margin-top:9px}
  .callout{border-left:4px solid var(--code);background:var(--code-bg);padding:13px 17px;
    border-radius:0 8px 8px 0;margin:20px 0}
  .callout .t{font-weight:700;display:block;margin-bottom:4px}
  .cards{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin:16px 0 6px}
  .card{display:block;border:1px solid var(--line);border-radius:10px;padding:13px 15px;
    text-decoration:none;color:inherit;background:#fff;transition:border-color .12s}
  .card:hover{border-color:var(--code);box-shadow:0 1px 6px rgba(21,101,192,.08)}
  .ct{font-weight:650;font-size:.99rem;line-height:1.45;color:var(--ink)}
  .cs{font-size:.88rem;color:var(--muted);margin-top:5px;line-height:1.6}
  .cm{margin-top:9px;display:flex;gap:7px;align-items:center;flex-wrap:wrap}
  .kind{font-size:.74rem;font-weight:700;border:1px solid;border-radius:999px;padding:1px 8px}
  .st{font-size:.74rem;font-weight:700;border-radius:999px;padding:1px 8px}
  .st-legacy{background:#fff6e5;color:#b26a00}
  .st-implemented{background:#e8f5e9;color:#2e7d32}
  .st-revised{background:#e8f1fb;color:#1565c0}
  .dt{font-size:.74rem;color:#9aa5b1;margin-left:auto;font-family:var(--mono)}
  .paths{display:grid;grid-template-columns:1fr 1fr 1fr;gap:14px;margin:18px 0}
  .path{border:1px solid;border-left-width:4px;border-radius:10px;padding:13px 16px;background:#fff}
  .pt{font-weight:700;font-size:1rem}
  .pn{font-size:.85rem;color:var(--muted);margin:4px 0 6px}
  .path ol{margin:0;padding-left:20px;font-size:.9rem}
  .path li{margin:5px 0}
  table{border-collapse:collapse;width:100%;margin:16px 0;font-size:.92rem}
  th,td{border:1px solid var(--line);padding:9px 12px;text-align:left;vertical-align:top}
  th{background:var(--soft);font-weight:600}
  .sn{font-size:.85rem;color:var(--muted);font-weight:400;margin-top:3px}
  .v{display:inline-block;font-size:.85rem;border-radius:6px;padding:0 7px;border:1px solid;white-space:nowrap}
  .v-none{color:#455a64;border-color:#cfd8dc;background:#eceff1}
  .v-unk{color:var(--warn);border-color:#ffcc80;background:#fff8e1}
  .scroll{overflow-x:auto}
  @media (max-width:760px){ .cards,.paths{grid-template-columns:1fr} }
  @media (max-width:640px){ header.top h1{font-size:1.5rem} h2{font-size:1.24rem} }
</style>
</head>
<body>
<div class="wrap">

<header class="top">
  <h1>understanding — 전체 지도</h1>
  <p class="sub">ContentCompare 가 문서를 대조하는 과정을 한 편으로 훑고, 각 대목에서 심화 문서로 내려간다.</p>
</header>

<p class="lede">
이 폴더는 <b>무엇이 왜 그렇게 됐는가</b>를 남기는 곳이다.
<code>docs/FACT_*_DESIGN.md</code> 가 "무엇을 만들 것인가"라면, 여기는 그 결과 실제로 무슨 일이
벌어졌고 어디서 어긋났는지를 사후에 설명한다. 지금 19편이 있다.
</p>

<h2 id="what">30초 요약 <span class="tag">이 시스템은 무엇을 하는가</span></h2>

<p>
기준 문서(엑셀 규격서) <b>한 개</b>와 대상 문서(워드·PPT·영문 사양서) <b>N개</b>를 받아,
기준의 <b>각 항목이 대상에도 같은 내용으로 적혀 있는지</b> 한 줄씩 판정한다.
결과는 네 가지다 — <span class="v" style="color:#2e7d32;border-color:#a5d6a7;background:#e8f5e9">✅ 일치</span>
<span class="v" style="color:#c62828;border-color:#ef9a9a;background:#ffebee">❌ 불일치</span>
<span class="v v-none">⚪ 대상에 없음</span>
<span class="v v-unk">❓ 판단보류</span>.
</p>

<p>
핵심은 <b>문서를 통째로 LLM 에 주지 않는다</b>는 것이다. 문서를 <code>fact</code> 라는 작은
주장으로 쪼개고, 각 단계에서 <b>코드가 확실한 것만 확정하고 애매한 것만 LLM 에 넘긴다.</b>
그래서 모든 판정에 원문 근거가 붙고, 사람이 사후에 검수할 수 있다.
</p>

<h2 id="flow">전체 흐름 <span class="tag">클릭하면 해당 계층으로</span></h2>

<div class="figbox">
<svg viewBox="0 0 900 268" role="img" aria-label="파이프라인 전체 흐름 (클릭 가능)">
  <defs>
    <marker id="ar" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto">
      <path d="M0 0 L10 5 L0 10 z" fill="#5b6570"/></marker>
    <style>
      .bx{font:700 13px sans-serif}
      .sm{font:11px sans-serif;fill:#5b6570}
      .lane{font:700 11.5px sans-serif;fill:#37424e}
      a{cursor:pointer}
      a:hover rect{stroke-width:2.4}
    </style>
  </defs>

  <text x="14" y="20" class="lane">본 흐름 — 문서에서 판정까지</text>

  <a href="#read"><rect x="14" y="30" width="158" height="60" rx="8" fill="#e8f1fb" stroke="#1565c0"/>
    <text x="30" y="54" class="bx" fill="#1565c0">F0 읽기·경계</text>
    <text x="30" y="74" class="sm">문서 → 블록</text></a>
  <path d="M172 60 L200 60" stroke="#5b6570" marker-end="url(#ar)"/>

  <a href="#extract"><rect x="206" y="30" width="158" height="60" rx="8" fill="#fdf0e3" stroke="#e65100"/>
    <text x="222" y="54" class="bx" fill="#e65100">F1~F3 추출</text>
    <text x="222" y="74" class="sm">블록 → fact</text></a>
  <path d="M364 60 L392 60" stroke="#5b6570" marker-end="url(#ar)"/>

  <a href="#concept"><rect x="398" y="30" width="176" height="60" rx="8" fill="#e2f2f0" stroke="#00796b"/>
    <text x="414" y="54" class="bx" fill="#00796b">F7 개념 그래프</text>
    <text x="414" y="74" class="sm">비교해도 되는가</text></a>
  <path d="M574 60 L602 60" stroke="#5b6570" marker-end="url(#ar)"/>

  <a href="#compare"><rect x="608" y="30" width="158" height="60" rx="8" fill="#e8f1fb" stroke="#1565c0"/>
    <text x="624" y="54" class="bx" fill="#1565c0">F5 값 대조</text>
    <text x="624" y="74" class="sm">값이 같은가</text></a>
  <path d="M766 60 L794 60" stroke="#5b6570" marker-end="url(#ar)"/>

  <a href="#report"><rect x="800" y="30" width="86" height="60" rx="8" fill="#f7f9fb" stroke="#455a64"/>
    <text x="814" y="54" class="bx" fill="#455a64">리포트</text>
    <text x="814" y="74" class="sm">판정 4종</text></a>

  <line x1="14" y1="112" x2="886" y2="112" stroke="#dfe4ea"/>

  <text x="14" y="140" class="lane">가로지르는 축 — 어느 단계에서든 필요한 것</text>

  <a href="#intro"><rect x="14" y="150" width="278" height="52" rx="8" fill="#fff" stroke="#455a64"/>
    <text x="30" y="172" class="bx">0. 입문 — 전체를 한 번에</text>
    <text x="30" y="191" class="sm">처음이라면 여기부터</text></a>

  <a href="#ops"><rect x="306" y="150" width="278" height="52" rx="8" fill="#fff" stroke="#455a64"/>
    <text x="322" y="172" class="bx">6. 운영·진단</text>
    <text x="322" y="191" class="sm">산출물 파일과 실행 통계 읽는 법</text></a>

  <a href="#strategy"><rect x="598" y="150" width="288" height="52" rx="8" fill="#fff" stroke="#455a64"/>
    <text x="614" y="172" class="bx">7. 전략</text>
    <text x="614" y="191" class="sm">이 구조를 어디로 끌고 갈 것인가</text></a>

  <rect x="14" y="218" width="872" height="38" rx="8" fill="#f7f9fb" stroke="#dfe4ea" stroke-dasharray="4 3"/>
  <text x="30" y="242" class="sm">
    ⚙️ 코드 = 파랑 · 🤖 LLM = 주황 · 🔢 임베딩 = 청록 · 👤 사람 = 보라 — 모든 문서가 같은 색을 쓴다.
  </text>
</svg>
</div>
<figcaption>박스를 클릭하면 그 단계의 문서 목록으로 이동한다.</figcaption>

<div class="callout">
  <span class="t">💡 이 순서가 중요한 이유</span>
  <b>앞 단계에서 잃은 것은 뒤에서 복구되지 않는다.</b> F0 에서 문단 경계를 잘못 자르면
  F3 는 그 fact 를 만들 수 없고, F7 이 개념을 잇지 못하면 F5 는 값을 대조조차 하지 않는다
  (그것이 <span class="v v-none">⚪ 대상에 없음</span> 이다).
  그래서 이상한 판정을 만나면 <b>뒤에서 앞으로</b> 거슬러 올라가며 원인을 찾는다.
</div>

"""

_INDEX_PATHS_OPEN = """
<h2 id="paths">읽기 경로 <span class="tag">목적에 따라 골라 읽기</span></h2>

<p>19편을 순서대로 읽을 필요는 없다. 무엇을 하려는지에 따라 세 갈래를 권한다.</p>

<div class="paths">
"""

_INDEX_SYMPTOM_OPEN = """
</div>

<h2 id="symptoms">증상별 역인덱스 <span class="tag">이럴 땐 이 문서</span></h2>

<p>리포트나 실행 결과에서 이상한 것을 봤을 때 어디부터 열지.</p>

<div class="scroll">
<table>
<thead><tr><th style="width:44%">증상</th><th>읽을 문서</th></tr></thead>
<tbody>
"""

_INDEX_TAIL = """</tbody>
</table>
</div>

<h2 id="relation">설계 문서와의 관계</h2>

<p>
<code>docs/</code> 에는 이 폴더 말고도 설계 문서가 12편 있다
(<code>FACT_F0~F7_DESIGN.md</code>, <code>FACT_LINKED_GRAPH_RAG_DESIGN.md</code>,
<code>DESIGN.md</code> 등). 둘의 역할이 다르다.
</p>

<div class="scroll">
<table>
<thead><tr><th>구분</th><th>답하는 질문</th><th>언제 읽나</th></tr></thead>
<tbody>
<tr><td><code>docs/*_DESIGN.md</code></td><td><b>무엇을 만들 것인가</b> — 스펙·DoD·인터페이스</td>
    <td>구현하기 전, 또는 계약을 확인할 때</td></tr>
<tr><td><code>docs/understanding/</code> (여기)</td><td><b>무엇이 왜 그렇게 됐는가</b> — 사건·결정·실측</td>
    <td>동작이 이해가 안 될 때, 남에게 설명할 때</td></tr>
<tr><td><code>docs/superpowers/specs·plans/</code></td><td>개별 변경의 설계와 실행 계획</td>
    <td>그 변경을 다시 손댈 때</td></tr>
</tbody>
</table>
</div>

<p class="small">
문서를 추가하려면 <code>scripts/understanding_index.py</code> 의 <code>CATALOG</code> 에 한 줄
넣고 <code>python scripts/understanding_index.py</code> 를 실행한다. 이 페이지 ·
<code>README.md</code> · 각 문서의 상하단 네비가 함께 갱신되고, 폴더와 카탈로그가 어긋나면
생성 전에 중단한다.
</p>

</div>
</body>
</html>
"""


if __name__ == "__main__":
    main()
