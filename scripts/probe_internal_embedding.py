"""사내 임베딩 API(bge-m3)와 리랭커 API(bge-reranker-v2-m3)를 쓸 수 있는지 확인한다.

**일회용 프로브다.** 설정 값을 확정하고 함정을 미리 드러내기 위한 것이고, 파이프라인은
이 파일을 쓰지 않는다.

네 가지를 순서대로 본다. **순서에 의미가 있다** — 앞이 실패하면 뒤 결과는 무의미하다:

    A. 원시 HTTP    curl 예시를 그대로 재현한다. base_url·api_key·model 확정.
    B. internal     ``InternalBackend.embed()`` 경로. A 와 결과가 같아야 한다.
    C. langchain    ``OpenAIEmbeddings`` 경로. ⚠️ **여기가 함정이다**(아래).
    D. 리랭커       엔드포인트 모양을 모르므로 후보를 하나씩 두드려 본다.

⚠️ **C 를 반드시 확인해야 하는 이유.** langchain 의 ``OpenAIEmbeddings`` 는 기본값
``check_embedding_ctx_length=True`` 로 동작하는데, 그러면 텍스트를 tiktoken 으로 먼저
쪼개어 **문자열이 아니라 토큰 ID 배열**을 ``input`` 에 실어 보낸다. OpenAI 서버는 그것을
받지만 bge-m3 같은 비-OpenAI 서버는 대개 못 받거나, 받아도 **엉뚱한 벡터**를 돌려준다.
게다가 tiktoken 은 ``/models/embedding/bge-m3`` 라는 모델명을 몰라 인코딩 추정도 빗나간다.
그래서 C 는 "붙는가"가 아니라 **"A 와 같은 벡터가 나오는가"** 를 본다 — 200 을 받고도
값이 다르면 그게 더 나쁘다(조용히 틀린 검색 결과가 된다).

실행:
    python scripts/probe_internal_embedding.py --config config/config.yaml

    # 모델명·리랭커 주소를 바꿔 가며
    python scripts/probe_internal_embedding.py --embed-model /models/embedding/bge-m3 \
        --rerank-model /models/reranker/bge-reranker-v2-m3
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import requests  # noqa: E402

from contentcompare.config import AppConfig, no_proxy  # noqa: E402
from contentcompare.timeline import console_safe  # noqa: E402


def _say(text: str = "") -> None:
    """화면 출력. cp949 콘솔이 못 쓰는 문자만 바꾼다(``print`` 직접 호출 금지)."""
    print(console_safe(text, getattr(sys.stdout, "encoding", None)))


# --------------------------------------------------------------------------- #
# 프로브 입력 — 의미가 가까운 둘 + 먼 하나. 벡터가 제대로 오면 유사도로 드러난다.
# --------------------------------------------------------------------------- #
TEXTS = [
    "충전환경온도는 -5도에서 55도입니다.",
    "Charging ambient temperature is -5 to 55 degrees.",
    "오늘 아침에 산책을 했다.",
]
QUERY = "충전 환경 온도 범위"

#: 리랭커 엔드포인트 후보. 사내 게이트웨이가 어떤 규격인지 모르므로 흔한 셋을 두드린다.
#: ``body`` 는 그 규격의 요청 모양이다(``{q}``/``{docs}``/``{model}`` 가 치환된다).
RERANK_SHAPES = [
    ("cohere 규격  /rerank",
     "{base}/rerank",
     lambda m, q, d: {"model": m, "query": q, "documents": d}),
    ("cohere 규격  (v1 밖)",
     "{root}/rerank",
     lambda m, q, d: {"model": m, "query": q, "documents": d}),
    ("TEI 규격     /rerank",
     "{base}/rerank",
     lambda m, q, d: {"query": q, "texts": d}),
    ("TEI 규격     (v1 밖)",
     "{root}/rerank",
     lambda m, q, d: {"query": q, "texts": d}),
]


def _headers(cfg: AppConfig) -> dict[str, str]:
    """``InternalBackend._headers`` 와 같은 규칙 — 직접 지정 키 우선, 없으면 환경변수."""
    headers = {"Content-Type": "application/json"}
    internal = cfg.llm.internal
    api_key = internal.api_key or os.environ.get(internal.api_key_env, "")
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


class _null_ctx:
    def __enter__(self) -> None:
        return None

    def __exit__(self, *exc: object) -> bool:
        return False


def _post(cfg: AppConfig, url: str, payload: dict) -> tuple[Optional[int], Any, str]:
    """(상태코드, 파싱된 JSON, 본문 요약). 예외도 결과로 돌려준다 — 뒤 항목을 계속 본다."""
    ctx = no_proxy() if cfg.llm.internal.unset_proxy else _null_ctx()
    try:
        with ctx:
            resp = requests.post(url, json=payload, headers=_headers(cfg),
                                 timeout=cfg.llm.timeout,
                                 verify=cfg.llm.internal.verify_ssl)
    except Exception as exc:  # noqa: BLE001 — 예외 자체가 결과다
        return None, None, f"{type(exc).__name__}: {exc}"
    try:
        return resp.status_code, resp.json(), ""
    except Exception:  # noqa: BLE001 — JSON 이 아닌 에러 페이지
        return resp.status_code, None, _clip(resp.text)


def _clip(text: str, limit: int = 400) -> str:
    text = (text or "").strip().replace("\n", " ")
    return text if len(text) <= limit else text[:limit] + f"… (총 {len(text)}자)"


def _cos(a: list, b: list) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    return dot / (na * nb) if na and nb else 0.0


def _vectors_from(data: Any) -> Optional[list[list[float]]]:
    """OpenAI 임베딩 응답 → 벡터 목록(입력 순서). 모양이 다르면 ``None``."""
    try:
        items = sorted(data["data"], key=lambda d: d.get("index", 0))
        return [d["embedding"] for d in items]
    except (KeyError, TypeError, IndexError):
        return None


# --------------------------------------------------------------------------- #
# A. 원시 HTTP — curl 예시 그대로
# --------------------------------------------------------------------------- #
def probe_raw(cfg: AppConfig, base: str, model: str) -> Optional[list[list[float]]]:
    _say(f"\n{'=' * 72}\n[A] 원시 HTTP — curl 예시 그대로")
    url = f"{base}/embeddings"
    _say(f"  POST {url}")
    _say(f'  model = "{model}"')
    status, data, body = _post(cfg, url, {"model": model, "input": TEXTS})
    _say(f"  status : {status if status is not None else '(요청 실패)'}")
    if status is None or status >= 400 or data is None:
        _say(f"  본문   : {body or _clip(json.dumps(data, ensure_ascii=False))}")
        _say("  ✗ 여기서 실패하면 아래 결과는 전부 무의미하다 — base_url·키·모델명부터 확인할 것.")
        return None
    vecs = _vectors_from(data)
    if not vecs:
        _say(f"  ✗ 응답 모양이 OpenAI 규격이 아니다: {_clip(json.dumps(data, ensure_ascii=False))}")
        return None
    _say(f"  ✓ 통과 — 벡터 {len(vecs)}개, 차원 {len(vecs[0])}")
    _say(f"  의미 검증(값이 진짜인지):")
    _say(f"    한글 충전온도 ↔ 영문 충전온도 = {_cos(vecs[0], vecs[1]):.3f}  (높아야 정상)")
    _say(f"    한글 충전온도 ↔ 산책        = {_cos(vecs[0], vecs[2]):.3f}  (낮아야 정상)")
    if _cos(vecs[0], vecs[1]) <= _cos(vecs[0], vecs[2]):
        _say("    ⚠️ 순서가 뒤집혔다 — 200 을 받았어도 벡터가 쓸모없다(모델명·입력 형식 의심).")
    return vecs


# --------------------------------------------------------------------------- #
# B. internal 백엔드 경로
# --------------------------------------------------------------------------- #
def probe_internal(cfg: AppConfig, ref: Optional[list[list[float]]]) -> None:
    _say(f"\n{'=' * 72}\n[B] internal 백엔드 — contentcompare 코드 경로")
    from contentcompare.llm.internal import InternalBackend

    try:
        vecs = InternalBackend(cfg.llm).embed(TEXTS, kind="passage")
    except Exception as exc:  # noqa: BLE001
        _say(f"  ✗ 실패 — {type(exc).__name__}: {_clip(str(exc))}")
        return
    _say(f"  ✓ 통과 — 벡터 {len(vecs)}개, 차원 {len(vecs[0]) if vecs else 0}")
    if ref:
        same = all(abs(a - b) < 1e-6 for x, y in zip(vecs, ref) for a, b in zip(x, y))
        _say(f"  A 와 동일한 벡터인가 : {'✓ 같음' if same else '✗ 다름 — 조사 필요'}")


# --------------------------------------------------------------------------- #
# C. langchain 백엔드 경로 — ⚠️ 토큰 ID 함정
# --------------------------------------------------------------------------- #
def probe_langchain(cfg: AppConfig, ref: Optional[list[list[float]]]) -> None:
    _say(f"\n{'=' * 72}\n[C] langchain 백엔드 — ⚠️ 토큰 ID 함정 확인")
    try:
        from langchain_openai import OpenAIEmbeddings
    except ImportError as exc:
        _say(f"  - 건너뜀(langchain 미설치): {exc}")
        return

    internal = cfg.llm.internal
    key = internal.api_key or os.environ.get(internal.api_key_env, "") or "sk-none"
    common = dict(model=cfg.llm.embed_model, base_url=internal.base_url,
                  api_key=key, timeout=cfg.llm.timeout)

    for label, extra in (
        ("기본값 (check_embedding_ctx_length=True — 토큰 ID 를 보낸다)", {}),
        ("수정안 (check_embedding_ctx_length=False — 문자열을 보낸다)",
         {"check_embedding_ctx_length": False}),
    ):
        _say(f"\n  · {label}")
        try:
            ctx = no_proxy() if internal.unset_proxy else _null_ctx()
            with ctx:
                vecs = OpenAIEmbeddings(**common, **extra).embed_documents(list(TEXTS))
        except Exception as exc:  # noqa: BLE001
            _say(f"    ✗ 실패 — {type(exc).__name__}: {_clip(str(exc))}")
            continue
        _say(f"    응답 : 벡터 {len(vecs)}개, 차원 {len(vecs[0]) if vecs else 0}")
        if ref:
            same = all(abs(a - b) < 1e-4
                       for x, y in zip(vecs, ref) for a, b in zip(x, y))
            mark = "✓ A 와 같음" if same else "✗ **A 와 다르다** — 200 이어도 못 쓴다"
            _say(f"    검증 : {mark}")
        _say(f"    교차언어 유사도 : {_cos(vecs[0], vecs[1]):.3f}"
             f"  vs 무관한 문장 {_cos(vecs[0], vecs[2]):.3f}")


# --------------------------------------------------------------------------- #
# D. 리랭커 — 엔드포인트 모양을 모르므로 후보를 두드린다
# --------------------------------------------------------------------------- #
def probe_rerank(cfg: AppConfig, base: str, model: str) -> None:
    _say(f"\n{'=' * 72}\n[D] 리랭커(bge-reranker-v2-m3) — 엔드포인트 탐색")
    _say(f'  model = "{model}"')
    root = base.rsplit("/v1", 1)[0] if "/v1" in base else base
    hit = False
    for label, tmpl, make_body in RERANK_SHAPES:
        url = tmpl.format(base=base, root=root)
        status, data, body = _post(cfg, url, make_body(model, QUERY, TEXTS))
        ok = status is not None and status < 400 and data is not None
        _say(f"\n  · {label}\n    POST {url}\n    status : {status}")
        if not ok:
            _say(f"    본문   : {_clip(body or json.dumps(data, ensure_ascii=False))}")
            continue
        hit = True
        _say(f"    ✓ 응답 : {_clip(json.dumps(data, ensure_ascii=False))}")
        _say("    → 이 모양이 우리 게이트웨이의 규격이다. 점수 순서가 "
             "'충전온도(한/영) > 산책' 이면 정상.")
    if not hit:
        _say("\n  ✗ 후보 넷이 전부 실패했다. 사내 API 문서에서 rerank 경로와 요청 모양을 "
             "확인해 --rerank-url 로 직접 지정해 다시 돌려 볼 것.")


# --------------------------------------------------------------------------- #
def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(
        description="사내 임베딩·리랭커 API 연결 확인용 일회용 프로브")
    p.add_argument("--config", default="config/config.yaml", help="설정 파일 경로")
    p.add_argument("--embed-model", default="", help="임베딩 모델명(비우면 config)")
    p.add_argument("--rerank-model", default="/models/reranker/bge-reranker-v2-m3",
                   help="리랭커 모델명")
    p.add_argument("--rerank-url", default="", help="리랭커 URL 직접 지정(탐색 대신)")
    p.add_argument("--skip", default="", help="건너뛸 단계: a,b,c,d 중 콤마로")
    args = p.parse_args(argv)

    cfg = AppConfig.load(args.config)
    base = cfg.llm.internal.base_url.rstrip("/")
    embed_model = args.embed_model or cfg.llm.embed_model
    skip = {s.strip().lower() for s in args.skip.split(",") if s.strip()}

    _say(f"설정     : {args.config}")
    _say(f"base_url : {base}")
    _say(f"임베딩모델: {embed_model}")
    _say(f"인증     : {'Authorization 헤더 있음' if 'Authorization' in _headers(cfg) else '⚠️ 키 없음'}")
    _say(f"verify_ssl={cfg.llm.internal.verify_ssl}  unset_proxy={cfg.llm.internal.unset_proxy}")

    ref = None
    if "a" not in skip:
        ref = probe_raw(cfg, base, embed_model)
    if "b" not in skip:
        probe_internal(cfg, ref)
    if "c" not in skip:
        probe_langchain(cfg, ref)
    if "d" not in skip:
        if args.rerank_url:
            RERANK_SHAPES[:] = [(s[0], args.rerank_url, s[2]) for s in RERANK_SHAPES[:2]]
        probe_rerank(cfg, base, args.rerank_model)

    _say(f"\n{'=' * 72}\n다음 단계")
    _say("  A 통과 + B 통과 + C 의 '수정안'만 통과  → langchain 경로에 "
         "check_embedding_ctx_length=False 배선이 필요하다(코드 수정).")
    _say("  A 통과 + C 의 '기본값'도 통과            → 함정이 없다. 설정만 맞추면 된다.")
    _say("  A 실패                                   → base_url·api_key·모델명부터 확인.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
