@echo off
chcp 65001 >nul
setlocal

:: ==========================================
:: 红包积分管理系统 构建脚本 (Windows)
::   build.bat          → 读取 VERSION 构建（含前端自动打包）
::   build.bat 1.1.0    → 指定版本并更新 VERSION
::   build.bat 1.1.0 skip → 跳过前端构建（仅后端）
:: ==========================================

if not "%~1"=="" (
    echo %~1>VERSION
    set V=%~1
) else (
    set /p V=<VERSION
)
echo [版本] %V%

if exist "D:\Java\jdk21" (
    set JAVA_HOME=D:\Java\jdk21
    set PATH=%JAVA_HOME%\bin;%PATH%
)

:: 是否跳过前端
if "%~2"=="skip" (
    echo [前端] 跳过前端构建
    call mvn clean package -Drevision=%V% -DskipTests -Dskip.fe=true -q
) else (
    echo [前端] 自动构建 React 前端并内嵌到 JAR
    call mvn clean package -Drevision=%V% -DskipTests -q
)

set JAR=target\hongbaojifen-api-%V%.jar
if exist "%JAR%" (
    for %%f in (%JAR%) do echo [完成] %%f  %%~zf bytes
    echo.
    echo 服务器部署流程（start.sh/stop.sh/deploy.sh 已常驻 /opt/hongbaojifen/）:
    echo   scp %JAR% ubuntu@^<服务器IP^>:/tmp/
    echo   ssh ubuntu@^<服务器IP^> "/opt/hongbaojifen/deploy.sh"
    echo.
    echo 首次部署: scp start.sh stop.sh deploy.sh VERSION ubuntu@^<服务器IP^>:/opt/hongbaojifen/
) else (
    echo [失败] 构建失败
    exit /b 1
)
