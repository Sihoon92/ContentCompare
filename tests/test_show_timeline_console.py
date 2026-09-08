"""``scripts/show_timeline.py`` 가 cp949 콘솔에서 **줄을 잃지 않는지**.

실측(Windows PowerShell 5.1, cp949): 이 스크립트가 찍는 거의 모든 줄이 인코딩 불가
문자를 담고 있었고, ``print`` 는 그 자리에서 ``UnicodeEncodeError`` 로 **죽는다**.

    format_line(ok)      ✓  U+2713
    format_line(length)  ✗  U+2717
    _HINTS 6/7건          —  U+2014

:func:`~contentcompare.timeline.console_safe` 는 이미 있었지만 이 스크립트만 안 거쳤다.
실시간 콘솔은 거치고 있어서 아무도 못 봤다 — "화면 실패는 기록 실패가 아니다"라는 원칙이
정작 **진단을 읽는 경로**에서 빠져 있었던 셈이다.
"""

from __future__ import annotations

import io
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import show_timeline  # noqa: E402


class _Cp949Out(io.TextIOBase):
    """cp949 콘솔 흉내 — 못 쓰는 문자가 오면 실제 콘솔처럼 던진다."""

    encoding = "cp949"

    def __init__(self) -> None:
        self.lines: list[str] = []

    def write(self, text: str) -> int:
        text.encode(self.encoding)  # 여기서 UnicodeEncodeError
        self.lines.append(text)
        return len(text)


@pytest.mark.parametrize("text", ["✓ 끝", "✗ 잘림", "무엇을 볼 것 — 배치를 줄이세요"])
def test_output_survives_a_cp949_console(text, monkeypatch):
    """화면이 초라해질지언정 **줄을 잃지는 않는다**(console_safe 의 계약)."""
    out = _Cp949Out()
    monkeypatch.setattr(sys, "stdout", out)

    show_timeline.safe_print(text)

    assert out.lines and out.lines[0].strip()


def test_utf8_console_keeps_the_original_symbols(monkeypatch):
    """UTF-8 콘솔에서는 한 글자도 바뀌지 않는다 — 치환은 **필요할 때만**."""

    class _Utf8Out(_Cp949Out):
        encoding = "utf-8"

    out = _Utf8Out()
    monkeypatch.setattr(sys, "stdout", out)

    show_timeline.safe_print("✓ 끝 — 좋다")

    assert out.lines[0] == "✓ 끝 — 좋다"


def test_every_hint_is_printable_on_cp949():
    """진단 문구는 **읽히라고** 있는 것이다 — 하나라도 죽으면 조치를 못 본다."""
    from contentcompare.timeline import _HINTS, console_safe

    for _, text in _HINTS:
        console_safe(text, "cp949").encode("cp949")
