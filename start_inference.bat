@echo off
setlocal
set "YOLO11_WORKSPACE_ROOT=%~dp0"
call "%~dp0yolo11_inference\start.bat" %*
exit /b %errorlevel%
