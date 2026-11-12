@echo off
python "%~dp0deploy.py" test %*
exit /b %errorlevel%
