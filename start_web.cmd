@echo off
setlocal
pushd "%~dp0"
if defined WQ_PYTHON goto custom_python
where python >nul 2>nul
if errorlevel 1 goto no_python
python scripts\serve_web.py %*
set "wq_result=%errorlevel%"
goto finish
:custom_python
"%WQ_PYTHON%" scripts\serve_web.py %*
set "wq_result=%errorlevel%"
goto finish
:no_python
echo [ERROR] Python not found. Install Python 3.12, or set WQ_PYTHON to python.exe.
set "wq_result=1"
:finish
if not "%wq_result%"=="0" pause
popd
exit /b %wq_result%
