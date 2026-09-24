@echo off
rem Starts Demand Tracker on http://localhost:8010 in this window (Ctrl+C to stop).
rem Runs independently of the Claude desktop app's preview pane.
cd /d "%~dp0.."
call "%~dp0db-start.cmd"
".venv\Scripts\python.exe" -m uvicorn app.main:app --port 8010 --env-file .env
