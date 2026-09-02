"""사내 임베딩 API(bge-m3)와 리랭커 API(bge-reranker-v2-m3)를 쓸 수 있는지 확인한다.

**일회용 프로브다.** 설정 값을 확정하고 함정을 미리 드러내기 위한 것이고, 파이프라인은
이 파일을 쓰지 않는다.

네 가지를 순서대로 본다. **순서에 의미가 있다** — 앞이 실패하면 뒤 결과는 무의미하다:

    A. 원시 HTTP    curl 예시를 그대로 재현한다. base_url·api_key·model 확정.
    B. internal     ``InternalBackend.embed()`` 경로. A 와 결과가 같아야 한다.
    C. langchain    ``OpenAIEmbeddings`` 경로. ⚠️ **여기가 함정이다**(아래).
    D. 리랭커       후보를 하나씩 두드려 본다. 사내는 "cohere 규격 (v1 밖)" 이다 —
                    임베딩은 {root}/v1/embeddings, 리랭커는 {root}/rerank 로 갈린다.

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

#: 리랭커 엔드포인트 후보. ``body`` 는 그 규격의 요청 모양이다.
#:
#: **사내 게이트웨이는 "cohere 규격 (v1 밖)"이다**(사용자 확인). 임베딩이
#: ``{root}/v1/embeddings`` 인데 리랭커는 ``{root}/rerank`` 로 **``/v1`` 바깥**에 있다 —
#: 그래서 :func:`probe_rerank` 가 ``base`` 에서 ``/v1`` 을 떼어 ``root`` 를 만든다.
#: 나머지 셋은 다른 환경을 위해 남겨 둔다(맞는 것 하나를 찾는 것이 이 단계의 목적이다).
RERANK_SHAPES = [
    ("cohere 규격  /rerank",
     "{base}/rerank",
     lambda m, q, d: {"model": m, "query": q, "documents": d}),
    ("cohere 규격  (v1 밖) ← 사내 게이트웨이가 이 모양",
     "{root}/rerank",
     lambda m, q, d: {"model": m, "query": q, "documents": d}),
    ("TEI 규격     /rerank",
     "{base}/rerank",
     lambda m, q, d: {"query": q, "texts": d}),
    ("TEI 규격     (v1 밖)",
     "{root}/rerank",
     lambda m, q, d: {"query": q, "texts": d}),
]


def _endpoint(cfg: AppConfig):
    """임베딩이 실제로 쓸 접속 정보. ``llm.embed_internal`` 이 있으면 그쪽이다.

    ⚠️ 여기서 ``internal`` 을 쓰면 **chat 주소로 임베딩을 시험**하게 되어 결과가 통째로
    거짓이 된다. 프로덕션(:func:`~contentcompare.llm.factory._make_embed`)이 고르는 것과
    같은 값을 골라야 프로브가 의미를 갖는다.
    """
    return cfg.llm.embed_internal


def _headers(cfg: AppConfig) -> dict[str, str]:
    """``InternalBackend._headers`` 와 같은 규칙 — 직접 지정 키 우선, 없으면 환경변수."""
    headers = {"Content-Type": "application/json"}
    ep = _endpoint(cfg)
    api_key = ep.api_key or os.environ.get(ep.api_key_env, "")
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
    ep = _endpoint(cfg)
    ctx = no_proxy() if ep.unset_proxy else _null_ctx()
    try:
        with ctx:
            resp = requests.post(url, json=payload, headers=_headers(cfg),
                                 timeout=cfg.llm.timeout, verify=ep.verify_ssl)
    except Exception as exc:  # noqa: BLE001 — 예외 자체가 결과다
        return None, None, f"{type(exc).__name__}: {exc}"
    try:
        return resp.status_code, resp.json(), ""
    except Exception:  # noqa: BLE001 — JSON 이 아닌 에러 페이지
        return resp.status_code, None, _clip(resp.text)


def _clip(text: str, limit: int = 400) -> str:
    text = (text or "").strip().replace("\n", " ")
    return text if len(text) <= limit else text[:limit] + f"… (총 {len(text)}자)"


def _norm(v: list) -> float:
    """벡터 길이. 1.0 이면 서버가 정규화해서 준 것이다(bge-m3 는 보통 정규화됨)."""
    return sum(x * x for x in v) ** 0.5


def _cos(a: list, b: list) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    return dot / (na * nb) if na and nb else 0.0


def _compare(label: str, got: list, ref: Optional[list], model: str) -> None:
    """A 와 얼마나 다른지 **수치로** 보여 준다. 참/거짓 하나로는 원인을 못 가른다.

    실측에서 "다름 — 조사 필요"만 나와 원인 후보가 둘로 갈렸다: ①모델명이 달라 서버가
    다른 모델을 태웠다(심각) ②같은 모델인데 서버가 요청마다 미세하게 다른 값을 준다(무해).
    **최대 오차 자릿수가 그 둘을 가른다** — 1e-4 이하면 부동소수점 잡음이고,
    0.01 을 넘으면 다른 모델이다. 그래서 코사인까지 함께 낸다(1.0 이면 방향이 같다).
    """
    _say(f'    model = "{model}"')
    if not ref:
        return
    if len(got) != len(ref) or len(got[0]) != len(ref[0]):
        _say(f"    ✗ 모양부터 다르다 — A={len(ref)}x{len(ref[0])} vs {label}={len(got)}x{len(got[0])}")
        return
    worst = max(abs(a - b) for x, y in zip(got, ref) for a, b in zip(x, y))
    cos = min(_cos(x, y) for x, y in zip(got, ref))
    _say(f"    A 대비 최대 오차 = {worst:.2e}   최소 코사인 = {cos:.6f}")
    # **문장별로** 낸다. 한 문장만 어긋나면 원인이 그 문장의 성질(길이·언어)에 있고,
    # 셋이 고르게 어긋나면 요청 자체(모델·전처리)가 다른 것이다 — 원인 범위가 갈린다.
    for i, (x, y) in enumerate(zip(got, ref)):
        _say(f"      [{i}] 코사인 {_cos(x, y):.6f}   |A|={_norm(y):.4f} |{label}|={_norm(x):.4f}"
             f"   \"{TEXTS[i][:24]}…\"")
    if worst < 1e-4:
        _say("    ✓ 사실상 같다(부동소수점 잡음). 정상.")
    elif cos > 0.999:
        _say("    ~ 방향은 같고 값만 미세하게 다르다 — 서버 비결정성으로 보인다. 사용 가능.")
    else:
        _say("    🚨 **다른 벡터다.** 같은 문장에 다른 답이 왔다.")
        _say("       ⚠️ 단, 위 [A] 의 '같은 요청 두 번' 결과를 먼저 볼 것 — 그 값이")
        _say("          여기와 비슷하면 서버가 원래 매번 다른 값을 주는 것이고,")
        _say("          우리 코드 문제가 아니다.")


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
    _say(f"  ✓ 통과 — 벡터 {len(vecs)}개, 차원 {len(vecs[0])}, "
         f"길이(norm) {_norm(vecs[0]):.4f}")

    # ⚠️ **이 대조군이 이 프로브에서 가장 중요한 측정이다.**
    # 완전히 같은 요청을 두 번 보내 서버 자체의 재현성을 잰다. 이 기준선이 없으면
    # 뒤의 "A 와 다르다"가 *우리 코드 탓*인지 *서버가 원래 그런 것*인지 영영 못 가른다.
    # 실측에서 B 가 코사인 0.935 로 나왔을 때 바로 이 값이 없어 원인을 좁힐 수 없었다.
    _say("\n  [대조군] 완전히 같은 요청을 한 번 더 — 서버가 재현 가능한가?")
    status2, data2, body2 = _post(cfg, url, {"model": model, "input": TEXTS})
    again = _vectors_from(data2) if status2 and status2 < 400 else None
    if not again:
        _say(f"    - 두 번째 요청 실패({status2}) — 대조군 없이 진행: {_clip(body2)}")
        return vecs
    self_cos = min(_cos(x, y) for x, y in zip(again, vecs))
    self_err = max(abs(a - b) for x, y in zip(again, vecs) for a, b in zip(x, y))
    _say(f"    최대 오차 = {self_err:.2e}   최소 코사인 = {self_cos:.6f}")
    if self_cos > 0.9999:
        _say("    ✓ 서버는 재현 가능하다 → 아래에서 다르게 나오면 **우리 코드 탓**이다.")
    else:
        _say("    ⚠️ **서버가 같은 요청에 다른 답을 준다.** 아래 B·C 의 '다름'은")
        _say("       우리 코드 문제가 아닐 수 있다 — 이 값과 비슷하면 같은 현상이다.")
        _say("       (동적 배칭·비결정 커널 등 서버 쪽 사정. 검색 품질에는 영향이 작다.)")

    _say(f"\n  의미 검증(값이 진짜인지):")
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
    _compare("B", vecs, ref, cfg.llm.embed_model)


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

    ep = _endpoint(cfg)
    key = ep.api_key or os.environ.get(ep.api_key_env, "") or "sk-none"
    common = dict(model=cfg.llm.embed_model, base_url=ep.base_url,
                  api_key=key, timeout=cfg.llm.timeout)

    # ⚠️ **프로덕션과 같은 httpx 클라이언트를 넘겨야 한다.**
    # LangChainBackend._ensure_emb() 는 verify=verify_ssl 로 만든 클라이언트를 주는데,
    # 프로브가 그것을 빠뜨렸더니 사내 사설 인증서 환경에서 C 만 전송 단계에서 죽었다.
    # requests(A·B)는 verify=False 로 통과하는데 httpx 기본값은 검증을 하기 때문이다 —
    # 그 결과가 "langchain 경로가 안 된다"로 읽혀 **없는 문제를 만들어 냈다.**
    http_client = None
    try:
        import httpx  # noqa: WPS433

        http_client = httpx.Client(verify=ep.verify_ssl)
        _say(f"  httpx 클라이언트: verify={ep.verify_ssl} (프로덕션과 동일)")
    except Exception as exc:  # noqa: BLE001 - 없으면 SDK 기본값으로 간다
        _say(f"  ⚠️ httpx 클라이언트 생성 실패 - SDK 기본값으로 진행: {exc}")

    for label, extra in (
        ("기본값 (check_embedding_ctx_length=True — 토큰 ID 를 보낸다)", {}),
        ("수정안 (check_embedding_ctx_length=False — 문자열을 보낸다)",
         {"check_embedding_ctx_length": False}),
    ):
        _say(f"\n  · {label}")
        kwargs = dict(common, **extra)
        if http_client is not None:
            kwargs["http_client"] = http_client
        try:
            ctx = no_proxy() if ep.unset_proxy else _null_ctx()
            with ctx:
                vecs = OpenAIEmbeddings(**kwargs).embed_documents(list(TEXTS))
        except Exception as exc:  # noqa: BLE001
            _say(f"    ✗ 실패 — {type(exc).__name__}: {_clip(str(exc))}")
            continue
        _say(f"    응답 : 벡터 {len(vecs)}개, 차원 {len(vecs[0]) if vecs else 0}")
        _compare("C", vecs, ref, cfg.llm.embed_model)
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
    p.add_argument("--rerank-model", default="/models/Reranker/bge-reranker-v2-m3",
                   help="리랭커 모델명(경로는 대소문자를 구분한다 — Reranker)")
    p.add_argument("--rerank-url", default="", help="리랭커 URL 직접 지정(탐색 대신)")
    p.add_argument("--skip", default="", help="건너뛸 단계: a,b,c,d 중 콤마로")
    args = p.parse_args(argv)

    cfg = AppConfig.load(args.config)
    # ⚠️ ``internal``(chat 주소)이 아니라 ``embed_internal`` 이다 — :func:`_endpoint` 의
    # 경고가 여기에도 걸린다. 헤더·verify 는 이미 그쪽을 쓰는데 URL 만 chat 주소였고,
    # 두 주소가 같은 환경에서는 증상이 없었다. 사내처럼 임베딩·chat·리랭커가 서로 다른
    # 경로에 있으면 A 단계가 **엉뚱한 호스트를 시험**해 결과가 통째로 거짓이 된다.
    ep = _endpoint(cfg)
    base = ep.base_url.rstrip("/")
    embed_model = args.embed_model or cfg.llm.embed_model
    skip = {s.strip().lower() for s in args.skip.split(",") if s.strip()}

    _say(f"설정     : {args.config}")
    _say(f"base_url : {base}  (llm.embed_internal)")
    _say(f"임베딩모델: {embed_model}")
    _say(f"인증     : {'Authorization 헤더 있음' if 'Authorization' in _headers(cfg) else '⚠️ 키 없음'}")
    _say(f"verify_ssl={ep.verify_ssl}  unset_proxy={ep.unset_proxy}")

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
