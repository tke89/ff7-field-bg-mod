@echo off
rem FF7 Field Studio - needs ComfyUI (Desktop app) running; opens http://127.0.0.1:8765
cd /d "%~dp0"
".venv\Scripts\python.exe" studio.py
pause
