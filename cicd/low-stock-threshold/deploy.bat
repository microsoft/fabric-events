@echo off
python "%~dp0deploy.py" deploy %*
exit /b %errorlevel%
