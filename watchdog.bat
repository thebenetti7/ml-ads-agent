@echo off
REM Watchdog — Verifica se o ML Ads Agent esta rodando e reinicia se necessario.
REM Agendar no Task Scheduler para rodar a cada 5 minutos.

cd /d "%~dp0"

REM Verificar se python esta rodando o agent
tasklist /FI "IMAGENAME eq python.exe" 2>NUL | find /I "python.exe" >NUL
if errorlevel 1 (
    echo [%date% %time%] Agent nao esta rodando. Reiniciando... >> logs\watchdog.log
    start "" /MIN cmd /c "call venv\Scripts\activate && python -m app.main >> logs\agent_stdout.log 2>&1"
    echo [%date% %time%] Agent reiniciado. >> logs\watchdog.log
) else (
    REM Verificar se o endpoint /health responde
    curl -s -o NUL -w "%%{http_code}" http://localhost:8888/health > "%TEMP%\health_check.txt" 2>NUL
    set /p HEALTH_CODE=<"%TEMP%\health_check.txt"
    if not "%HEALTH_CODE%"=="200" (
        echo [%date% %time%] Health check falhou (code=%HEALTH_CODE%). Reiniciando... >> logs\watchdog.log
        taskkill /F /IM python.exe >NUL 2>&1
        timeout /t 5 /nobreak >NUL
        start "" /MIN cmd /c "call venv\Scripts\activate && python -m app.main >> logs\agent_stdout.log 2>&1"
        echo [%date% %time%] Agent reiniciado apos health check falho. >> logs\watchdog.log
    )
)
