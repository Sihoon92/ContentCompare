@echo off
rem ============================================================
rem  ContentCompare 웹 서버 실행 (Windows)
rem    start.bat          서버 실행(빌드된 화면 web\dist) + 브라우저 열기
rem    start.bat dev      개발 모드: API(uvicorn 자동 재시작) + 화면(Vite) 을 각각 새 창으로
rem    start.bat check    실행 준비만 점검하고 끝낸다
rem  파이썬: .venv 가 있으면 그것을, 없으면 PATH 의 python 을 쓴다.
rem          다른 인터프리터를 쓰려면 CC_PYTHON 에 경로를 넣는다(예: set CC_PYTHON=python).
rem  끄기: 이 창에서 Ctrl+C 를 한 번 누른다(실행 중인 작업은 interrupted 로 남는다).
rem  주의: Office 자동화는 로그인된 데스크톱 세션이 필요하다. Windows 서비스로 등록하지 말 것.
rem ============================================================
rem  (이 파일은 CP949/ANSI + CRLF 로 저장되어야 한다)
setlocal
cd /d "%~dp0"
set "MODE=%~1"

rem --- 1) 파이썬 고르기 ------------------------------------------
set "PY="
if defined CC_PYTHON set "PY=%CC_PYTHON%"
if not defined PY if exist ".venv\Scripts\python.exe" set "PY=.venv\Scripts\python.exe"
if not defined PY (
    where python >nul 2>nul && set "PY=python"
)
if not defined PY (
    echo [ERROR] Python 을 찾을 수 없습니다. setup.bat 을 먼저 실행하세요.
    goto :fail
)

rem --- 2) 웹 서버 의존성 --------------------------------------------
"%PY%" -c "import fastapi, uvicorn, python_multipart, dotenv" >nul 2>nul
if errorlevel 1 (
    echo [ERROR] 웹 서버 의존성이 없습니다. python: %PY%
    echo         setup.bat 을 실행하거나: "%PY%" -m pip install -e ".[web]"
    goto :fail
)

rem --- 3) .env ------------------------------------------------------
if not exist ".env" (
    echo [ERROR] .env 가 없습니다. 아래로 만든 뒤 LLM 접속 정보와 관리자 비밀번호를 채우세요.
    echo         copy .env.example .env
    goto :fail
)

set "PORT=8000"
for /f "tokens=1,* delims==" %%a in ('findstr /b /c:"CC_PORT=" .env 2^>nul') do set "PORT=%%b"

if /i "%MODE%"=="dev" goto :dev

rem --- 4) 빌드된 화면 --------------------------------------------------
if not exist "web\dist\index.html" (
    echo [ERROR] 화면이 빌드되지 않았습니다^(web\dist^).
    echo         setup.bat 을 실행하거나, Node.js 가 있는 PC 에서 빌드한 web\dist 폴더를 복사하세요.
    goto :fail
)

if /i "%MODE%"=="check" (
    echo [OK] 실행 준비가 끝났습니다. python: %PY% / 포트: %PORT%
    goto :ok
)

echo ContentCompare 웹 서버를 시작합니다. 끄려면 이 창에서 Ctrl+C 를 한 번 누르세요.
if not defined CC_NO_BROWSER (
    start "" /min powershell -NoProfile -Command "Start-Sleep -Seconds 3; Start-Process 'http://localhost:%PORT%'"
)
"%PY%" -m contentcompare.web
goto :ok

:dev
where npm >nul 2>nul
if errorlevel 1 (
    echo [ERROR] npm 이 없습니다. 개발 모드에는 Node.js 가 필요합니다.
    goto :fail
)
if not exist "web\node_modules" (
    pushd web
    call npm ci
    popd
)
echo 개발 모드: API http://localhost:8000 / 화면 http://localhost:5173
start "ContentCompare API (dev)" "%PY%" -m uvicorn contentcompare.web.__main__:dev_app --factory --reload --reload-dir contentcompare --port 8000 --timeout-graceful-shutdown 5
start "ContentCompare UI (dev)" cmd /k "cd /d web && npm run dev"
if not defined CC_NO_BROWSER (
    start "" /min powershell -NoProfile -Command "Start-Sleep -Seconds 5; Start-Process 'http://localhost:5173'"
)
goto :ok

:ok
endlocal
exit /b 0

:fail
echo.
echo 실행을 중단했습니다.
if not defined CC_NO_PAUSE pause
endlocal
exit /b 1
