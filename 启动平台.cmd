@echo off
chcp 65001 >nul
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0启动平台.ps1" -OpenBrowser
if errorlevel 1 pause
