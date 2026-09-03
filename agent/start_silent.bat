@echo off
chcp 65001 >nul
cd /d "%~dp0"

REM 同步最新插件到内置 NapCat（源码在 plugin\ 目录；release 包内为 agent\plugin\）
if exist "%~dp0plugin\dist\index.mjs" (
  copy /Y "%~dp0plugin\dist\index.mjs" "%~dp0tools\NapCat\plugins\napcat-plugin-cleaner\index.mjs" >nul 2>&1
) else if exist "%~dp0agent\plugin\dist\index.mjs" (
  copy /Y "%~dp0agent\plugin\dist\index.mjs" "%~dp0tools\NapCat\plugins\napcat-plugin-cleaner\index.mjs" >nul 2>&1
)

taskkill /F /IM NapCatWinBootMain.exe /T >nul 2>&1
timeout /t 3 /nobreak >nul

wscript //nologo "%~dp0tools\restart_napcat_hidden.vbs"

set OK=0
for /L %%i in (1,1,30) do (
  powershell -NoProfile -WindowStyle Hidden -Command "try { $r=Invoke-WebRequest 'http://127.0.0.1:6099/plugin/napcat-plugin-cleaner/api/status' -UseBasicParsing -TimeoutSec 2; if($r.StatusCode -eq 200){ exit 0 } else { exit 1 } } catch { exit 1 }" >nul 2>&1
  if not errorlevel 1 (
    set OK=1
    goto :gui
  )
  timeout /t 3 /nobreak >nul
)

:gui
if exist "%~dp0agent.exe" (
  start "" "%~dp0agent.exe"
  exit /b 0
)
exit /b 1
