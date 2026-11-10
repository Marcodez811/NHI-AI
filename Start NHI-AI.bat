@echo off
rem Double-click to start NHI-AI. Needs Docker Desktop installed.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start_app.ps1"
pause
