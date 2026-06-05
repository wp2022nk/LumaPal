@echo off
setlocal EnableDelayedExpansion
set PYTHONUTF8=1

if /I "%~1"=="dev" goto dev
uv run langgraph %*
exit /b %ERRORLEVEL%

:dev
set "ARGS="
shift /1

:collect_dev_args
if "%~1"=="" goto run_dev
set "ARGS=!ARGS! %1"
shift /1
goto collect_dev_args

:run_dev
uv run langgraph dev --allow-blocking !ARGS!
exit /b %ERRORLEVEL%
