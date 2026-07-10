@echo off
REM Build SilverResonance panel into a single exe.
REM Just double-click this file. Dependencies install automatically on first run.

cd /d "%~dp0"
echo ============================================
echo   SilverResonance - Build Tool
echo ============================================

REM ---- Auto-locate Python (works even if not on PATH) ----
set "PY="
py -3 --version >nul 2>&1 && set "PY=py -3"
if not defined PY (
  python --version >nul 2>&1 && set "PY=python"
)
if not defined PY if exist "%LOCALAPPDATA%\Programs\Python\Python313\python.exe" set "PY=%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
if not defined PY (
  for /d %%D in ("%LOCALAPPDATA%\Programs\Python\Python3*") do if exist "%%D\python.exe" set "PY=%%D\python.exe"
)
if not defined PY (
  echo [ERROR] Python not found. Install Python 3 or add it to PATH, then retry.
  echo.
  pause
  exit /b 1
)
echo Using Python: %PY%

echo [1/3] Checking dependencies: PyInstaller + tqsdk/flask/pandas ...
%PY% -m pip show pyinstaller >nul 2>&1
if errorlevel 1 (
  echo   Installing pyinstaller...
  %PY% -m pip install pyinstaller
)
%PY% -m pip install -r requirements.txt

echo [2/3] Building (first run is slow, please wait) ...
%PY% -m PyInstaller build.spec --noconfirm --clean

echo [3/3] Done
echo.

REM Remove account config from dist so your account is not shipped to others.
if exist "dist\user_config.json" del /q "dist\user_config.json"

echo exe location: %~dp0dist\SilverResonance.exe
echo When sharing, copy ONLY this exe, do NOT include user_config.json
echo The recipient fills in their own TQ account on first run (same account cannot log in on multiple PCs at once)
echo.
pause