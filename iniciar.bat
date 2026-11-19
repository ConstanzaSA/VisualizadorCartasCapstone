@echo off
cd /d "%~dp0"
where uv >nul 2>nul
if not errorlevel 1 (
    echo Preparando el escaner. La primera vez puede tardar un poco...
    uv run --isolated --no-project --python 3.11 --with-requirements requirements.txt main.py
    if errorlevel 1 pause
    exit /b
)
if exist ".venv\Scripts\python.exe" (
    .venv\Scripts\python.exe --version >nul 2>nul
    if not errorlevel 1 (
        .venv\Scripts\python.exe main.py
        if errorlevel 1 pause
        exit /b
    )
)
echo No se encontro una instalacion funcional de Python.
echo Instala uv desde https://docs.astral.sh/uv/getting-started/installation/
echo Luego cierra esta ventana y vuelve a abrir iniciar.bat.
pause
exit /b 1
