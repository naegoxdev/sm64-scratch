@echo off
setlocal
cd /d "%~dp0"

echo ============================================================
echo  Mario 64 Scratch - build from your own ROM
echo ============================================================
echo.

where py >nul 2>nul
if %errorlevel%==0 (
  set PY=py -3
) else (
  set PY=python
)

%PY% --version || (
  echo.
  echo ERROR: Python 3 was not found.
  echo Install Python 3.10 or newer and run this file again.
  pause
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  echo Creating virtual environment...
  %PY% -m venv .venv || exit /b 1
)

call ".venv\Scripts\activate.bat"
python -m pip install --upgrade pip
python -m pip install -r requirements.txt || (
  echo ERROR installing dependencies.
  pause
  exit /b 1
)

set /p ROM=Drag your US Super Mario 64 ROM (.z64/.n64/.v64) here and press ENTER: 
set ROM=%ROM:"=%
if not exist "%ROM%" (
  echo ERROR: The specified file does not exist.
  pause
  exit /b 1
)

if not exist output mkdir output
python build_game.py --rom "%ROM%" --out "output\MARIO64_SCRATCH.sb3" --report "output\build_report.json"
if errorlevel 1 (
  echo.
  echo The build failed. Check the error message above.
  pause
  exit /b 1
)

echo.
echo READY: output\MARIO64_SCRATCH.sb3
echo Report: output\build_report.json
pause
