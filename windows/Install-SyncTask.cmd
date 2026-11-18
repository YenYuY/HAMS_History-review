@echo off
setlocal DisableDelayedExpansion
set "source=Y:\"
set "destination=C:\Temp"
if not "%~1"=="" set "source=%~f1"
if not "%~2"=="" set "destination=%~f2"
if "%source:~-1%"=="\" set "source=%source%\"
if "%destination:~-1%"=="\" set "destination=%destination%\"

powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0Install-SyncTask.ps1" -Source "%source%" -DestinationDir "%destination%"
set "exit_code=%ERRORLEVEL%"
pause
exit /b %exit_code%
