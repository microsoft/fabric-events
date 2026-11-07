@echo off
setlocal
python "%~dp0card.py" deploy %*
exit /b %errorlevel%
