@echo off
rem FF7: 2D: the new field backgrounds only, flat as in the 2D game (no 3D scenes, lighting or shaders)
cd /d "%~dp0"
"..\..\.venv\Scripts\python.exe" game_mode.py backgrounds
echo.
pause
