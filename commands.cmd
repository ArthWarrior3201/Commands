@echo off
rem Commands launcher for Windows. Needs Python 3.9 or newer.
setlocal
where py >nul 2>nul && goto :use_py
python "%~dp0run.py" %*
goto :done
:use_py
py -3 "%~dp0run.py" %*
:done
exit /b %errorlevel%
