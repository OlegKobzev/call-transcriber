@echo off
setlocal
set "ROOT=%~dp0"
if not exist "%ROOT%.venv\Scripts\python.exe" (
  echo Not installed yet. Run install.cmd first.
  exit /b 1
)
"%ROOT%.venv\Scripts\python.exe" -m call_transcriber %*
