@echo off
chcp 65001 >nul
echo ============================================
echo    ML Ads Agent - Instalador
echo ============================================
echo.

REM Verificar Python
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERRO] Python nao encontrado!
    echo.
    echo Instale Python 3.11 de https://python.org/downloads
    echo IMPORTANTE: Marque "Add Python to PATH" durante a instalacao
    echo.
    pause
    exit /b 1
)

echo [OK] Python encontrado
python --version

REM Criar ambiente virtual
echo.
echo [1/6] Criando ambiente virtual...
python -m venv venv
if errorlevel 1 (
    echo [ERRO] Falha ao criar ambiente virtual
    pause
    exit /b 1
)

REM Ativar e instalar dependencias
echo [2/6] Instalando dependencias Python...
call venv\Scripts\activate
pip install --upgrade pip >nul 2>&1
pip install -r requirements.txt
if errorlevel 1 (
    echo [ERRO] Falha ao instalar dependencias
    pause
    exit /b 1
)

REM Instalar Camoufox
echo.
echo [3/6] Instalando Camoufox (browser anti-detect)...
python -m camoufox fetch
if errorlevel 1 (
    echo [AVISO] Camoufox pode precisar de instalacao manual
)

REM Instalar Playwright browsers
echo.
echo [4/6] Instalando browsers Playwright...
python -m playwright install chromium
if errorlevel 1 (
    echo [AVISO] Playwright chromium pode precisar de instalacao manual
)

REM Criar diretorios
echo.
echo [5/6] Criando diretorios...
if not exist state mkdir state
if not exist profiles mkdir profiles
if not exist screenshots mkdir screenshots
if not exist logs mkdir logs

REM Copiar templates
echo [6/6] Preparando configuracao...
if not exist .env (
    copy .env.example .env >nul
    echo    .env criado - EDITE COM SEUS DADOS
) else (
    echo    .env ja existe - mantido
)
if not exist config.json (
    copy config.json.example config.json >nul
    echo    config.json criado - VERIFIQUE AS CONFIGURACOES
) else (
    echo    config.json ja existe - mantido
)

echo.
echo ============================================
echo    Instalacao concluida com sucesso!
echo ============================================
echo.
echo Proximos passos:
echo   1. Edite .env com suas credenciais
echo   2. Verifique config.json
echo   3. Execute start.bat para iniciar
echo.
pause
