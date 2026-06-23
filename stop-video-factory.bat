@echo off
for /f "tokens=5" %%a in ('netstat -ano ^| findstr :8088 ^| findstr LISTENING') do taskkill /F /PID %%a >nul 2>nul
echo Video Factory (port 8088) stopped.
timeout /t 2 >nul
