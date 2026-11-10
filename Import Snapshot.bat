@echo off
rem Double-click to load a snapshot copied into the .\snapshot folder.
rem REPLACES this install's data. Run "Start NHI-AI.bat" once before using this.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\snapshot_import.ps1"
pause
