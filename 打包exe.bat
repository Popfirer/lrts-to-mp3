@echo off
chcp 65001 >nul
cd /d %~dp0
echo 正在打包，请稍候（首次约 1~3 分钟）...
echo.
"C:\ProgramData\anaconda3\python.exe" "%~dp0打包exe.py"
echo.
echo 按任意键关闭窗口...
pause >nul
