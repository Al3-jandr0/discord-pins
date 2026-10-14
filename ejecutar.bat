@echo off
title Extractor de Fijados Discord - Google Drive
cd /d "%~dp0"

REM Si existe el ejecutable .exe compilado, abrirlo directamente
if exist "DiscordPinsBackup.exe" (
    start "" "DiscordPinsBackup.exe"
    exit /b 0
)

REM Si no existe el .exe, abrir la interfaz grafica con Python
where pythonw >nul 2>&1
if %errorlevel% equ 0 (
    start "" pythonw gui_app.py
    exit /b 0
)

python gui_app.py
if errorlevel 1 (
    echo.
    echo [ERROR] No se pudo iniciar la interfaz grafica.
    pause
)
