@echo off
REM ============================================================
REM  One-click build. Every time you change the code, just run
REM  this file (double click, or: build.bat [dir|onefile|both]).
REM  Real logic lives in build.py so the Chinese output and error
REM  handling stay readable.
REM ============================================================
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] .venv not found. Create the virtualenv and install requirements.txt first.
    pause
    exit /b 1
)

".venv\Scripts\python.exe" build.py %*
set CODE=%ERRORLEVEL%

if not "%CODE%"=="0" (
    echo.
    echo [ERROR] build failed ^(exit %CODE%^). See build_log.txt for details.
)

pause
exit /b %CODE%
