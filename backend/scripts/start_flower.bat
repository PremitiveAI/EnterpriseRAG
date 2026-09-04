@echo off
REM Flower has no authentication. Bind to localhost only.
cd /d "%~dp0.."
call venv\Scripts\activate
celery -A app.workers.celery_app flower --port=5555 --address=127.0.0.1
