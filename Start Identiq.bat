@echo off
setlocal EnableExtensions DisableDelayedExpansion
pushd "%~dp0" || exit /b 1
set "PYTHONUTF8=1"
set "venvPython=.venv\Scripts\python.exe"
set "python313=%LocalAppData%\Programs\Python\Python313\python.exe"
if not defined PORT set "PORT=8080"
if not defined IDENTIQ_RAPID_CAPTURE set "IDENTIQ_RAPID_CAPTURE=1"

"%venvPython%" -c "import sys; assert sys.version_info[:2] == (3,13) and sys.maxsize > 2**32" >nul 2>&1
if not errorlevel 1 goto dependencies

call :create_environment
if not errorlevel 1 goto dependencies
where winget >nul 2>&1
if errorlevel 1 goto python_needed
echo Installing Python 3.13 for this Windows user...
winget install --id Python.Python.3.13 --exact --source winget --scope user --architecture x64 --accept-package-agreements --accept-source-agreements
if errorlevel 1 goto python_needed
call :create_environment
if errorlevel 1 goto python_needed

:dependencies
fc /b requirements.txt ".venv\identiq-requirements.txt" >nul 2>&1
if errorlevel 1 goto install
"%venvPython%" -c "import flask, flask_sock, cv2, numpy, PIL, onnxruntime" >nul 2>&1
if not errorlevel 1 goto models

:install
echo Installing attendance dependencies...
"%venvPython%" -m pip install --only-binary=:all: -r requirements.txt
if errorlevel 1 goto failed
"%venvPython%" -c "import flask, flask_sock, cv2, numpy, PIL, onnxruntime"
if errorlevel 1 goto failed
copy /y requirements.txt ".venv\identiq-requirements.txt" >nul
if errorlevel 1 goto failed

:models
"%venvPython%" scripts\download_models.py
if errorlevel 1 goto failed
echo Open http://localhost:%PORT% in your browser.
echo Initial staff credentials are in data\staff-login.json.
"%venvPython%" app.py
if errorlevel 1 goto failed
popd
exit /b 0

:create_environment
if exist "%python313%" goto user_python
py -3.13 -c "import sys; assert sys.maxsize > 2**32" >nul 2>&1
if errorlevel 1 exit /b 1
py -3.13 -m venv .venv
exit /b %errorlevel%

:user_python
"%python313%" -c "import sys; assert sys.maxsize > 2**32" >nul 2>&1
if errorlevel 1 exit /b 1
"%python313%" -m venv .venv
exit /b %errorlevel%

:python_needed
echo Install 64-bit Python 3.13 from python.org, then double-click this file again.
:failed
echo Startup stopped. Check the message above.
pause
popd
exit /b 1
