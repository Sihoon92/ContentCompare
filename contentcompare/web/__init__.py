"""웹 서버(FastAPI) — 여러 명이 브라우저로 쓰는 비교 서비스.

⚠️ 여기서 FastAPI 를 import 하지 말 것. worker 프로세스(:mod:`.worker`)가 이 패키지를 쓰는데,
worker 는 웹 의존성 없이도 떠야 한다(설계 §4).
"""
