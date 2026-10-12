"""작업 하나를 실행하는 별도 프로세스 — ``python -m contentcompare.web.worker <작업폴더>``.

- **파이프라인은 웹을 모른다**(설계 §4.1). 여기서도 CLI 처럼 ``make_pipeline().run()`` 만 부른다.
- **프로세스가 작업 단위다.** 타임라인·로그 파일·``disable_proxy()``·Office COM 이 모두
  프로세스 전역이라, 작업마다 새 프로세스를 띄워 서로를 가둔다.
- 화면용 로그: ``log_print``(stdout)와 콘솔 핸들러(stderr, INFO)를 서버가 ``console.log`` 로
  받는다. 프롬프트·원문은 DEBUG 라 작업 폴더의 ``contentcompare_*.log`` 에만 남는다.
- **API 키를 작업 폴더에 남기지 않는다.** 서버가 설정을 파일로 넘기지 않고 worker 가
  `.env` 를 스스로 읽는다.
- 성패는 종료코드로 알린다(0 성공, 1 실패). 실패 사유는 ``error.json`` 한 줄.
"""

from __future__ import annotations

import importlib
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, Callable, Optional

from .. import progress as prog
from ..fact.engine import make_pipeline
from ..llm.tracing import get_tracer, run_metadata, trace_run
from ..logging_setup import apply_logger_overrides, log_print, setup_console, setup_logging
from ..timeline import start_timeline
from . import results
from .jobs import Job
from .settings import WebSettings, build_app_config, load_settings

FACTORY_ENV = "CC_PIPELINE_FACTORY"
DOTENV_ENV = "CC_DOTENV"
"""서버가 쓰는 `.env` 경로. 서버가 설정하고 worker 가 물려받는다(서버 ``--env`` 와 일치시키려고)."""

logger = logging.getLogger(__name__)


def run_job(job_dir: Path, settings: WebSettings, *,
            factory: Optional[Callable[[Any, str], Any]] = None) -> int:
    job_dir = Path(job_dir)
    job = Job.from_dict(json.loads((job_dir / "job.json").read_text(encoding="utf-8")))

    try:
        config = build_app_config(settings)
        # 산출물·추적·리포트를 작업 폴더로 돌린다 — 같은 이름 문서를 두 사람이 올려도 안 섞인다.
        config.fact.artifacts_dir = str(job_dir / "artifacts")
        config.llm.trace_dir = str(job_dir / "artifacts" / "_traces")
        config.logging.timeline_dir = ""  # 비우면 <artifacts>/_timeline — config.yaml 값보다 우선
        config.report.output_dir = str(job_dir)

        setup_console(level=logging.INFO)
        setup_logging(log_dir=str(job_dir), force_new=True)
        apply_logger_overrides(config.logging.quiet_extra, config.logging.verbose_extra)
        start_timeline(config, console=False, label="timeline")
        prog.set_reporter(prog.JsonlProgress(job_dir / "progress.jsonl"))

        reference = str(job_dir / "inputs" / job.reference)
        targets = [str(job_dir / "inputs" / t) for t in job.targets]
        log_print(f"작업 {job.id} 시작 · 엔진 {job.engine} · 대상 {len(targets)}개 · "
                  f"모델 {config.llm.chat_model}")
        pipeline = (factory or make_pipeline)(config, job.engine)
        tracer = get_tracer(config)
        with trace_run(tracer, f"contentcompare {job.engine} (web)",
                       run_metadata(config, job.engine, reference, targets)):
            outcome = pipeline.run(reference, targets)
        if job.engine == "fact":
            payload = results.fact_payload(outcome)
            markdown = outcome.markdown
        else:
            payload = results.rag_payload(outcome)
            markdown = results.rag_markdown(outcome, reference, targets)
        _write_json(job_dir / "result.json", payload)
        if not markdown:
            message = "비교할 fact 가 부족해 리포트를 만들지 못했습니다(실패한 문서를 확인하세요)."
            _write_json(job_dir / "error.json", {"error": message})
            log_print(message)
            return 1
        (job_dir / "report.md").write_text(markdown, encoding="utf-8")
        log_print(f"완료: 판정 {len(payload.get('summary', []))}건")
        return 0
    except Exception as exc:  # noqa: BLE001 — 사유를 남기고 종료코드로 알린다
        logger.exception("작업 실패")
        _write_json(job_dir / "error.json", {"error": f"{type(exc).__name__}: {exc}"})
        log_print(f"실패: {type(exc).__name__}: {exc}", level=logging.ERROR)
        return 1
    finally:
        prog.reset_reporter()


def _write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _factory_from_env() -> Optional[Callable[[Any, str], Any]]:
    spec = os.environ.get(FACTORY_ENV, "").strip()
    if not spec:
        return None
    module, _, attr = spec.partition(":")
    return getattr(importlib.import_module(module), attr)


def main(argv: Optional[list[str]] = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1:
        print("사용법: python -m contentcompare.web.worker <작업폴더>", file=sys.stderr)
        return 2
    # 서버가 --env 로 다른 파일을 쓰면 그 경로를 CC_DOTENV 로 물려준다(__main__ 참고).
    settings = load_settings(os.environ.get(DOTENV_ENV, ".env"))
    return run_job(Path(args[0]), settings, factory=_factory_from_env())


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
