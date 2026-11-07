@echo off
python "%~dp0card.py" deploy %*
exit /b %errorlevel%
