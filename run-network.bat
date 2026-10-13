@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo Creating Python virtual environment...
    python -m venv .venv
    if errorlevel 1 (
        echo Failed to create virtual environment. Ensure Python is installed and added to PATH.
        exit /b 1
    )
)

.venv\Scripts\python.exe -c "import flask, cv2, numpy, PIL, flask_sock" >nul 2>&1
if errorlevel 1 (
    echo Installing dependencies...
    .venv\Scripts\python.exe -m pip install --upgrade pip
    .venv\Scripts\python.exe -m pip install -r requirements.txt
)

.venv\Scripts\python.exe scripts\download_models.py

echo Starting Identiq over HTTPS on port 8443...
.venv\Scripts\python.exe scripts\run_network.py %*
endlocal
