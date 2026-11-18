@echo off
setlocal DisableDelayedExpansion
set "app=%~dp0"
set "source=C:\Temp"
if not "%~1"=="" set "source=%~1/."
set "cache=%LOCALAPPDATA%\HAMSAccessQuery\cache"
if not defined LOCALAPPDATA set "cache=%USERPROFILE%\AppData\Local\HAMSAccessQuery\cache"

if not exist "%app%runtime\python\python.exe" goto incomplete
if not exist "%app%runtime\jre\bin\java.exe" goto incomplete
if not exist "%app%runtime\mdb-reader.jar" goto incomplete
if not exist "%app%runtime\jackcess.jar" goto incomplete
if not exist "%app%server.py" goto incomplete

"%app%runtime\python\python.exe" "%app%server.py" --open --source-dir "%source%" --cache-dir "%cache%" --java "%app%runtime\jre\bin\java.exe" --reader-jar "%app%runtime\mdb-reader.jar"
set "exit_code=%ERRORLEVEL%"
if not "%exit_code%"=="0" pause
exit /b %exit_code%

:incomplete
echo Extract the entire ZIP to a folder, then double-click Start.cmd in that folder.
pause
exit /b 1
