@echo off
python "%~dp0card.py" test %*
exit /b %errorlevel%
