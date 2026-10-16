@echo off
rem Smoke test: same as start.bat but never sends anything into the groups.
rem Safe first run -- watch it for a few minutes, then use start.bat.
call "%~dp0start.bat" --dry-run %*