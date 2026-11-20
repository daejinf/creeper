@echo off
rem MOIM: save the Timely API key into .env (input is hidden). Double-click this file.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\launcher\set-api-key.ps1"
pause
