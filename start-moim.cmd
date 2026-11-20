@echo off
rem MOIM local run: double-click this file. First run installs everything; later runs just start the server.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\launcher\start.ps1"
if errorlevel 1 pause
