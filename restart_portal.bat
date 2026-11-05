@echo off
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0restart_portal.ps1"
if errorlevel 1 pause
