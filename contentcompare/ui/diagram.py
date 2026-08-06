"""공용 다이어그램 컴포넌트 — 데이터를 받아 HTML 조각을 만든다.

원래 ``scripts/doc_diagrams.py`` 에 있던 것을 패키지로 옮겼다. 이유는 하나다:
``scripts/`` 는 패키지가 아니라 앱(``app/streamlit_app.py``)에서 import 할 수 없는데,
파이프라인 현미경이 설명 페이지와 **같은 시각 언어**를 써야 하기 때문이다. 독자가
문서에서 배운 색과 배지가 화면에서 다르면 다시 배워야 한다.

여기 있는 것은 **데이터를 받는 렌더러**뿐이다. 문서의 서술 데이터
(``STOPS``/``FATES``/``MAP_ITEMS``)와 페이지 삽입 엔진은 ``scripts/doc_diagrams.py``
에 그대로 남아 있고, 그쪽이 이 모듈을 호출한다.

설계 규칙 셋(원본에서 승계):

1. **모든 클래스는 ``dv-`` 접두어.** 페이지마다 CSS 가 제각각이라 짧은 클래스명은
   충돌한다. 색·다크모드 변수도 ``.dv`` 스코프가 자체적으로 들고 있어 어디에 넣어도 같다.
2. **주체 색을 고정한다.** ⚙️코드=파랑 · 🤖LLM=주황 · 🔢임베딩=청록 · 👤사람=보라 · 📄파일=회색.
3. **의존성 0.** 문자열 조립만 한다 — streamlit 도, 템플릿 엔진도 쓰지 않는다.
"""

from __future__ import annotations

import html
from typing import Any, Iterable, Mapping, Optional, Sequence

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
.dv-stop.dv-h:before{border-color:var(--dv-human)}
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
.dv-fate.dv-ok .dv-fh{background:var(--dv-ok-bg);color:var(--dv-ok)}
.dv-fate.dv-bad .dv-fh{background:var(--dv-bad-bg);color:var(--dv-bad)}
.dv-fate.dv-gray .dv-fh{background:var(--dv-gray-bg);color:var(--dv-gray)}
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
.dv-mi.dv-off{opacity:.42}

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

/* ---- 흔적(trail) — 게이트별 통과/차단 ---- */
.dv-trail{display:flex;gap:6px;flex-wrap:wrap;align-items:center;margin:8px 0}
.dv-tr{display:inline-flex;align-items:center;gap:6px;padding:4px 10px;border-radius:8px;
  border:1px solid var(--dv-line);background:var(--dv-card);font-size:.77rem}
.dv-tr.dv-fail{border-color:var(--dv-bad);background:var(--dv-bad-bg);color:var(--dv-bad);font-weight:700}
.dv-tr .dv-trn{color:var(--dv-mut);font-size:.72rem}
.dv-tr.dv-fail .dv-trn{color:var(--dv-bad)}
.dv-arrow{color:var(--dv-mut);font-size:.8rem}

.dv-scroll{overflow-x:auto}
.dv-tbl{border-collapse:collapse;width:100%;font-size:.83rem;min-width:340px}
.dv-tbl th,.dv-tbl td{border:1px solid var(--dv-line);padding:7px 10px;text-align:left;vertical-align:top}
.dv-tbl th{background:var(--dv-bg);font-size:.78rem}
.dv-tbl td.dv-n,.dv-tbl th.dv-n{text-align:right;font-variant-numeric:tabular-nums}
@media (max-width:640px){.dv{font-size:14px}.dv-hd .dv-fn{margin-left:0;flex-basis:100%}}
"""

# --------------------------------------------------------------------------- #
# 배지 — 주체=색 규약의 단일 출처
# --------------------------------------------------------------------------- #
WHO = {
    "c": ("c", "⚙️ 코드"),
    "l": ("l", "🤖 LLM"),
    "e": ("e", "🔢 임베딩"),
    "h": ("h", "👤 사람"),
    "f": ("f", "📄 파일"),
}

# 진단 코드(``missing_trace`` 의 ``trail[].who``) → 배지 키.
WHO_ALIAS = {
    "code": "c", "llm": "l", "embed": "e", "human": "h", "file": "f",
    "graph": "c", "ontology": "h",
}


def who(k: str, label: Optional[str] = None) -> str:
    """주체 배지 하나. 모르는 키는 회색 파일 배지로 떨어진다(화면이 깨지지 않게)."""
    cls, txt = WHO.get(WHO_ALIAS.get(k, k), WHO["f"])
    return f'<span class="dv-who dv-{cls}">{label or txt}</span>'


def legend(keys: Iterable[str] = ("f", "c", "l", "e", "h")) -> str:
    return '<div class="dv-legend">' + "".join(who(k) for k in keys) + "</div>"


def esc(s: Any) -> str:
    """텍스트를 HTML 에 넣기 전에 이스케이프한다.

    문서 원문에는 ``<``·``&`` 가 실제로 들어온다 — 이걸 빼먹으면 화면이 깨지는 정도가
    아니라 임의 마크업이 주입된다.
    """
    return html.escape("" if s is None else str(s), quote=False)


# 하위호환 — 원본 모듈이 쓰던 이름.
_esc = esc


def _wrap(title: str, sub: str, body: list[str], cap: str) -> str:
    """공통 껍데기.

    제목을 여는 ``div`` 와 **같은 줄**에 붙이는 것은 원본(``scripts/doc_diagrams.py``)의
    출력과 바이트 단위로 같게 유지하기 위해서다 — 이미 발행된 설명 페이지들을 다시
    생성해도 diff 가 0 이어야 이식이 안전하다는 것을 증명할 수 있다.
    """
    head = '<div class="dv">'
    if title:
        head += f'<div class="dv-title">{title}</div>'
    out = [head]
    if sub:
        out.append(f'<div class="dv-sub">{sub}</div>')
    out.extend(body)
    if cap:
        out.append(f'<div class="dv-cap">{cap}</div>')
    out.append("</div>")
    return "\n".join(out)


# --------------------------------------------------------------------------- #
# 여정 — 단계별 입력/출력 추적
# --------------------------------------------------------------------------- #
def journey(
    stops: Sequence[Mapping[str, Any]],
    *,
    title: str = "",
    sub: str = "",
    cap: str = "",
    legend_keys: Iterable[str] = ("f", "c", "l"),
) -> str:
    """단계별 '받는 것 → 내놓는 것' 추적.

    ``stops`` 항목: ``{k(주체), n(단계), nm(이름), fn(파일), inp?, out, note?, extra?}``.
    ``inp``/``out`` 은 **원문 데이터**라 이스케이프하고, ``note``/``extra`` 는 설명용
    HTML 이라 그대로 둔다.
    """
    body = [legend(legend_keys), '<div class="dv-jrn">']
    for s in stops:
        kind = str(s.get("k") or "c")
        body.append(f'<div class="dv-stop dv-{WHO_ALIAS.get(kind, kind)}"><div class="dv-box">')
        body.append(
            '<div class="dv-hd">' + who(kind)
            + f'<span class="dv-nm">{esc(s.get("n"))} · {esc(s.get("nm"))}</span>'
            + f'<span class="dv-fn">{esc(s.get("fn"))}</span></div>'
        )
        body.append('<div class="dv-bd"><div class="dv-io">')
        if s.get("inp"):
            body.append('<div><div class="dv-lb">받는 것</div>'
                        f'<div class="dv-data">{esc(s["inp"])}</div></div>')
        body.append(f'<div><div class="dv-lb">{"내놓는 것" if s.get("inp") else "이렇게 생겼다"}</div>'
                    f'<div class="dv-data">{esc(s.get("out"))}</div></div>')
        body.append("</div>")
        if s.get("note"):
            body.append(f'<div class="dv-note">{s["note"]}</div>')
        if s.get("extra"):
            body.append(str(s["extra"]))
        body.append("</div></div></div>")
    body.append("</div>")
    return _wrap(title, sub, body, cap)


# --------------------------------------------------------------------------- #
# 갈림길 — 같은 것이 상대에 따라 다른 길을 간다
# --------------------------------------------------------------------------- #
def fates(
    cards: Sequence[Mapping[str, Any]],
    *,
    title: str = "",
    sub: str = "",
    cap: str = "",
) -> str:
    """카드 여러 장을 나란히. 항목: ``{cls, doc, rows: [(제목, 본문HTML), ...]}``."""
    body = ['<div class="dv-fates">']
    for card in cards:
        body.append(f'<div class="dv-fate dv-{card.get("cls", "a")}">'
                    f'<div class="dv-fh">{card.get("doc", "")}</div>')
        for label, content in card.get("rows") or []:
            body.append(f'<div class="dv-fs"><span class="dv-st">{label}</span>{content}</div>')
        body.append("</div>")
    body.append("</div>")
    return _wrap(title, sub, body, cap)


# --------------------------------------------------------------------------- #
# 파이프라인 지도
# --------------------------------------------------------------------------- #
def pipemap(
    items: Sequence[Sequence[str]],
    *,
    active: Optional[Iterable[str]] = None,
    dimmed: Optional[Iterable[str]] = None,
    title: str = "",
    sub: str = "",
    cap: str = "",
    legend_keys: Iterable[str] = ("c", "l", "e", "h"),
) -> str:
    """단계 타일 지도. 항목: ``(phase, name, kinds, files)``.

    ``active``/``dimmed`` 는 **파일명**(``files`` 의 첫 줄)이나 단계 이름의 집합이다 —
    실제로 돌아간 단계만 진하게, 이 문서 유형에 없는 단계는 흐리게 그린다.
    """
    active = set(active or ())
    dimmed = set(dimmed or ())
    body = [legend(legend_keys), '<div class="dv-map">']
    for phase, name, kinds, files in items:
        badges = "".join(who(k) for k in kinds)
        key = files.split("<br>")[0].replace(".json", "").replace(".md", "")
        cls = " dv-on" if (key in active or name in active) else ""
        cls += " dv-off" if (key in dimmed or name in dimmed) else ""
        body.append(f'<div class="dv-mi{cls}"><div class="dv-n">{phase}</div>'
                    f'<div class="dv-t">{name}</div>{badges}'
                    f'<div class="dv-f">{files}</div></div>')
    body.append("</div>")
    return _wrap(title, sub, body, cap)


# --------------------------------------------------------------------------- #
# 흔적 — 게이트별 통과/차단
# --------------------------------------------------------------------------- #
def trail(steps: Sequence[Mapping[str, Any]]) -> str:
    """``missing_trace.MissingTrace.trail`` 을 가로 스텝 바로 그린다.

    **실패한 칸에만** 빨강을 칠한다 — 원인이 색 하나로 읽혀야 한다.
    """
    parts = ['<div class="dv-trail">']
    for i, step in enumerate(steps):
        if i:
            parts.append('<span class="dv-arrow">→</span>')
        ok = bool(step.get("ok"))
        cls = "dv-tr" if ok else "dv-tr dv-fail"
        mark = "✔" if ok else "✖"
        parts.append(
            f'<span class="{cls}">{who(str(step.get("who") or "c"))}'
            f'<span>{mark} {esc(step.get("stage"))}</span>'
            f'<span class="dv-trn">{esc(step.get("note"))}</span></span>'
        )
    parts.append("</div>")
    return "".join(parts)


def table(headers: Sequence[str], rows: Sequence[Sequence[str]],
          *, numeric: Iterable[int] = ()) -> str:
    """가로 스크롤 되는 표(본문은 **이스케이프하지 않는다** — 호출자가 HTML 을 넣는다)."""
    numeric = set(numeric)
    head = "".join(
        f'<th class="dv-n">{h}</th>' if i in numeric else f"<th>{h}</th>"
        for i, h in enumerate(headers)
    )
    body = "".join(
        "<tr>" + "".join(
            f'<td class="dv-n">{c}</td>' if i in numeric else f"<td>{c}</td>"
            for i, c in enumerate(row)
        ) + "</tr>"
        for row in rows
    )
    return (f'<div class="dv-scroll"><table class="dv-tbl"><thead><tr>{head}</tr></thead>'
            f"<tbody>{body}</tbody></table></div>")


def pill(text: str, kind: str = "gray") -> str:
    """판정 알약 — ``ok`` | ``bad`` | ``amber`` | ``gray``."""
    return f'<span class="dv-v dv-{kind}">{esc(text)}</span>'


def data_block(text: Any) -> str:
    """원문 데이터 블록(줄바꿈 보존, 이스케이프)."""
    return f'<div class="dv-data">{esc(text)}</div>'


__all__ = [
    "CSS", "CSS_MARKER", "WHO", "WHO_ALIAS",
    "data_block", "esc", "fates", "journey", "legend", "pill", "pipemap",
    "table", "trail", "who",
]
