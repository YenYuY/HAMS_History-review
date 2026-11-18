@echo off
setlocal DisableDelayedExpansion
set "app=%~dp0"
set "source=Y:\"
set "destination=C:\Temp"
if not "%~1"=="" set "source=%~f1"
if not "%~2"=="" set "destination=%~f2"
if "%source:~-1%"=="\" set "source=%source%\"
if "%destination:~-1%"=="\" set "destination=%destination%\"

if not exist "%app%runtime\python\python.exe" goto incomplete
if not exist "%app%sync_mdb.py" goto incomplete

"%app%runtime\python\python.exe" "%app%sync_mdb.py" --source "%source%" --destination-dir "%destination%"
set "exit_code=%ERRORLEVEL%"
pause
exit /b %exit_code%

:incomplete
echo Extract the entire ZIP before running Sync-Now.cmd.
pause
exit /b 1
