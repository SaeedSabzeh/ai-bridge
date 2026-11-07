@echo off
setlocal
where pythonw.exe >nul 2>nul
if not errorlevel 1 (
  start "" pythonw.exe "%~dp0bridge_desktop.pyw"
  exit /b
)
where pyw.exe >nul 2>nul
if not errorlevel 1 (
  start "" pyw.exe -3 "%~dp0bridge_desktop.pyw"
  exit /b
)
echo AI Bridge requires Python 3.10 or later with Tkinter.
echo Run python bridge_desktop.pyw from this folder if Python is installed.
pause
exit /b 1
