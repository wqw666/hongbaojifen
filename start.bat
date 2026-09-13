@echo off
setlocal EnableDelayedExpansion

:: ==========================================
:: hongbaojifen backend start script (Windows)
::   Usage: start.bat [local or prod]      default: local
::   Requires: hongbaojifen-api-*.jar in this folder (build.bat output)
::   Log: logs\app.log
::   Port: 8892 - if occupied, the owner process (old instance / stray java)
::         is killed first, then start. Run again to restart.
::   Note: ASCII only - do NOT add chcp / non-ASCII text (cmd parse bug)
:: ==========================================
cd /d "%~dp0"

set "PORT=8892"
set "PROFILE=%~1"
if "%PROFILE%"=="" set "PROFILE=local"

echo ========================================
echo   hongbaojifen - start
echo ========================================
echo.

:: JDK: prefer D:\Java\jdk21 (same as build.bat), else PATH
if exist "D:\Java\jdk21" (
    set "JAVA_HOME=D:\Java\jdk21"
    set "PATH=!JAVA_HOME!\bin;!PATH!"
)
where java >nul 2>&1
if errorlevel 1 (
    echo [ERROR] java not found. Install JDK 21 or add it to PATH.
    goto :fail
)

:: pick newest jar: same folder first (server style), then target\ (local dev)
set "JAR="
for /f "delims=" %%f in ('dir /b /o-d "hongbaojifen-api-*.jar" 2^>nul') do (
    if not defined JAR set "JAR=%%f"
)
if not defined JAR (
    for /f "delims=" %%f in ('dir /b /o-d "target\hongbaojifen-api-*.jar" 2^>nul') do (
        if not defined JAR set "JAR=target\%%f"
    )
)
if not defined JAR (
    echo [ERROR] hongbaojifen-api-*.jar not found. Run build.bat first.
    goto :fail
)

:: cleanup: kill whatever listens on %PORT% (old instance / stray java)
set "PIDS="
for /f "tokens=5" %%p in ('netstat -ano ^| findstr /r /c:":%PORT% .*LISTENING"') do (
    echo !PIDS! | findstr /c:" %%p " >nul 2>&1 || set "PIDS=!PIDS! %%p"
)
if defined PIDS (
    echo [clean] port %PORT% occupied by PIDs:!PIDS! - killing
    for %%p in (!PIDS!) do taskkill /F /T /PID %%p >nul 2>&1
    set "FREE="
    for /l %%i in (1,1,10) do (
        if not defined FREE (
            netstat -ano | findstr /r /c:":%PORT% .*LISTENING" >nul 2>&1
            if !errorlevel! equ 0 (
                ping -n 2 127.0.0.1 >nul
            ) else (
                set "FREE=1"
            )
        )
    )
    if not defined FREE echo [WARN] port %PORT% still occupied
) else (
    echo [check] port %PORT% free
)

:: start minimized, log to logs\app.log
if not exist logs mkdir logs
echo [start] %JAR%  profile=%PROFILE%  log=logs\app.log
start "hongbaojifen-api" /min cmd /c "java -Xms256m -Xmx512m -jar %JAR% --spring.profiles.active=%PROFILE% > logs\app.log 2>&1"

:: health check up to 60s (needs system curl)
where curl >nul 2>&1
if errorlevel 1 (
    echo [info] curl not found - skip health check. Verify: http://localhost:%PORT%/health
    goto :done
)
set "OK="
for /l %%i in (1,1,30) do (
    if not defined OK (
        curl -s -o nul "http://localhost:%PORT%/health" >nul 2>&1
        if !errorlevel! equ 0 (
            set "OK=1"
        ) else (
            ping -n 3 127.0.0.1 >nul
        )
    )
)
if defined OK (
    echo [ready] http://localhost:%PORT%/health ok
) else (
    echo [WARN] not ready in 60s - see logs\app.log
    goto :fail
)

:done
echo.
echo admin: http://localhost:%PORT%/admin/
echo %cmdcmdline% | findstr /i /c:"%~nx0" >nul && pause
exit /b 0

:fail
echo.
echo %cmdcmdline% | findstr /i /c:"%~nx0" >nul && pause
exit /b 1
