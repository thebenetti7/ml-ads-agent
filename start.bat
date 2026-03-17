@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ============================================
echo    ML Ads Agent - Iniciando...
echo ============================================
echo.
echo Dashboard: http://localhost:8888
echo Para parar: Ctrl+C ou crie arquivo STOP
echo.

call venv\Scripts\activate
python -m app.main %*
pause
