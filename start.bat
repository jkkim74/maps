@echo off
chcp 65001 > nul
title MAPS Server

set "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%"

echo [MAPS] Starting server...
echo [MAPS] Working directory: %CD%
echo [MAPS] URL: http://localhost:8000
echo.

python -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload

echo.
echo [MAPS] Server stopped.
pause
