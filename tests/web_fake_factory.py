"""worker 통합 테스트용 가짜 파이프라인. ``CC_PIPELINE_FACTORY=web_fake_factory:make`` 로 주입한다."""

from __future__ import annotations

import time

from contentcompare import progress as prog
from contentcompare.fact.pipeline import FactRunResult
from contentcompare.logging_setup import log_print


class _Fake:
    def __init__(self, delay: float = 0.0) -> None:
        self.delay = delay

    def run(self, reference, targets):
        log_print("가짜 파이프라인 실행 중")
        prog.plan([prog.Unit("doc:0", "기준.xlsx", prog.DOC)])
        prog.unit_start("doc:0")
        time.sleep(self.delay)
        prog.unit_done("doc:0")
        return FactRunResult(
            summaries=[{"path": reference, "status": "ok"}],
            comparisons=[{"result": "match", "entity_name": "충전온도",
                          "target_doc": "a.docx", "reason": "일치"}],
            markdown="# 가짜 리포트")


def make(config, engine):
    return _Fake()


def make_slow(config, engine):
    return _Fake(delay=120.0)
