@echo off
setlocal
chcp 65001 >nul
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
pushd "%~dp0"
if defined WQ_PYTHON goto custom_python
where python >nul 2>nul
if errorlevel 1 goto no_python
python scripts\serve_web.py %*
set "wq_result=%errorlevel%"
goto finish
:custom_python
if not exist "%WQ_PYTHON%" goto bad_python
"%WQ_PYTHON%" scripts\serve_web.py %*
set "wq_result=%errorlevel%"
goto finish
:bad_python
echo [ERROR] WQ_PYTHON 指向的解释器不存在: %WQ_PYTHON%
echo [INFO] 请设置为本机 Python 3.11+ 的 python.exe 完整路径（本机以 3.14 验证）。
set "wq_result=1"
goto finish
:no_python
echo [ERROR] Python not found. Install Python 3.11+, or set WQ_PYTHON to python.exe.
set "wq_result=1"
:finish
if not "%wq_result%"=="0" pause
popd
exit /b %wq_result%
