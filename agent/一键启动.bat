@echo off
chcp 65001 >nul
cd /d "%~dp0"

if exist "%~dp0agent.exe" (
  start "" "%~dp0agent.exe"
  exit /b 0
)

if exist "%~dp0start_silent.bat" (
  call "%~dp0start_silent.bat"
  exit /b 0
)

echo 未找到 agent.exe，请确认已完整解压。
pause
exit /b 1
