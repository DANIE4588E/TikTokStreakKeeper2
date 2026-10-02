@echo off
rem Daily wrapper used by the scheduled task: runs the messenger unattended
rem and appends everything to logs\cron.log.
cd /d "%~dp0"
if not exist logs mkdir logs

set "PY=python"
where python >nul 2>nul || set "PY=py"

echo ========== run %date% %time% ========== >> "logs\cron.log"
"%PY%" send_messages.py --unattended >> "logs\cron.log" 2>&1
echo ========== exit code %ERRORLEVEL% ====== >> "logs\cron.log"
