"""samples/spec_en.docx 생성 — 영어 대상 문서 회귀 픽스처.

``samples/자표준_규격서.docx`` 의 영어판이다. **수치와 구조(단락 11 + 표 6행)를 원본과
1:1 로 맞추고 언어·표기 관습만 바꾼다** — 그래야 비교 결과의 차이를 언어 탓으로 돌릴
수 있다. 표기 관습 차이도 의도적이다:

- 온도 단위를 ``℃``(U+2103) 대신 ``°C``(도 기호 + C)로 쓴다 — 영어 문서에서 흔하다.
- 습도를 ``33~53%RH`` 대신 ``33 to 53 %RH`` 로 쓴다.

바이너리 샘플의 출처를 남기기 위한 스크립트다. 실행:

    pip install python-docx
    python scripts/make_en_sample.py
"""

from docx import Document

OUT = "samples/spec_en.docx"

PARAGRAPHS = [
    ("h1", "Battery Cell Specification (Summary)"),
    ("p", "This document summarizes the specification items of the master standard "
          "document in narrative form."),
    ("h2", "1. Electrical Specifications"),
    ("p", "This specification conforms to Battery Approval Specification ver 4.7 "
          "(SEC Req. ver.4.7)."),
    ("p", "The rated charging voltage is 4.55V."),
    ("p", "The discharge cut-off voltage shall be 3.0V."),
    ("p", "The nominal voltage is 3.85V."),
    ("p", "The maximum charging current shall not exceed 1.495A."),
    ("p", "The evaluation ambient humidity shall be within the range of 33 to 53 %RH, "
          "with a center value of 43 %RH."),
    ("h2", "2. Key Specification Table"),
]

TABLE = [
    ("Item", "Specification", "Unit"),
    ("Nominal capacity", "1150", "mAh"),
    ("Rated capacity", "1150", "mAh"),
    ("Standard charging current", "230", "mA"),
    ("Maximum discharging current", "1150", "mA"),
    ("Standard ambient temperature", "21 ~ 29 (center 25)", "°C"),
]

FOOTER = "* Items not specified in this document shall be agreed upon separately."


def build() -> None:
    doc = Document()
    for kind, text in PARAGRAPHS:
        if kind == "h1":
            doc.add_heading(text, level=1)
        elif kind == "h2":
            doc.add_heading(text, level=2)
        else:
            doc.add_paragraph(text)

    table = doc.add_table(rows=len(TABLE), cols=3)
    table.style = "Table Grid"
    for r, values in enumerate(TABLE):
        for c, value in enumerate(values):
            table.cell(r, c).text = value

    doc.add_paragraph(FOOTER)
    doc.save(OUT)
    print(f"saved: {OUT}")


if __name__ == "__main__":
    build()
