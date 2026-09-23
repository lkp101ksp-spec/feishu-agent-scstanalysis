@echo off
rem One-click: register ws_guardian scheduled task (wraps install_guardian_task.ps1).
rem Double-click to install+start; uninstall: install_guardian_task.bat -Uninstall
chcp 65001 >nul
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install_guardian_task.ps1" %*
echo.
pause
