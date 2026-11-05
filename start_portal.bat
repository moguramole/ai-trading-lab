@echo off
rem ポータルをブラウザで開く。まだ動いていなければ起動する（PCにログインすると自動起動もされる）。
cd /d "%~dp0"
start "" http://localhost:8501
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0run_portal.ps1"
