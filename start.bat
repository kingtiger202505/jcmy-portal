@echo off
rem ===========================================================================
rem  WeChat group CS bot - one-click launcher (Windows). Double-click to run.
rem
rem  NOTE: deliberately ASCII-only. Chinese text inside a .bat gets mangled by
rem  cmd.exe depending on the console code page, and mangled quotes break the
rem  parsing. All Chinese guidance lives in README.txt and in the bot itself.
rem
rem  Usage:
rem    start.bat                  normal run (really replies in groups)
rem    start.bat --dry-run        compute replies but never send
rem    start.bat --no-reply       monitor only
rem    start.bat --duration 60    quit after 60s (smoke test)
rem
rem  Requirements: Python 3.10-3.12, WeChat running + logged in, and python must
rem  run at the SAME integrity level as WeChat (never launch WeChat as admin).
rem  Group names in groups.txt must match the chat list exactly.
rem
rem  Log: bot.log in this folder. Send that file when something goes wrong.
rem ===========================================================================
chcp 65001 >nul
setlocal enabledelayedexpansion
cd /d "%~dp0"

set "DEPS=uiautomation,win32gui,rapidocr_onnxruntime,pyperclip,requests,numpy,PIL"
set "MIRROR=-i https://pypi.tuna.tsinghua.edu.cn/simple"

echo ============================================
echo   WeChat group CS bot
echo ============================================

for %%F in (wechat_group_bot.py wechat_bot.py requirements.txt) do (
  if not exist "%%F" (
    echo [ERROR] missing %%F -- copy the WHOLE folder, not just one file
    goto end
  )
)

rem ---- 1) pick the interpreter ----------------------------------------------
rem Prefer 3.12/3.11/3.10: the OCR stack (onnxruntime / opencv) only ships
rem wheels for those. On a brand-new 3.13/3.14 the install dies with
rem "No matching distribution found" -- which looks like a network problem but
rem is really a too-new-python problem. So pick a version that can work.
set "BASE="
set "PYVER="
call :probe "py -3.12"
call :probe "py -3.11"
call :probe "py -3.10"
if not defined BASE (
  rem No 3.10-3.12 on this machine. A brand-new python (3.13/3.14/3.15) does NOT
  rem work here: the OCR engine rapidocr-onnxruntime declares Requires-Python
  rem <3.13 and ships no build for it, so it can never be installed. Ranges in
  rem requirements.txt cannot fix that -- only an older python can.
  echo [i] no Python 3.10-3.12 found; this bot needs one of those.
  where winget >nul 2>nul
  if not errorlevel 1 (
    echo [i] trying to install Python 3.12 with winget ^(may take a few minutes^) ...
    winget install -e --id Python.Python.3.12 --accept-package-agreements --accept-source-agreements
    call :probe "py -3.12"
  )
)
if not defined BASE (
  echo [ERROR] still no usable Python.
  echo         Install Python 3.12 by hand: python.org, scroll to
  echo         "Looking for a specific release?", pick Python 3.12.x, then
  echo         "Windows installer ^(64-bit^)", tick "Add python.exe to PATH".
  echo         Do NOT use 3.13/3.14/3.15. See README.txt step 1.
  goto end
)
for /f "tokens=2" %%V in ('%BASE% -V 2^>nul') do set "PYVER=%%V"
rem Resolve it to a real .exe path: BASE is something like "py -3.11" (a command
rem with a space), and cmd treats "py -3.11" in quotes as one program name.
rem Note: no parentheses in the -c string, they would break for /f.
set "PYEXE="
for /f "delims=" %%P in ('%BASE% -c "import sys;sys.stdout.write(sys.executable)" 2^>nul') do set "PYEXE=%%P"
if not defined PYEXE (
  echo [ERROR] could not resolve the python path from %BASE%
  goto end
)
echo [1] interpreter: %BASE%  ^(python !PYVER!^)

rem ---- 2) system python already has everything? use it directly -------------
"%PYEXE%" -c "import %DEPS%" >nul 2>nul
if not errorlevel 1 (
  set "PY=%PYEXE%"
  echo [2] dependencies OK ^(system python^)
  goto run
)

rem ---- 3) build .venv from the interpreter we just picked -------------------
rem Wipe any existing .venv first. A half-finished install leaves an empty shell
rem behind (every later run then dies with "No module named 'numpy'"), and a
rem .venv built from a too-new python can never install our deps. Either way the
rem cure is the same: rebuild it from the interpreter chosen above.
if exist ".venv" (
  echo [2] removing the old .venv and rebuilding it ...
  rmdir /s /q .venv
)
if not exist ".venv" (
  echo [3] creating virtualenv .venv from %BASE% ...
)
"%PYEXE%" -m venv .venv
if not exist ".venv\Scripts\python.exe" (
  echo [ERROR] could not create .venv -- is python on PATH? see README.txt step 1
  goto end
)
set "PY=.venv\Scripts\python.exe"

echo [4] installing dependencies - first time only, a few minutes ...
".venv\Scripts\python.exe" -m pip install --quiet --upgrade pip %MIRROR%
".venv\Scripts\python.exe" -m pip install -r requirements.txt %MIRROR%
if errorlevel 1 (
  echo [WARN] mirror failed, retrying with the default PyPI ...
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt
)
".venv\Scripts\python.exe" -c "import %DEPS%" >nul 2>nul
if errorlevel 1 (
  echo [ERROR] dependencies still missing after install - see the errors above
  echo         if it says "No matching distribution found", this python is too
  echo         new for these packages: install Python 3.12 and run again
  echo         ^(README.txt step 1^)
  goto end
)
echo [4] dependencies installed

:run
echo.
echo running: !PY! wechat_group_bot.py %*
echo log: bot.log      stop: Ctrl+C
echo --------------------------------------------
"!PY!" -u wechat_group_bot.py %*
echo.
echo bot exited, code %errorlevel%. log: bot.log

:end
pause
endlocal
goto :eof

:probe
rem :probe "<command>"  -- keep the first one that actually runs
if defined BASE goto :eof
%~1 -c "import sys" >nul 2>nul
if not errorlevel 1 set "BASE=%~1"
goto :eof