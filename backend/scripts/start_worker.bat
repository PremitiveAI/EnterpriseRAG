@echo off
REM --pool=solo is required on Windows; it processes one document at a time.
cd /d "%~dp0.."
call venv\Scripts\activate
celery -A app.workers.celery_app worker -l info --pool=solo
