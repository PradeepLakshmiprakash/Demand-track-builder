@echo off
rem Starts the private local Postgres cluster (port 5434) used by Demand Tracker on this machine.
rem It is not a Windows service; run this after a reboot, before the app or tests.
"C:\Program Files\PostgreSQL\17\bin\pg_ctl.exe" -D "%~dp0..\..\demand-tracker-db\data" -l "%~dp0..\..\demand-tracker-db\server.log" status >nul 2>&1
if %errorlevel%==0 (
  echo Postgres already running on 5434.
) else (
  "C:\Program Files\PostgreSQL\17\bin\pg_ctl.exe" -D "%~dp0..\..\demand-tracker-db\data" -l "%~dp0..\..\demand-tracker-db\server.log" start -w
)
