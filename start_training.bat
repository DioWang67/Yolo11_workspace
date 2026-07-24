@echo off
setlocal
set "YOLO11_WORKSPACE_ROOT=%~dp0"
call "%~dp0Yolo11_auto_train\open_operator_training.bat" %*
exit /b %errorlevel%
