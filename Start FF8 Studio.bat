@echo off
rem FF8 Field Studio (FINAL FANTASY VIII Remastered) - needs ComfyUI (Desktop app) running; opens http://127.0.0.1:8767
rem Its renders and settings live in projects\ff8, apart from FF7's. Both Studios can run at once.
cd /d "%~dp0"
".venv\Scripts\python.exe" studio.py --game ff8
pause
