@echo off
rem FF7: the full 3D setup (FFNx-3d: 3D scenes, lighting and shaders)
cd /d "%~dp0"
"..\..\.venv\Scripts\python.exe" game_mode.py 3d
echo.
pause
