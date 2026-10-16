@echo off
rem ===========================================================================
rem  Diagnostics for the WeChat CS bot. Double-click it, then send back the two
rem  files it produces:  diagnose-report.txt  and  diagnose-screenshot.png
rem
rem  Stop the bot first (both fight over the foreground window).
rem  ASCII-only on purpose -- see start.bat for why.
rem ===========================================================================
chcp 65001 >nul
setlocal enabledelayedexpansion
cd /d "%~dp0"

echo ============================================
echo   WeChat group CS bot - diagnostics
echo ============================================
echo   (stop the bot first: close its window or press Ctrl+C)
echo.

set "BASE="
call :probe "py -3.12"
call :probe "py -3.11"
call :probe "py -3.10"
if not defined BASE (
  echo [ERROR] no Python 3.10-3.12 found - see README.txt step 1
  goto end
)

rem Prefer the .venv made by start.bat (it definitely has the deps)
set "PYEXE="
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" -c "import numpy" >nul 2>nul
  if not errorlevel 1 set "PYEXE=%CD%\.venv\Scripts\python.exe"
)
if not defined PYEXE (
  for /f "delims=" %%P in ('%BASE% -c "import sys;sys.stdout.write(sys.executable)" 2^>nul') do set "PYEXE=%%P"
)
if not defined PYEXE (
  echo [ERROR] could not resolve the python path
  goto end
)

echo running diagnostics with: !PYEXE!
echo --------------------------------------------
"!PYEXE!" -u diagnose.py

echo.
echo send these two files back to the developer:
echo    %CD%\diagnose-report.txt
echo    %CD%\diagnose-screenshot.png

:end
pause
endlocal
goto :eof

:probe
if defined BASE goto :eof
%~1 -c "import sys" >nul 2>nul
if not errorlevel 1 set "BASE=%~1"
goto :eof