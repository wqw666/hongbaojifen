@echo off
chcp 65001 >nul
setlocal EnableDelayedExpansion
cd /d "%~dp0"

REM ---- 定位 Python（显式路径 -> py 启动器 -> PATH 上的 python）----
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
  echo 未找到 Python 3.10+，请先安装 Python
  goto :fail
)
echo 使用 Python: %PY% %PYARG%

echo ========================================
echo   agent 执行器 — 交付包打包
echo ========================================
echo.

echo [1/5] NapCat 插件...
if exist plugin\dist\index.mjs (
  echo     已有 plugin\dist\index.mjs，跳过插件构建（无需 Node）
) else (
  echo     需要 Node.js 18+，开始 npm install + build
  call npm install
  if errorlevel 1 goto :fail
  call npm run build
  if errorlevel 1 goto :fail
)

echo.
echo [2/5] 准备内嵌插件资源...
if not exist plugin_bundle mkdir plugin_bundle
copy /Y plugin\dist\index.mjs plugin_bundle\ >nul
copy /Y plugin\package.json plugin_bundle\ >nul

echo.
echo [3/5] 安装 Python 依赖...
"%PY%" %PYARG% -m pip install --user -r requirements.txt -q
if errorlevel 1 goto :fail

echo.
echo [4/5] PyInstaller 打包 exe...
"%PY%" %PYARG% -m PyInstaller --noconfirm QQHongbaoMonitor.spec
if errorlevel 1 goto :fail

echo.
echo [5/5] 组装 release（exe + tools + 源码）...
"%PY%" %PYARG% "%~dp0scripts\assemble_release.py" "%CD%"
if errorlevel 1 goto :fail

echo.
echo ===== 完成 =====
echo 交付目录: %CD%\release\
echo 压缩包:   %CD%\agent_交付包.zip
echo 部署:     解压 zip 到目标机器后双击「一键启动.bat」
goto :end

:fail
echo.
echo 打包失败
exit /b 1

:end
endlocal
pause
