@echo off
setlocal
cd /d "%~dp0"

set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY where python >nul 2>nul && set "PY=python"
if not defined PY (
  echo Python 3.11+ was not found. Install it, for example: winget install Python.Python.3.13
  exit /b 1
)
%PY% -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)"
if errorlevel 1 (
  echo Python 3.11 or newer is required.
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  echo Creating virtual environment...
  %PY% -m venv .venv
  if errorlevel 1 exit /b 1
)
set "VPY=.venv\Scripts\python.exe"

echo Installing packages...
"%VPY%" -m pip install --disable-pip-version-check -q -r requirements.txt
if errorlevel 1 exit /b 1

where nvidia-smi >nul 2>nul
if errorlevel 1 (
  echo No NVIDIA GPU found: transcription will run on the CPU - slower, smaller model.
) else (
  echo Installing CUDA libraries for the NVIDIA GPU...
  "%VPY%" -m pip install --disable-pip-version-check -q -r requirements-cuda.txt
  if errorlevel 1 exit /b 1
)

if not exist config.toml copy config.example.toml config.toml >nul

"%VPY%" -m call_transcriber.prepare
if errorlevel 1 exit /b 1

echo.
echo Done. Start a recording with:  ct start
echo Add this folder to PATH to run ct from anywhere: %~dp0
