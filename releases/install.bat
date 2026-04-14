@echo off
setlocal
echo ============================================
echo  Installing Job Manager CK v2.1
echo ============================================
echo.

set "SCRIPT_DIR=%~dp0"
set "LOCAL_SOURCE=%SCRIPT_DIR%JobManager.exe"
set "FALLBACK_SOURCE=S:\Software\JobManagerCK\releases\JobManager.exe"
set "INSTALL_DIR=C:\Program Files\JobManagerCK"
set "EXE=%INSTALL_DIR%\JobManager.exe"
set "BACKUP=%INSTALL_DIR%\JobManager.exe.bak"
set "DESKTOP=%USERPROFILE%\Desktop"
set "SHORTCUT=%DESKTOP%\Job Manager CK.lnk"

if exist "%LOCAL_SOURCE%" (
    set "SOURCE=%LOCAL_SOURCE%"
    echo Source: local ^(next to installer^)
) else if exist "%FALLBACK_SOURCE%" (
    set "SOURCE=%FALLBACK_SOURCE%"
    echo Source: S drive
) else (
    echo ERROR: Cannot find JobManager.exe
    echo   Looked in: %LOCAL_SOURCE%
    echo   Looked in: %FALLBACK_SOURCE%
    echo.
    echo Put JobManager.exe next to install.bat, or connect the S drive.
    pause
    exit /b 1
)
echo.

echo Stopping any running Job Manager CK instances...
taskkill /IM JobManager.exe /F >nul 2>&1
timeout /t 1 /nobreak >nul

echo Creating install directory...
if not exist "%INSTALL_DIR%" mkdir "%INSTALL_DIR%"

if exist "%EXE%" (
    echo Backing up previous version to JobManager.exe.bak...
    copy /y "%EXE%" "%BACKUP%" >nul
)

echo Copying JobManager.exe...
copy /y "%SOURCE%" "%EXE%"
if errorlevel 1 (
    echo.
    echo ERROR: Copy failed.
    echo - If the file is locked, close Job Manager CK and try again.
    echo - If permission denied, right-click install.bat and Run as Administrator.
    pause
    exit /b 1
)

echo Removing stale desktop shortcut...
if exist "%SHORTCUT%" del /f /q "%SHORTCUT%"

echo Creating desktop shortcut...
powershell -NoProfile -Command "$ws = New-Object -ComObject WScript.Shell; $sc = $ws.CreateShortcut('%SHORTCUT%'); $sc.TargetPath = '%EXE%'; $sc.WorkingDirectory = '%INSTALL_DIR%'; $sc.Description = 'Job Manager CK v2.1'; $sc.Save()"
if errorlevel 1 (
    echo WARNING: Shortcut creation failed. You can launch manually from %EXE%
)

echo.
echo ============================================
echo  Job Manager CK v2.1 installed successfully
echo  Location: %EXE%
echo  Shortcut: %SHORTCUT%
echo ============================================
echo.
pause
endlocal
