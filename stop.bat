@echo off
chcp 65001 > nul
title MAPS Stop

echo [MAPS] Stopping server on port 8000...

for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":8000 " ^| findstr "LISTENING"') do (
    echo [MAPS] Killing PID %%a
    taskkill /PID %%a /F /T > nul 2>&1
)

echo [MAPS] Server stopped.
pause
