@echo off
chcp 65001 >nul
setlocal EnableDelayedExpansion
cd /d "%~dp0"

REM ============================================================
REM  agent - build exe only (quick test entry)
REM  Output : dist\agent.exe
REM
REM  build.bat    = full release package (plugin + exe + release + zip)
REM  this file   = exe only, skip release/NapCat copy, for fast iterations
REM ============================================================

echo ========================================
echo   agent - pack exe (test)
echo ========================================
echo.

REM ---- locate Python (known paths - py launcher - PATH python) ----
set "PY="
set "PYARG="
for %%P in (
  "%LocalAppData%\Programs\Python\Python313\python.exe"
  "%LocalAppData%\Programs\Python\Python312\python.exe"
  "%LocalAppData%\Programs\Python\Python311\python.exe"
  "C:\Program Files\python\python.exe"
) do (
  if not defined PY if exist %%~P set "PY=%%~P"
)
if not defined PY (
  py -3 --version >nul 2>&1
  if not errorlevel 1 (set "PY=py" & set "PYARG=-3")
)
if not defined PY (
  python --version >nul 2>&1
  if not errorlevel 1 set "PY=python"
)
if not defined PY (
  echo ERROR: Python 3.10+ not found, install it first
  goto :fail
)
echo Using Python: %PY% %PYARG%
echo.

echo [1/2] prepare embedded plugin bundle...
if not exist plugin_bundle mkdir plugin_bundle
if exist plugin\dist\index.mjs (
  copy /Y plugin\dist\index.mjs plugin_bundle\ >nul
  if exist plugin\package.json copy /Y plugin\package.json plugin_bundle\ >nul
  echo     synced from plugin\dist
) else if exist plugin_bundle\index.mjs (
  echo     reuse existing plugin_bundle\index.mjs
) else (
  echo     ERROR: missing plugin\dist\index.mjs AND plugin_bundle\index.mjs
  echo     run full build.bat once first, or cd plugin ^&^& npm install ^&^& npm run build
  goto :fail
)

echo.
echo [2/2] PyInstaller...
"%PY%" %PYARG% -m pip install --user -r requirements.txt -q
if errorlevel 1 goto :fail
"%PY%" %PYARG% -m PyInstaller --noconfirm QQHongbaoMonitor.spec
if errorlevel 1 goto :fail

echo.
echo ===== DONE =====
echo exe: %CD%\dist\agent.exe
echo double-click to run; run build.bat for full release package
goto :end

:fail
echo.
echo PACK FAILED
exit /b 1

:end
endlocal
pause
