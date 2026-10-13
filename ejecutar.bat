@echo off
title Extractor de Fijados Discord - Google Drive
color 0A

echo.
echo  ==========================================
echo   Extractor de Fijados: Discord -^> Drive
echo  ==========================================
echo.

cd /d "%~dp0"

python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python no esta instalado o no esta en el PATH.
    echo Por favor, instala Python desde https://python.org
    echo y asegurate de marcar "Add python.exe to PATH".
    echo.
    pause
    exit /b 1
)

if not exist ".env" (
    echo [ERROR] No se encontro el archivo .env en esta carpeta.
    echo Copia el archivo .env.example, renombralo a .env
    echo y completa tus credenciales.
    echo.
    pause
    exit /b 1
)

if not exist "credentials.json" (
    echo [ERROR] No se encontro el archivo credentials.json.
    echo Descarga el JSON de tu Cuenta de Servicio de Google Cloud
    echo y colocalo aqui con el nombre credentials.json.
    echo.
    pause
    exit /b 1
)

echo  Iniciando sincronizacion...
echo  ==========================================
echo.

python discord_pins_sync.py

echo.
echo  ==========================================
if errorlevel 1 (
    echo  [!] El script termino con errores.
) else (
    echo  [OK] Proceso finalizado correctamente.
)
echo.
pause
