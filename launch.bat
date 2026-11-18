@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Creating lightweight Python environment...
  py -3 -m venv .venv
  if errorlevel 1 goto no_python
  call .venv\Scripts\activate.bat
  python -m pip install --upgrade pip
  pip install -r requirements.txt
) else (
  call .venv\Scripts\activate.bat
)
python app.py
pause
goto :eof
:no_python
echo Python 3.10 or newer was not found. Install Python from python.org and enable Add to PATH.
pause
