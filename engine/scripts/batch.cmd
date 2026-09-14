@echo off
setlocal
set "PYTHON_EXE=%~dp0..\..\.venv\Scripts\python.exe"

if not "%~1"=="" goto :fatal
if not exist "%PYTHON_EXE%" goto :fatal

"%PYTHON_EXE%" -c "from ipc.batch_stdio import main; import importlib.metadata; importlib.metadata.version('cad-copilot')" >nul 2>&1
if errorlevel 1 goto :fatal

"%PYTHON_EXE%" -m ipc.batch_stdio %*
exit /b %ERRORLEVEL%

:fatal
>&2 echo {"type":"diagnostic","phase":"fatal","message":"Batch process failed before a contract response could be produced."}
exit /b 1
