@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

set "TARGET=%~dp0NapCat"

if exist "%TARGET%\napcat.mjs" (
  echo NapCat 已安装: %TARGET%
  goto :end
)

set "SRC=%~1"
if "%SRC%"=="" (
  echo 用法: setup_napcat.bat "NapCat解压目录"
  echo.
  echo 1. 从 NapCat 官方发布页下载 Win 完整包:
  echo    https://github.com/NapNeko/NapCatQQ/releases
  echo 2. 解压到任意目录
  echo 3. 运行: setup_napcat.bat "解压目录"
  echo.
  echo 说明: NapCat 含登录态与密钥，不入 git，每台机器独立安装独立登录。
  goto :end
)

if not exist "%SRC%\napcat.mjs" (
  echo 错误: 源目录内未找到 napcat.mjs: "%SRC%"
  goto :end
)

echo 复制 NapCat: "%SRC%" -^> "%TARGET%"
robocopy "%SRC%" "%TARGET%" /E /NFL /NDL /NJH /NJS /NP /R:1 /W:1
if errorlevel 8 (
  echo 复制失败，请手动复制
  goto :end
)
echo 完成。运行「一键启动.bat」后扫码登录。
:end
endlocal
pause
