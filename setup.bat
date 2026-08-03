@echo off
rem ============================================================
rem  ContentCompare 개발환경 준비 스크립트 (Windows)
rem  - .venv 가상환경 생성(없을 때만) → 활성화 → 패키지 설치
rem  사용법:
rem    setup.bat                 (기본: office,ui,dev 설치)
rem    setup.bat office,ui,dev,fastembed
rem    setup.bat core            (코어 의존성만: pyyaml+requests)
rem ============================================================
rem  (이 파일은 CP949/ANSI 로 저장되어야 한글이 콘솔에 정상 출력됩니다)
setlocal
cd /d "%~dp0"

set "EXTRAS=%~1"
if "%EXTRAS%"=="" set "EXTRAS=office,ui,dev"

rem --- 1) 파이썬 인터프리터 찾기 (py 런처 우선) ---------------
set "PY_CMD="
where py >nul 2>nul && set "PY_CMD=py -3"
if not defined PY_CMD (
    where python >nul 2>nul && set "PY_CMD=python"
)
if not defined PY_CMD (
    echo [ERROR] Python 을 찾을 수 없습니다. Python 3.10 이상을 설치한 뒤 다시 실행하세요.
    goto :fail
)

rem --- 2) 가상환경 생성 ---------------------------------------
if exist ".venv\Scripts\python.exe" (
    echo [1/4] 기존 .venv 를 재사용합니다.
) else (
    echo [1/4] .venv 가상환경 생성 중... ^(%PY_CMD%^)
    %PY_CMD% -m venv .venv
    if errorlevel 1 (
        echo [ERROR] 가상환경 생성 실패.
        goto :fail
    )
)

rem --- 3) 활성화 ----------------------------------------------
echo [2/4] 가상환경 활성화...
call ".venv\Scripts\activate.bat"
if errorlevel 1 (
    echo [ERROR] 가상환경 활성화 실패.
    goto :fail
)
python -c "import sys; print('     python:', sys.version.split()[0], '-', sys.executable)"

rem --- 4) pip 최신화 ------------------------------------------
echo [3/4] pip 업그레이드...
python -m pip install --upgrade pip
if errorlevel 1 (
    echo [ERROR] pip 업그레이드 실패.
    goto :fail
)

rem --- 5) 패키지 설치 -----------------------------------------
if /i "%EXTRAS%"=="core" (
    echo [4/4] 패키지 설치: 코어만 ^(pyyaml, requests^)
    python -m pip install -e .
) else (
    echo [4/4] 패키지 설치: extras = %EXTRAS%
    python -m pip install -e ".[%EXTRAS%]"
)
if errorlevel 1 (
    echo [ERROR] 패키지 설치 실패. ^(office extras 는 Windows + MS Office 환경에서만 설치됩니다^)
    goto :fail
)

echo.
echo ============================================================
echo  완료. 새 터미널에서는 아래로 가상환경을 켜세요.
echo    PowerShell : .\.venv\Scripts\Activate.ps1
echo    cmd        : .venv\Scripts\activate.bat
echo.
echo  다음 단계 ^(설정 파일이 없다면^):
echo    copy config\config.example.yaml config\config.yaml
echo    contentcompare --check --config config\config.yaml
echo ============================================================
if not defined CC_NO_PAUSE pause
endlocal
exit /b 0

:fail
echo.
echo 설치가 중단되었습니다.
if not defined CC_NO_PAUSE pause
endlocal
exit /b 1
