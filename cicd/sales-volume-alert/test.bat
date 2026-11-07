@echo off
setlocal
python "%~dp0card.py" test %*
exit /b %errorlevel%
