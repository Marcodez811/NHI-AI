@echo off
rem Double-click to stop NHI-AI. Your documents and data are kept.
cd /d "%~dp0"
docker compose down
echo.
echo NHI-AI is stopped.
pause
