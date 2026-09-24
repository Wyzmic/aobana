@echo off
cd /d "%~dp0"
if exist "%~dp0python\python.exe" (
    "%~dp0python\python.exe" "%~dp0launcher.py"
) else (
    python "%~dp0launcher.py"
)
