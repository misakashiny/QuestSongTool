@echo off
chcp 65001 >nul
setlocal
"%SystemRoot%\System32\wscript.exe" "%~dp0启动.vbs" %*
exit /b %errorlevel%
