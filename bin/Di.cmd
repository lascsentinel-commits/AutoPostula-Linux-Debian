@echo off
set SCRIPT_DIR=%~dp0
if exist "%SCRIPT_DIR%..\venv\Scripts\python.exe" (
    "%SCRIPT_DIR%..\venv\Scripts\python.exe" "%SCRIPT_DIR%di.py" %*
) else (
    python "%SCRIPT_DIR%di.py" %*
)

