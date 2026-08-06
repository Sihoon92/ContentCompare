# -*- coding: utf-8 -*-
"""``docs/understanding/*.html`` 설명 페이지에 넣을 **공용 다이어그램 컴포넌트**.

설명 페이지가 늘어나면서 글만 길어지고 그림이 없어 읽기 어렵다는 문제가 있었다
(2026-08-06 실측: 시각자료 하나당 본문 2,500~5,300자, 두 페이지는 SVG 가 0개).
페이지마다 그림을 따로 그리면 용어·색·비유가 서로 어긋나 오히려 더 헷갈린다.
그래서 **여러 페이지가 공유하는 컴포넌트 한 벌**로 만들었다.

설계 규칙 세 가지:

1. **모든 클래스는 ``dv-`` 접두어.** 페이지마다 CSS 가 제각각이라 짧은 클래스명은
   충돌한다 — 실제로 ``2026-08-02`` 페이지의 전역 ``.st{}`` 가 카드에 새어 들어왔다.
   색·다크모드 변수도 ``.dv`` 스코프에 자체적으로 들고 있어 어느 페이지에 넣어도 같게 보인다.
2. **주체 색을 고정한다.** ⚙️코드=파랑 · 🤖LLM=주황 · 🔢임베딩=청록 · 👤사람=보라.
   페이지를 옮겨 다녀도 같은 색이 같은 뜻이라야 독자가 다시 배우지 않는다.
3. **데이터는 지어내지 않는다.** :data:`STOPS`/:data:`FATES` 의 JSON·점수·판정은 전부
   실행이 남긴 ``artifacts/`` 산출물에서 그대로 가져온 것이다(기준: 공칭전압 ``3.89``,
   ``artifacts/자표준문서_xlsx/`` 및 ``artifacts/_runs/``). 파이프라인이 바뀌어 수치가
   달라지면 **여기도 같이 고쳐야 한다** — 설명이 실측과 어긋나면 문서의 신뢰가 무너진다.

컴포넌트 여섯 종:

===============  =========================================================
``journey()``    값 ``3.89`` 하나가 문서→카드로 변해 가는 단계별 입출력 추적
``fates()``      같은 카드가 대상 문서 3종에서 각각 어떤 길을 가고 누가 판정했나
``pipemap()``    10단계 파이프라인 지도 (단계별 주체 배지 + 산출 파일명)
``lanes()``      짝을 정하는 세 갈래 길과 근거 검문소 (SVG)
``tools()``      임베딩 / BM25 / LLM 이 각각 못 하는 것
``vs()``         RAG(행↔청크) 와 fact(카드↔카드) 의 결정적 차이 (SVG)
===============  =========================================================

사용 — 라이브러리로::

    import doc_diagrams as dv
    s = open(page, encoding="utf-8").read()
    s = dv.ensure_css(s)                                   # CSS 1회 주입(멱등)
    s = dv.insert_after_heading(s, "직관", dv.journey())    # 제목 뒤에 블록 삽입
    open(page, "w", encoding="utf-8").write(s)

사용 — CLI::

    python scripts/doc_diagrams.py --list
    python scripts/doc_diagrams.py docs/understanding/새페이지.html         --block journey --after "직관" [--level 2]

``ensure_css`` 는 :data:`CSS_MARKER` 를 보고 이미 주입된 페이지는 건너뛴다(멱등).
블록 삽입은 멱등이 아니므로 같은 블록을 두 번 넣지 않도록 호출자가 확인할 것.
"""
from __future__ import annotations
import re, html, sys

CSS_MARKER = "/* === dv-components v1 === */"

CSS = CSS_MARKER + """
.dv{--dv-fg:#1c1c1a;--dv-mut:#6b6b66;--dv-line:#dcdcd6;--dv-card:#fff;--dv-bg:#f6f6f4;
    --dv-code:#1565c0;--dv-code-bg:#e8f1fb;--dv-llm:#e65100;--dv-llm-bg:#fdf0e3;
    --dv-emb:#00796b;--dv-emb-bg:#e2f2f0;--dv-human:#6a1b9a;--dv-human-bg:#f4e9f7;
    --dv-ok:#2e7d32;--dv-ok-bg:#e9f5ea;--dv-bad:#c62828;--dv-bad-bg:#fbeaea;
    --dv-gray:#607d8b;--dv-gray-bg:#eceff1;--dv-amber:#ef6c00;--dv-amber-bg:#fdf0e0;
    --dv-mono:ui-monospace,SFMono-Regular,Menlo,Consolas,"D2Coding",monospace;
    margin:26px 0;font-size:15px;line-height:1.6;color:var(--dv-fg)}
@media (prefers-color-scheme:dark){.dv{--dv-fg:#e8e8e4;--dv-mut:#a2a29c;--dv-line:#3a3d44;
    --dv-card:#1f2229;--dv-bg:#191c21;
    --dv-code:#8ec5f5;--dv-code-bg:#16283a;--dv-llm:#ffb074;--dv-llm-bg:#3a2412;
    --dv-emb:#6fc9bd;--dv-emb-bg:#123430;--dv-human:#d09ada;--dv-human-bg:#2c1936;
    --dv-ok:#83c98a;--dv-ok-bg:#173620;--dv-bad:#ef9a9a;--dv-bad-bg:#3a1e1e;
    --dv-gray:#a8bcc6;--dv-gray-bg:#272d33;--dv-amber:#ffb74d;--dv-amber-bg:#3a2a12}}
.dv *{box-sizing:border-box}
.dv-title{font-weight:700;font-size:.95rem;margin:0 0 4px;display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.dv-title .dv-tag{font-size:.68rem;font-weight:700;letter-spacing:.05em;padding:2px 8px;border-radius:99px;background:var(--dv-gray-bg);color:var(--dv-gray)}
.dv-sub{font-size:.83rem;color:var(--dv-mut);margin:0 0 14px}
.dv-cap{font-size:.8rem;color:var(--dv-mut);margin-top:12px;line-height:1.6}

/* 주체 배지 */
.dv-who{display:inline-flex;align-items:center;gap:5px;font-size:.72rem;font-weight:700;
  padding:2px 9px;border-radius:99px;white-space:nowrap;border:1px solid transparent}
.dv-who.dv-c{background:var(--dv-code-bg);color:var(--dv-code);border-color:var(--dv-code)}
.dv-who.dv-l{background:var(--dv-llm-bg);color:var(--dv-llm);border-color:var(--dv-llm)}
.dv-who.dv-e{background:var(--dv-emb-bg);color:var(--dv-emb);border-color:var(--dv-emb)}
.dv-who.dv-h{background:var(--dv-human-bg);color:var(--dv-human);border-color:var(--dv-human)}
.dv-who.dv-f{background:var(--dv-gray-bg);color:var(--dv-gray);border-color:var(--dv-gray)}
.dv-legend{display:flex;gap:9px;flex-wrap:wrap;margin:0 0 14px}

/* 판정 알약 */
.dv-v{display:inline-block;padding:2px 10px;border-radius:99px;font-size:.76rem;font-weight:700;white-space:nowrap}
.dv-v.dv-ok{background:var(--dv-ok-bg);color:var(--dv-ok)}
.dv-v.dv-bad{background:var(--dv-bad-bg);color:var(--dv-bad)}
.dv-v.dv-gray{background:var(--dv-gray-bg);color:var(--dv-gray)}
.dv-v.dv-amber{background:var(--dv-amber-bg);color:var(--dv-amber)}

/* ---- 여정(journey) ---- */
.dv-jrn{position:relative;padding-left:30px}
.dv-jrn:before{content:"";position:absolute;left:9px;top:14px;bottom:14px;width:2px;background:var(--dv-line)}
.dv-stop{position:relative;margin:0 0 12px}
.dv-stop:before{content:"";position:absolute;left:-25px;top:14px;width:12px;height:12px;border-radius:50%;
  background:var(--dv-card);border:2.5px solid var(--dv-line)}
.dv-stop.dv-c:before{border-color:var(--dv-code)}
.dv-stop.dv-l:before{border-color:var(--dv-llm)}
.dv-stop.dv-e:before{border-color:var(--dv-emb)}
.dv-stop.dv-f:before{border-color:var(--dv-gray);background:var(--dv-gray)}
.dv-box{background:var(--dv-card);border:1px solid var(--dv-line);border-radius:10px;overflow:hidden}
.dv-hd{display:flex;gap:9px;align-items:center;flex-wrap:wrap;padding:9px 13px;background:var(--dv-bg);border-bottom:1px solid var(--dv-line)}
.dv-hd .dv-nm{font-weight:700;font-size:.88rem}
.dv-hd .dv-fn{font-family:var(--dv-mono);font-size:.74rem;color:var(--dv-mut);margin-left:auto}
.dv-bd{padding:11px 13px}
.dv-io{display:flex;gap:10px;flex-wrap:wrap;align-items:stretch}
.dv-io>div{flex:1 1 230px;min-width:0}
.dv-io .dv-lb{font-size:.7rem;font-weight:700;color:var(--dv-mut);letter-spacing:.05em;margin-bottom:3px}
.dv-data{font-family:var(--dv-mono);font-size:.73rem;line-height:1.55;background:var(--dv-bg);
  border:1px solid var(--dv-line);border-radius:7px;padding:8px 10px;white-space:pre-wrap;word-break:break-word;overflow-x:auto}
.dv-note{font-size:.8rem;color:var(--dv-mut);margin-top:9px;padding-left:10px;border-left:2px solid var(--dv-line)}
.dv-hi{background:rgba(230,81,0,.15);border-radius:3px;padding:0 3px;font-weight:700}

/* ---- 갈림길(fates) ---- */
.dv-fates{display:flex;gap:12px;flex-wrap:wrap}
.dv-fate{flex:1 1 250px;background:var(--dv-card);border:1px solid var(--dv-line);border-radius:11px;overflow:hidden;display:flex;flex-direction:column}
.dv-fate .dv-fh{padding:9px 13px;font-size:.83rem;font-weight:700;border-bottom:1px solid var(--dv-line)}
.dv-fate.dv-a .dv-fh{background:var(--dv-code-bg);color:var(--dv-code)}
.dv-fate.dv-b .dv-fh{background:var(--dv-llm-bg);color:var(--dv-llm)}
.dv-fate.dv-c2 .dv-fh{background:var(--dv-emb-bg);color:var(--dv-emb)}
.dv-fate .dv-fs{padding:9px 13px;border-bottom:1px dashed var(--dv-line);font-size:.81rem}
.dv-fate .dv-fs:last-child{border-bottom:none;margin-top:auto}
.dv-fate .dv-fs .dv-st{font-size:.68rem;font-weight:700;color:var(--dv-mut);letter-spacing:.04em;display:block;margin-bottom:3px}
.dv-fate .dv-mono{font-family:var(--dv-mono);font-size:.73rem;word-break:break-word}

/* ---- 파이프라인 지도(map) ---- */
.dv-map{display:flex;gap:0;flex-wrap:wrap;align-items:stretch}
.dv-mi{flex:1 1 120px;min-width:118px;background:var(--dv-card);border:1px solid var(--dv-line);
  border-radius:9px;padding:9px 10px;margin:3px;position:relative}
.dv-mi .dv-n{font-size:.68rem;color:var(--dv-mut);font-weight:700}
.dv-mi .dv-t{font-size:.82rem;font-weight:700;margin:2px 0 5px;line-height:1.35}
.dv-mi .dv-f{font-family:var(--dv-mono);font-size:.68rem;color:var(--dv-mut);word-break:break-all;margin-top:5px}
.dv-mi.dv-on{box-shadow:0 0 0 2px var(--dv-llm)}

/* ---- 저울(before/after) ---- */
.dv-ba{display:flex;gap:12px;flex-wrap:wrap;align-items:stretch}
.dv-ba>div{flex:1 1 240px;border:1px solid var(--dv-line);border-radius:11px;overflow:hidden;background:var(--dv-card)}
.dv-ba .dv-h{padding:8px 13px;font-weight:700;font-size:.84rem;border-bottom:1px solid var(--dv-line)}
.dv-ba .dv-before .dv-h{background:var(--dv-bad-bg);color:var(--dv-bad)}
.dv-ba .dv-after .dv-h{background:var(--dv-ok-bg);color:var(--dv-ok)}
.dv-ba .dv-b{padding:10px 13px;font-size:.83rem}
.dv-ba ul{margin:0;padding-left:1.15em}
.dv-ba li{margin:4px 0}

/* ---- 막대(비율) ---- */
.dv-bar{margin:9px 0 4px}
.dv-bar .dv-bl{display:flex;justify-content:space-between;font-size:.79rem;color:var(--dv-mut);margin-bottom:4px}
.dv-bar .dv-bt{display:flex;height:32px;border-radius:7px;overflow:hidden;border:1px solid var(--dv-line)}
.dv-bar .dv-sg{display:flex;align-items:center;justify-content:center;font-size:.74rem;font-weight:700;color:#fff;white-space:nowrap;overflow:hidden;min-width:0}
.dv-bar .dv-sg.dv-c{background:#1565c0}.dv-bar .dv-sg.dv-h{background:#6a1b9a}
.dv-bar .dv-sg.dv-l{background:#e65100}.dv-bar .dv-sg.dv-e{background:#00796b}
.dv-bar .dv-sg.dv-g{background:#78909c}

.dv-scroll{overflow-x:auto}
.dv-tbl{border-collapse:collapse;width:100%;font-size:.83rem;min-width:340px}
.dv-tbl th,.dv-tbl td{border:1px solid var(--dv-line);padding:7px 10px;text-align:left;vertical-align:top}
.dv-tbl th{background:var(--dv-bg);font-size:.78rem}
.dv-tbl td.dv-n,.dv-tbl th.dv-n{text-align:right;font-variant-numeric:tabular-nums}
@media (max-width:640px){.dv{font-size:14px}.dv-hd .dv-fn{margin-left:0;flex-basis:100%}}
"""

# --------------------------------------------------------------------------- #
# 배지
# --------------------------------------------------------------------------- #
WHO = {
    "c": ("c", "⚙️ 코드"),
    "l": ("l", "🤖 LLM"),
    "e": ("e", "🔢 임베딩"),
    "h": ("h", "👤 사람"),
    "f": ("f", "📄 파일"),
}


def who(k: str, label: str | None = None) -> str:
    cls, txt = WHO[k]
    return f'<span class="dv-who dv-{cls}">{label or txt}</span>'


def legend(keys=("f", "c", "l", "e", "h")) -> str:
    return '<div class="dv-legend">' + "".join(who(k) for k in keys) + "</div>"


def _esc(s: str) -> str:
    return html.escape(s, quote=False)


# --------------------------------------------------------------------------- #
# 여정 데이터 — 전부 실제 artifacts 에서 가져온 값
# --------------------------------------------------------------------------- #
STOPS = [
    dict(k="f", n="출발", nm="엑셀 원본 파일", fn="samples/자표준문서.xlsx",
         out='7번째 줄:\n  E7 = "공칭전압"\n  G7 = 3.89        ← 중심치 열\n  K7 = (단위 칸이 비어 있음)',
         note="사람이 만든 표. 값 3.89 는 <b>G열(중심치)</b>에 있고, 이름은 <b>E열(소분류)</b>에 따로 있다. "
              "이 '이름과 값이 다른 칸에 있다'가 나중에 큰 차이를 만든다."),
    dict(k="c", n="1단계", nm="있는 그대로 꺼내기", fn="raw/excel_raw.py → physical_raw.json",
         inp="xlsx 파일 (COM 으로 연다)",
         out='{"address":"E7","row":7,"column":"E",\n "value":"공칭전압","value_type":"str",\n "font_bold":false,"font_size":10.0}\n{"address":"G7","row":7,"column":"G",\n "value":3.89,"value_type":"float"}',
         note="<b>해석하지 않는다.</b> 셀 주소·서식·병합까지 보이는 그대로 받아 적기만 한다. "
              "여기서 판단을 섞으면 나중에 틀렸을 때 원본으로 되돌아갈 수가 없다."),
    dict(k="c", n="2단계", nm="압축하기", fn="raw/excel_raw.py → compact_raw.json",
         inp="physical_raw.json (셀 하나당 객체 하나)",
         out='{"r":7,"cells":{\n  "B":50.0,"C":"기본사양","D":"기본사양",\n  "E":"공칭전압","G":3.89,\n  "O":"C001D001","P":"N019316"}}',
         note="글꼴·색 같은 건 비교에 쓸모없으니 걷어낸다. 이제야 LLM 에게 보여줄 만한 크기가 된다."),
    dict(k="l", n="3단계", nm="열의 뜻을 알아낸다", fn="fact/schema_inducer.py → column_schema.json",
         inp="compact_raw 의 헤더 근처 (표당 1회만)",
         out='{"column":"G","field_name":"중심치",\n "semantic_role":"quantitative_target",\n "data_type":"number"}\n{"column":"K","field_name":"단위",\n "semantic_role":"unit"}',
         note="LLM 이 처음 등장하는 자리. <b>행마다 부르지 않는다</b> — 표 전체에 한 번만 물어보고, "
              "그 답을 20행 모두에 재사용한다. 비용이 여기서 결정된다."),
    dict(k="l", n="4단계", nm="행을 레코드로", fn="fact/record_normalizer.py → records.json",
         inp="7행의 셀들 + 3단계의 열 뜻 (30행씩 묶어 1회)",
         out='{"record_id":"row-7",\n "entity":{"display_name":"공칭전압",\n   "path":["기본사양","C001D001","공칭전압"]},\n "attributes":{"target_value":\n   {"value":3.89,"unit":""}},\n "evidence_text":"3.89"}',
         note='"G열의 3.89" 가 <b>"target_value 는 3.89"</b> 라는 이름 붙은 속성이 됐다. '
              '단위 칸이 비어 있어 <code>unit</code> 도 빈 문자열이다 — 이것도 나중에 중요해진다. '
              '좌표(row 7)는 LLM 이 아니라 <b>코드가 채운다</b>(지어낼 수 없게).'),
    dict(k="c", n="5단계", nm="카드 완성", fn="fact/fact_extractor.py → facts.json",
         inp="records.json",
         out='{"fact_id":"fact-row-7",\n "entity_name":"공칭전압",\n "attributes":{"target_value":\n   {"value":3.89,"unit":""}},\n "search_text":"공칭전압 기본사양 C001D001 3.89",\n "evidence_text":"3.89",\n "source":{"sheet":"데이터","row":7,\n   "cell_range":"B7:O7"}}',
         note="<b>엑셀은 여기서 LLM 을 쓰지 않는다.</b> 레코드를 카드 모양으로 옮기는 건 규칙이라 코드가 한다. "
              "Word/PPT 는 줄글이라 이 자리에서 LLM 이 카드를 뽑는다.<br>"
              "<b>주목:</b> <code>evidence_text</code> 가 <code>\"3.89\"</code> — 낱말 하나뿐이다."),
    dict(k="c", n="6단계", nm="카드 검사", fn="fact/validator.py → validation_report.json",
         inp="facts.json 20장",
         out='error: 0\nwarn : 20  (단위 없음 16, 속성 없음 3,\n           역할 중복 1)\nlow_confidence: 0',
         note="문제가 있어도 <b>버리지 않고 표시만</b> 한다. 버리면 사람이 확인할 기회 자체가 사라지기 때문이다. "
              "표시된 카드는 나중에 판정할 때 '조심하라'는 신호로 쓰인다."),
]


def journey(stops=None, title="추적 여행 — 값 <code>3.89</code> 하나가 겪는 일",
            sub="엑셀 7번째 줄의 공칭전압이 카드가 되기까지. 데이터는 전부 실제 <code>artifacts/자표준문서_xlsx/</code> 산출물이다.",
            cap="여기까지는 <b>모든 대상 문서에 공통</b>이다. 기준 문서를 카드로 만드는 일이므로 상대가 누구든 한 번만 하면 된다. "
                "갈라지는 것은 다음 단계, 짝을 찾는 순간부터다.") -> str:
    """값 3.89 하나가 문서→카드로 변해 가는 단계별 입출력 추적."""
    sel = STOPS if stops is None else [STOPS[i] for i in stops]
    out = [f'<div class="dv"><div class="dv-title">{title}</div>',
           f'<div class="dv-sub">{sub}</div>', legend(("f", "c", "l")),
           '<div class="dv-jrn">']
    for s in sel:
        out.append(f'<div class="dv-stop dv-{s["k"]}"><div class="dv-box">')
        out.append('<div class="dv-hd">' + who(s["k"]) +
                   f'<span class="dv-nm">{s["n"]} · {s["nm"]}</span>'
                   f'<span class="dv-fn">{_esc(s["fn"])}</span></div>')
        out.append('<div class="dv-bd"><div class="dv-io">')
        if s.get("inp"):
            out.append(f'<div><div class="dv-lb">받는 것</div><div class="dv-data">{_esc(s["inp"])}</div></div>')
        out.append(f'<div><div class="dv-lb">{"내놓는 것" if s.get("inp") else "이렇게 생겼다"}</div>'
                   f'<div class="dv-data">{_esc(s["out"])}</div></div>')
        out.append('</div>')
        if s.get("note"):
            out.append(f'<div class="dv-note">{s["note"]}</div>')
        out.append('</div></div></div>')
    out.append('</div>')
    if cap:
        out.append(f'<div class="dv-cap">{cap}</div>')
    out.append('</div>')
    return "\n".join(out)


# --------------------------------------------------------------------------- #
# 갈림길 — 같은 카드, 세 대상 문서, 세 운명
# --------------------------------------------------------------------------- #
FATES = [
    dict(cls="a", doc="📘 자표준_규격서.docx <span style='font-weight:400'>(한국어 워드)</span>",
         rows=[
             ("상대 카드", '<span class="dv-mono">entity_name: "공칭전압"</span><br>값 3.85 V'),
             ("짝 찾기", who("c") + ' 이름이 글자까지 똑같다<br><span class="dv-mono">norm_name 완전일치 → score 1.0</span>'),
             ("검문소", '<span class="dv-v dv-gray">지나지 않음</span> — 코드가 이은 연결은 검증 대상이 아니다'),
             ("값 대조", who("c") + ' 3.89 vs 3.85 · 한쪽만 단위 있음 → 호환으로 봄 → 값이 다름'),
             ("결과", '<span class="dv-v dv-bad">❌ 불일치</span> <span class="dv-mono">decided_by: code</span><br>'
                      '<span class="dv-mono" style="color:var(--dv-mut)">"값이 다릅니다: target_value(기준 3.89 vs 대상 3.85V)"</span><br>'
                      '<b>LLM 을 한 번도 부르지 않았다.</b>'),
         ]),
    dict(cls="b", doc="📗 spec_en.docx <span style='font-weight:400'>(영어 워드)</span>",
         rows=[
             ("상대 카드", '<span class="dv-mono">entity_name: "Nominal voltage"</span><br>값 3.85 V'),
             ("짝 찾기", who("c") + ' 이름 일치 <b>0건</b> → ' + who("e") +
                        '<br><span class="dv-mono">임베딩 0.6589 로 후보에만 오름</span>'),
             ("개념 판정", who("l") + ' → <span class="dv-mono">same_as</span><br>'
                          '인용: <span class="dv-mono">"3.89"</span> / <span class="dv-mono">"The nominal voltage is 3.85V."</span>'),
             ("검문소", '🚧 <b>수정 전</b>: 왼쪽 인용이 낱말 1개 &lt; 3 → <span class="dv-v dv-gray">⚪ 탈락</span> '
                        '→ 연결 소멸 → <b>진짜 오류를 놓쳤다</b><br>'
                        '✅ <b>수정 후</b>: 원문 전체 인용이라 하한 면제 → 통과'),
             ("값 대조", who("l") + ' 연결을 LLM 이 만들었으므로 <span class="dv-mono">needs_review=True</span> → 값도 LLM 이 재확인'),
             ("결과", '<span class="dv-v dv-bad">❌ 불일치</span> <span class="dv-mono">decided_by: llm</span><br>'
                      '<span class="dv-mono" style="color:var(--dv-mut)">"대상은 동일하나 기준 3.89와 후보 3.85가 다릅니다"</span>'),
         ]),
    dict(cls="c2", doc="📙 자표준_발표.pptx <span style='font-weight:400'>(PPT 스피커노트)</span>",
         rows=[
             ("상대 카드", '<span class="dv-mono">entity_name: "충전 규격 상세조건"</span><br>'
                          '속성 2개: 공칭전압 3.89 V, 측정 조건 "0.1C, 4.55V"'),
             ("짝 찾기", who("c") + ' 이름이 아예 다름 → ' + who("e") +
                        '<br><span class="dv-mono">임베딩 0.6973</span>'),
             ("개념 판정", who("l") + ' → <span class="dv-mono">same_as</span>'),
             ("값 대조", who("l") + ' 기준은 속성 1개·단위 없음, 대상은 속성 2개 → 코드가 손을 뗌'),
             ("결과", '<span class="dv-v dv-ok">✅ 일치</span> <span class="dv-mono">decided_by: llm</span><br>'
                      '<span class="dv-mono" style="color:var(--dv-mut)">"3.89와 3.89 V는 단위 표기 차이만 있을 뿐 동일"</span>'),
         ]),
]


def fates(title="같은 카드 하나, 세 갈래 운명",
          sub="위에서 만든 <code>fact-row-7</code>(공칭전압 3.89) 한 장이 <b>대상 문서에 따라 완전히 다른 길</b>을 간다. 판정을 누가 내렸는지에 주목하자.",
          cap="같은 기준 값인데 판정 주체가 <b>코드 → LLM → LLM</b> 으로 달라진다. 이름이 똑같으면 코드가 공짜로 끝내고, "
              "이름이 다를수록 LLM 이 개입하고 검문소가 걸린다. <b>영어 문서가 어려운 이유가 여기에 다 들어 있다</b> — "
              "언어가 다르면 첫 칸(이름 일치)이 절대 성립하지 않아 항상 가장 험한 길로만 간다.") -> str:
    """같은 카드가 대상 문서 3종에서 각각 어떤 길을 가고 누가 판정했나."""
    out = [f'<div class="dv"><div class="dv-title">{title}</div>',
           f'<div class="dv-sub">{sub}</div>', '<div class="dv-fates">']
    for f in FATES:
        out.append(f'<div class="dv-fate dv-{f["cls"]}"><div class="dv-fh">{f["doc"]}</div>')
        for st, body in f["rows"]:
            out.append(f'<div class="dv-fs"><span class="dv-st">{st}</span>{body}</div>')
        out.append('</div>')
    out.append('</div>')
    if cap:
        out.append(f'<div class="dv-cap">{cap}</div>')
    out.append('</div>')
    return "\n".join(out)


# --------------------------------------------------------------------------- #
# 파이프라인 지도
# --------------------------------------------------------------------------- #
MAP_ITEMS = [
    ("F0", "있는 그대로 꺼내기", "c", "physical_raw.json"),
    ("F0", "압축하기", "c", "compact_raw.json"),
    ("F1", "이 문서 뭐야?", "l", "document_profile.json"),
    ("F1", "표·열의 뜻", "l", "table_profile.json<br>column_schema.json"),
    ("F2", "행 → 레코드", "l", "records.json"),
    ("F3", "카드 만들기", "cl", "facts.json"),
    ("F4a", "카드 검사", "c", "validation_report.json"),
    ("F7", "짝 찾기(개념)", "elh", "concept_graph.json"),
    ("F5", "값 대조", "cl", "comparison_result.json"),
    ("F6", "리포트", "c", "report.md"),
]


def pipemap(title="전체 지도 — 어느 단계에서 누가 일하고, 무슨 파일이 남는가",
            sub="문서 하나가 이 열 단계를 지난다. <b>모든 중간 산출물이 파일로 남는 것</b>이 이 설계의 핵심이다 — "
                "틀렸을 때 어느 단계에서 틀렸는지 열어볼 수 있어야 하기 때문이다.",
            cap="LLM 이 개입하는 자리는 <b>네 곳뿐</b>이고, 나머지는 코드가 한다. "
                "그리고 LLM 은 어디서도 '제어 흐름'을 정하지 않는다 — 언제나 <b>단일 목적 JSON 만 만들고 끝</b>이며, "
                "다음에 무엇을 할지는 100% 코드가 정한다.") -> str:
    """10단계 파이프라인 지도 — 단계별 주체 배지와 산출 파일명."""
    out = [f'<div class="dv"><div class="dv-title">{title}</div>',
           f'<div class="dv-sub">{sub}</div>', legend(("c", "l", "e", "h")),
           '<div class="dv-map">']
    for phase, name, kinds, files in MAP_ITEMS:
        badges = "".join(who(k) for k in kinds)
        out.append(f'<div class="dv-mi"><div class="dv-n">{phase}</div>'
                   f'<div class="dv-t">{name}</div>{badges}'
                   f'<div class="dv-f">{files}</div></div>')
    out.append('</div>')
    if cap:
        out.append(f'<div class="dv-cap">{cap}</div>')
    out.append('</div>')
    return "\n".join(out)


# --------------------------------------------------------------------------- #
# 세 갈래 길 SVG (짝 찾기)
# --------------------------------------------------------------------------- #
def lanes(title="짝을 정하는 세 갈래 길 — 검문소는 한 곳에만 있다",
          cap="③번 길에만 검문소가 있는 이유: ①은 코드가 글자를 직접 맞춰 본 것이고 ②는 사람이 확정한 것이라 "
              "<b>지어냈을 걱정이 없다</b>. 검문소는 'AI 가 지어냈는가'만 막는 장치다.") -> str:
    """짝을 정하는 세 갈래 길과 근거 검문소 (SVG)."""
    return f'''<div class="dv"><div class="dv-title">{title}</div>
<figure style="margin:6px 0 0">
<svg viewBox="0 0 760 350" role="img" aria-label="짝을 정하는 세 갈래 길" style="max-width:100%;height:auto">
 <defs><marker id="dvA" markerWidth="9" markerHeight="7" refX="8" refY="3.5" orient="auto">
   <path d="M0,0 L9,3.5 L0,7 z" fill="#78909c"/></marker></defs>
 <rect x="8" y="146" width="116" height="60" rx="10" fill="#eceff1" stroke="#b0bec5"/>
 <text x="66" y="171" font-size="12.5" font-weight="700" text-anchor="middle" fill="#37474f">후보 쌍</text>
 <text x="66" y="191" font-size="11.5" text-anchor="middle" fill="#546e7a">임베딩이 좁힌 것</text>

 <path d="M129,164 C182,164 182,56 236,56" fill="none" stroke="#1565c0" stroke-width="2.5" marker-end="url(#dvA)"/>
 <rect x="244" y="28" width="214" height="56" rx="10" fill="#e3f2fd" stroke="#1565c0" stroke-width="1.5"/>
 <text x="257" y="50" font-size="12.5" font-weight="700" fill="#0d47a1">① 이름이 글자까지 같다</text>
 <text x="257" y="70" font-size="11" fill="#1565c0">⚙️ 코드가 즉시 확정</text>
 <text x="257" y="100" font-size="10.5" font-family="monospace" fill="#607d8b">공칭전압 == 공칭전압 → score 1.0</text>

 <path d="M129,176 C186,176 186,148 236,148" fill="none" stroke="#6a1b9a" stroke-width="2.5" marker-end="url(#dvA)"/>
 <rect x="244" y="120" width="214" height="56" rx="10" fill="#f4e9f7" stroke="#6a1b9a" stroke-width="1.5"/>
 <text x="257" y="142" font-size="12.5" font-weight="700" fill="#4a148c">② 사람이 적어놨다</text>
 <text x="257" y="162" font-size="11" fill="#6a1b9a">👤 knowledge/ontology.yaml</text>
 <text x="257" y="192" font-size="10.5" font-family="monospace" fill="#607d8b">한 번 승격하면 영구 · LLM 을 안 부른다</text>

 <path d="M129,190 C186,190 186,256 236,256" fill="none" stroke="#e65100" stroke-width="2.5" marker-end="url(#dvA)"/>
 <rect x="244" y="228" width="214" height="56" rx="10" fill="#fdf0e3" stroke="#e65100" stroke-width="1.5"/>
 <text x="257" y="250" font-size="12.5" font-weight="700" fill="#bf360c">③ 나머지 → AI 에게 묻는다</text>
 <text x="257" y="270" font-size="11" fill="#e65100">🤖 판단 + 원문 인용을 함께 제출</text>

 <path d="M460,256 L494,256" stroke="#e65100" stroke-width="2.5" marker-end="url(#dvA)"/>
 <rect x="502" y="222" width="98" height="68" rx="10" fill="#fbeaea" stroke="#c62828" stroke-width="2"/>
 <text x="551" y="246" font-size="19" text-anchor="middle">🚧</text>
 <text x="551" y="266" font-size="11.5" font-weight="700" text-anchor="middle" fill="#c62828">근거 검문소</text>
 <text x="551" y="282" font-size="9.5" text-anchor="middle" fill="#c62828">인용이 진짜인가?</text>
 <text x="551" y="312" font-size="10.5" text-anchor="middle" fill="#c62828">탈락 = 연결 소멸 = 「대상에 없음」</text>

 <path d="M458,56 C556,56 588,116 618,152" fill="none" stroke="#78909c" stroke-width="2" marker-end="url(#dvA)"/>
 <path d="M458,148 C536,148 566,152 616,164" fill="none" stroke="#78909c" stroke-width="2" marker-end="url(#dvA)"/>
 <path d="M602,252 C628,252 632,212 636,192" fill="none" stroke="#78909c" stroke-width="2" marker-end="url(#dvA)"/>
 <rect x="626" y="136" width="124" height="58" rx="10" fill="#e2f2f0" stroke="#00796b" stroke-width="1.5"/>
 <text x="688" y="159" font-size="12.5" font-weight="700" text-anchor="middle" fill="#00695c">개념 그래프</text>
 <text x="688" y="178" font-size="10.5" text-anchor="middle" fill="#00695c">같은 것끼리 묶인 덩어리</text>
</svg>
</figure>
<div class="dv-cap">{cap}</div></div>'''


# --------------------------------------------------------------------------- #
# 세 도구 (임베딩 / BM25 / LLM)
# --------------------------------------------------------------------------- #
def tools(title="쓰이는 도구 세 가지 — 잘하는 일이 서로 다르다",
          cap="셋 다 만능이 아니다. 그래서 <b>순서</b>가 중요하다 — 싼 도구로 먼저 좁히고, "
              "비싼 도구는 정말 애매한 것에만 쓴다.") -> str:
    """임베딩 / BM25 / LLM 이 각각 잘하는 것과 못 하는 것."""
    items = [
        ("e", "임베딩", "문장 → 숫자 목록으로 바꿔 '뜻이 가까운 정도'를 잰다",
         "표준환경온도 ↔ Standard ambient temperature = <b>0.7492</b>",
         "글자가 달라도 뜻이 비슷하면 찾아낸다. 언어를 넘는다.",
         "'무관하다'를 말하지 못한다. 정답 최저 0.697 vs 오답 최고 0.700 으로 분포가 겹친다."),
        ("c", "BM25", "겹치는 낱말이 얼마나 되는지 센다 (검색엔진의 고전 방식)",
         "'공칭전압' 이 양쪽에 그대로 있으면 높은 점수",
         "빠르고 공짜다. 임베딩 백엔드가 없을 때 폴백으로 쓴다.",
         "한↔영은 원리적으로 불가능하다. 낱말이 안 겹치니까."),
        ("l", "LLM (채팅)", "사람처럼 읽고 판단한다",
         "'둘 다 공칭전압을 가리킵니다' → same_as",
         "표현이 달라도, 문장에 파묻혀 있어도 이해한다.",
         "느리고 비싸고, 가끔 그럴듯한 거짓말을 한다. 그래서 인용을 요구한다."),
    ]
    out = [f'<div class="dv"><div class="dv-title">{title}</div><div class="dv-fates">']
    for k, nm, what, ex, good, bad in items:
        out.append(f'<div class="dv-fate {"dv-a" if k=="c" else ("dv-b" if k=="l" else "dv-c2")}">'
                   f'<div class="dv-fh">{who(k)} &nbsp;{nm}</div>'
                   f'<div class="dv-fs"><span class="dv-st">하는 일</span>{what}</div>'
                   f'<div class="dv-fs"><span class="dv-st">실제 예</span><span class="dv-mono">{ex}</span></div>'
                   f'<div class="dv-fs"><span class="dv-st">잘하는 것</span>{good}</div>'
                   f'<div class="dv-fs"><span class="dv-st">못하는 것</span>{bad}</div></div>')
    out.append(f'</div><div class="dv-cap">{cap}</div></div>')
    return "\n".join(out)


# --------------------------------------------------------------------------- #
# RAG(사서) vs Fact(카드) 대비
# --------------------------------------------------------------------------- #
def vs(title="두 방식의 결정적 차이 — 무엇과 무엇을 견주는가",
       cap="왼쪽은 <b>모양이 다른 둘</b>을 견준다 — 엑셀 '행'과 워드 '문단 조각'. 조각을 어디서 끊느냐에 따라 "
           "필요한 내용이 잘려 나가고, 표현이 다르면 검색에서 아예 빠진다. "
           "오른쪽은 양쪽을 <b>먼저 같은 모양(카드)으로 만든 뒤</b> 견준다. "
           "그래서 비교가 대칭이 되고, 값은 코드가 직접 대조할 수 있게 된다.") -> str:
    """RAG(행↔청크)와 fact(카드↔카드)의 결정적 차이 (SVG)."""
    return f'''<div class="dv"><div class="dv-title">{title}</div>
<figure style="margin:6px 0 0">
<svg viewBox="0 0 780 340" role="img" aria-label="RAG 방식과 fact 방식의 비교" style="max-width:100%;height:auto">
 <defs><marker id="dvB" markerWidth="9" markerHeight="7" refX="8" refY="3.5" orient="auto">
   <path d="M0,0 L9,3.5 L0,7 z" fill="#8a8a84"/></marker></defs>

 <rect x="6" y="6" width="372" height="328" rx="12" fill="none" stroke="#b23a3a" stroke-width="1.5"/>
 <text x="22" y="30" font-size="13.5" font-weight="700" fill="#b23a3a">지금 방식 · 도서관 사서 (RAG)</text>
 <text x="22" y="49" font-size="11" fill="#8a6a6a">"비슷해 보이는 조각을 찾아다 LLM 에게 읽힌다"</text>

 <rect x="22" y="64" width="150" height="46" rx="7" fill="#e9f0fb" stroke="#7a9cc6"/>
 <text x="32" y="83" font-size="11" fill="#2a4a6a">엑셀 <tspan font-weight="700">행</tspan> 하나</text>
 <text x="32" y="99" font-size="10" font-family="monospace" fill="#5a6a7a">공칭전압 · 3.89</text>

 <rect x="212" y="60" width="144" height="24" rx="5" fill="#f2efe9" stroke="#c9c2b4"/>
 <text x="220" y="76" font-size="9.5" font-family="monospace" fill="#6b6255">…앞 문단의 끝부분</text>
 <rect x="212" y="88" width="144" height="24" rx="5" fill="#f2efe9" stroke="#c9c2b4"/>
 <text x="220" y="104" font-size="9.5" font-family="monospace" fill="#6b6255">공칭전압은 3.85V 이다</text>
 <rect x="212" y="116" width="144" height="24" rx="5" fill="#f2efe9" stroke="#c9c2b4"/>
 <text x="220" y="132" font-size="9.5" font-family="monospace" fill="#6b6255">…다음 문단 시작</text>
 <text x="284" y="55" font-size="10.5" text-anchor="middle" fill="#8a8a84">대상 문서를 잘라 만든 조각들</text>

 <path d="M176,86 C192,86 194,100 208,100" fill="none" stroke="#8a8a84" stroke-width="1.8" marker-end="url(#dvB)"/>
 <text x="192" y="150" font-size="10" text-anchor="middle" fill="#8a8a84">임베딩</text>

 <rect x="22" y="164" width="334" height="62" rx="8" fill="#fbe7e6" stroke="#b23a3a" stroke-dasharray="4 3"/>
 <text x="34" y="183" font-size="11.5" font-weight="700" fill="#b23a3a">모양이 다른 둘을 견준다</text>
 <text x="34" y="200" font-size="10.5" fill="#8a5a5a">행(구조가 있음) ↔ 문단 조각(그냥 글줄)</text>
 <text x="34" y="216" font-size="10.5" fill="#8a5a5a">표현이 다르면 조각이 후보에 아예 안 뜬다 → 「없음」</text>

 <rect x="22" y="238" width="334" height="34" rx="7" fill="#fdf0e3" stroke="#e65100"/>
 <text x="34" y="259" font-size="11" fill="#bf360c">🤖 LLM 이 조각을 읽고 <tspan font-weight="700">혼자 다 판단한다</tspan> (기준 행마다 1회)</text>
 <rect x="22" y="282" width="334" height="34" rx="7" fill="#f2efe9" stroke="#c9c2b4"/>
 <text x="34" y="303" font-size="11" fill="#6b6255">중간 산출물 없음 → 왜 그렇게 나왔는지 되짚을 수 없다</text>

 <rect x="402" y="6" width="372" height="328" rx="12" fill="none" stroke="#2f7a4f" stroke-width="1.5"/>
 <text x="418" y="30" font-size="13.5" font-weight="700" fill="#2f7a4f">새 방식 · 사실 카드 (fact)</text>
 <text x="418" y="49" font-size="11" fill="#5a7a66">"양쪽을 같은 모양으로 만든 뒤 견준다"</text>

 <rect x="418" y="64" width="150" height="60" rx="7" fill="#e9f0fb" stroke="#7a9cc6"/>
 <text x="428" y="82" font-size="10" fill="#5a6a7a">엑셀 행 → 카드</text>
 <text x="428" y="98" font-size="9.5" font-family="monospace" fill="#2a4a6a">항목: 공칭전압</text>
 <text x="428" y="113" font-size="9.5" font-family="monospace" fill="#2a4a6a">값: 3.89 · 단위: —</text>

 <rect x="606" y="64" width="150" height="60" rx="7" fill="#e4f3e9" stroke="#69a882"/>
 <text x="616" y="82" font-size="10" fill="#5a7a66">워드 문단 → 카드</text>
 <text x="616" y="98" font-size="9.5" font-family="monospace" fill="#2a5a3a">항목: Nominal voltage</text>
 <text x="616" y="113" font-size="9.5" font-family="monospace" fill="#2a5a3a">값: 3.85 · 단위: V</text>

 <path d="M572,94 L602,94" stroke="#2f7a4f" stroke-width="2" marker-end="url(#dvB)"/>
 <path d="M602,104 L572,104" stroke="#2f7a4f" stroke-width="2" marker-end="url(#dvB)"/>
 <text x="587" y="140" font-size="10" text-anchor="middle" fill="#2f7a4f">양방향</text>

 <rect x="418" y="150" width="338" height="48" rx="8" fill="#e4f3e9" stroke="#2f7a4f" stroke-dasharray="4 3"/>
 <text x="430" y="169" font-size="11.5" font-weight="700" fill="#2f7a4f">모양이 같으니 대칭으로 견줄 수 있다</text>
 <text x="430" y="187" font-size="10.5" fill="#5a7a66">칸이 정해져 있어 「값」끼리, 「단위」끼리 맞대볼 수 있다</text>

 <rect x="418" y="210" width="338" height="34" rx="7" fill="#e2f2f0" stroke="#00796b"/>
 <text x="430" y="231" font-size="11" fill="#00695c">① 짝이 맞는가? — 개념 그래프가 정한다 (값은 안 본다)</text>
 <rect x="418" y="252" width="338" height="34" rx="7" fill="#e8f1fb" stroke="#1565c0"/>
 <text x="430" y="273" font-size="11" fill="#0d47a1">② 값이 같은가? — ⚙️ <tspan font-weight="700">코드가 70% 를 직접 판정</tspan></text>
 <rect x="418" y="294" width="338" height="30" rx="7" fill="#f2efe9" stroke="#c9c2b4"/>
 <text x="430" y="314" font-size="11" fill="#6b6255">단계마다 JSON 파일이 남는다 → 어디서 틀렸는지 열어볼 수 있다</text>
</svg>
</figure>
<div class="dv-cap">{cap}</div></div>'''


# --------------------------------------------------------------------------- #
# 삽입 엔진
# --------------------------------------------------------------------------- #
def ensure_css(s: str) -> str:
    if CSS_MARKER in s:
        return s
    i = s.find("</style>")
    if i < 0:
        raise ValueError("no </style>")
    return s[:i] + "\n" + CSS + "\n" + s[i:]


def insert_after_heading(s: str, needle: str, block: str, level: str = "2") -> str:
    """제목 텍스트에 needle 이 들어 있는 h{level} 요소 바로 뒤에 block 을 넣는다."""
    for m in re.finditer(rf"<h{level}[^>]*>(.*?)</h{level}>", s, re.S):
        plain = re.sub(r"<[^>]+>", "", m.group(1))
        plain = re.sub(r"\s+", " ", plain)
        if needle in plain:
            at = m.end()
            return s[:at] + "\n" + block + "\n" + s[at:]
    raise ValueError(f"heading not found: {needle!r}")


def insert_before(s: str, needle: str, block: str) -> str:
    i = s.find(needle)
    if i < 0:
        raise ValueError(f"anchor not found: {needle!r}")
    return s[:i] + block + "\n" + s[i:]


def insert_after_str(s: str, needle: str, block: str) -> str:
    i = s.find(needle)
    if i < 0:
        raise ValueError(f"anchor not found: {needle!r}")
    at = i + len(needle)
    return s[:at] + "\n" + block + s[at:]


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
BLOCKS = {
    "journey": journey,
    "fates": fates,
    "pipemap": pipemap,
    "lanes": lanes,
    "tools": tools,
    "vs": vs,
}


def _main(argv: list[str] | None = None) -> int:
    import argparse

    # Windows 콘솔 기본 코드페이지(cp949)는 설명문의 '—' 같은 문자를 못 찍는다.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    ap = argparse.ArgumentParser(
        description="설명 페이지에 공용 다이어그램 블록을 넣는다.")
    ap.add_argument("page", nargs="?", help="대상 html 경로")
    ap.add_argument("--block", choices=sorted(BLOCKS), help="넣을 컴포넌트")
    ap.add_argument("--after", help="이 텍스트가 들어 있는 제목 바로 뒤에 넣는다")
    ap.add_argument("--level", default="2", choices=["2", "3"], help="제목 수준(기본 h2)")
    ap.add_argument("--list", action="store_true", help="컴포넌트 목록만 출력")
    a = ap.parse_args(argv)

    if a.list or not a.page:
        for name, fn in sorted(BLOCKS.items()):
            first = (fn.__doc__ or "").strip().splitlines()[0] if fn.__doc__ else ""
            print(f"  {name:<9} {first}")
        return 0
    if not a.block or not a.after:
        ap.error("--block 과 --after 가 함께 필요합니다 (--list 로 목록 확인)")

    with open(a.page, encoding="utf-8") as fh:
        s = fh.read()
    before = len(s)
    s = ensure_css(s)
    s = insert_after_heading(s, a.after, BLOCKS[a.block](), a.level)
    with open(a.page, "w", encoding="utf-8") as fh:
        fh.write(s)
    print(f"OK  {a.page}: +{len(s) - before} bytes ({a.block} → h{a.level} '{a.after}')")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
