@echo off
cd /d "%~dp0"
title Video Factory - keep this window open
echo ==================================================
echo   Video Factory  ->  http://localhost:8088
echo   Keep this window OPEN while using.
echo   Close it to stop the server.
echo ==================================================
start "" "%~dp0video-factory.html"
node live-server.js
echo.
echo (server stopped)
pause >nul
