@echo off
cd /d C:\dosemed
python -m uvicorn main:app --host 0.0.0.0 --port 8000
