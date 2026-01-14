@echo off
REM ============================================================
REM   WhatsApp Fuel Extractor - Unified Launcher
REM   All-in-one setup and run script for Windows
REM ============================================================

setlocal EnableDelayedExpansion
cd /d "%~dp0"

REM Handle command line arguments
if "%1"=="--setup" goto setup_menu
if "%1"=="--clean" goto clean_install
if "%1"=="--help" goto show_help
if "%1"=="-h" goto show_help

REM Check if conda is available
where conda >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo.
    echo  ============================================================
    echo     WhatsApp Fuel Extractor
    echo  ============================================================
    echo.
    echo  [ERROR] Conda is not installed or not in PATH
    echo.
    echo  Please install Anaconda or Miniconda from:
    echo    https://docs.conda.io/en/latest/miniconda.html
    echo.
    echo  After installation, restart this script.
    echo.
    pause
    exit /b 1
)

REM Check if environment exists
conda env list | findstr /C:"fuel-extractor" >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo.
    echo  ============================================================
    echo     WhatsApp Fuel Extractor - First Time Setup
    echo  ============================================================
    echo.
    echo  Python environment 'fuel-extractor' not found.
    echo.
    set /p do_setup="  Would you like to set it up now? (y/n): "
    if /i "!do_setup!"=="y" (
        goto full_install
    ) else (
        echo.
        echo  Setup cancelled. Run this script again when ready.
        echo.
        pause
        exit /b 0
    )
)

REM Check if Node modules are installed
if not exist "node_modules\whatsapp-web.js" (
    echo.
    echo  ============================================================
    echo     WhatsApp Fuel Extractor - Node.js Setup Required
    echo  ============================================================
    echo.
    echo  Node.js dependencies are not installed.
    echo.
    where node >nul 2>&1
    if %ERRORLEVEL% neq 0 (
        echo  [WARNING] Node.js is not installed.
        echo  Please install from: https://nodejs.org/
        echo.
        echo  You can still use some features without Node.js.
        echo.
        pause
    ) else (
        set /p do_npm="  Would you like to install them now? (y/n): "
        if /i "!do_npm!"=="y" (
            goto node_install_only
        )
    )
)

REM All set - run the interactive CLI
call conda activate fuel-extractor
python cli.py %*

if %ERRORLEVEL% neq 0 (
    echo.
    echo  Command failed with error code %ERRORLEVEL%
    pause
)
goto end

REM ============================================================
REM   SETUP MENU
REM ============================================================
:setup_menu
cls
echo.
echo  ============================================================
echo     WhatsApp Fuel Extractor - Setup Menu
echo  ============================================================
echo.

REM Check current installation status
set "CONDA_OK=0"
set "ENV_OK=0"
set "NODE_OK=0"
set "NPM_OK=0"

where conda >nul 2>&1 && set "CONDA_OK=1"
if "%CONDA_OK%"=="1" (
    conda env list 2>nul | findstr /C:"fuel-extractor" >nul 2>&1 && set "ENV_OK=1"
)
where node >nul 2>&1 && set "NODE_OK=1"
if exist "node_modules\puppeteer" set "NPM_OK=1"

echo   Current Status:
if "%CONDA_OK%"=="1" (echo     [OK] Conda installed) else (echo     [X]  Conda NOT found)
if "%ENV_OK%"=="1" (echo     [OK] Python environment ready) else (echo     [X]  Python environment NOT found)
if "%NODE_OK%"=="1" (echo     [OK] Node.js installed) else (echo     [X]  Node.js NOT found)
if "%NPM_OK%"=="1" (echo     [OK] Node modules installed) else (echo     [X]  Node modules NOT installed)
echo.
echo  ------------------------------------------------------------
echo.
echo     [1] Full Install (first time setup)
echo     [2] Update Only (reinstall dependencies)
echo     [3] Clean Install (remove everything and reinstall)
echo     [4] Install Python packages only
echo     [5] Install Node.js packages only
echo.
echo     [R] Run Application
echo     [Q] Quit
echo.
echo  ============================================================
echo.

set /p setup_choice="  Select option: "

if "%setup_choice%"=="1" goto full_install
if "%setup_choice%"=="2" goto update_install
if "%setup_choice%"=="3" goto clean_install
if "%setup_choice%"=="4" goto python_install
if "%setup_choice%"=="5" goto node_install_only
if /i "%setup_choice%"=="r" goto run_app
if /i "%setup_choice%"=="q" goto end

echo  Invalid option. Press any key to continue...
pause >nul
goto setup_menu

REM ============================================================
REM   INSTALLATION ROUTINES
REM ============================================================

:full_install
cls
echo.
echo  ============================================================
echo     Full Installation
echo  ============================================================
echo.

REM Check conda
where conda >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo  [ERROR] Conda is not installed or not in PATH
    echo.
    echo  Please install Anaconda or Miniconda from:
    echo    https://docs.conda.io/en/latest/miniconda.html
    echo.
    pause
    goto setup_menu
)

echo  [1/5] Creating conda environment 'fuel-extractor'...
call conda create -n fuel-extractor python=3.11 -y 2>nul
if %ERRORLEVEL% neq 0 (
    echo         Environment may already exist, continuing...
)

echo.
echo  [2/5] Installing Python dependencies...
call conda run -n fuel-extractor pip install -r requirements.txt --quiet
if %ERRORLEVEL% neq 0 (
    echo  [ERROR] Failed to install Python dependencies
    pause
    goto setup_menu
)

REM Check Node.js
where node >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo.
    echo  [WARNING] Node.js is not installed
    echo  Please install Node.js from: https://nodejs.org/
    echo  Then run: run_fuel_extractor --setup
    echo.
    goto skip_node_full
)

echo.
echo  [3/5] Installing Node.js dependencies...
echo        (This may take a few minutes to download Chromium)
set PUPPETEER_SKIP_CHROMIUM_DOWNLOAD=false
call npm install --loglevel=error
if %ERRORLEVEL% neq 0 (
    echo  [WARNING] Node.js installation had issues
)

:skip_node_full

echo.
echo  [4/5] Creating data directories...
if not exist data\raw_messages mkdir data\raw_messages
if not exist data\processed mkdir data\processed
if not exist data\errors mkdir data\errors
if not exist data\output mkdir data\output
if not exist data\session mkdir data\session

REM Create default JSON files
if not exist data\car_last_update.json echo {} > data\car_last_update.json
if not exist data\driver_history.json echo {} > data\driver_history.json
if not exist data\last_processed.json echo {} > data\last_processed.json
if not exist data\car_summary.json echo {} > data\car_summary.json
if not exist data\pending_approvals.json echo [] > data\pending_approvals.json

echo.
echo  [5/5] Checking configuration...
if not exist .env (
    if exist .env.example (
        copy .env.example .env >nul
        echo         Created .env from template
    )
)

echo.
echo  ============================================================
echo     Installation Complete!
echo  ============================================================
echo.
echo  Next steps:
echo    1. Edit config.json with your WhatsApp group name
echo    2. Start the application to begin
echo.
set /p start_now="  Start the application now? (y/n): "
if /i "%start_now%"=="y" goto run_app
goto setup_menu

:update_install
cls
echo.
echo  ============================================================
echo     Update Installation
echo  ============================================================
echo.
echo  Updating all dependencies without removing existing data...
echo.

echo  [1/2] Updating Python packages...
call conda run -n fuel-extractor pip install -r requirements.txt --upgrade --quiet 2>nul
if %ERRORLEVEL% neq 0 (
    echo  [WARNING] Python update had issues
)

echo.
echo  [2/2] Updating Node.js packages...
call npm update --loglevel=error 2>nul
if %ERRORLEVEL% neq 0 (
    echo  [WARNING] Node.js update had issues
)

echo.
echo  Update complete!
echo.
pause
goto setup_menu

:clean_install
cls
echo.
echo  ============================================================
echo     Clean Installation
echo  ============================================================
echo.
echo  WARNING: This will remove:
echo    - node_modules folder
echo    - package-lock.json
echo    - Conda environment (will be recreated)
echo.
echo  Your data and configuration will be preserved.
echo.
set /p confirm="  Are you sure? (y/n): "
if /i not "%confirm%"=="y" goto setup_menu

echo.
echo  Cleaning previous installation...

if exist node_modules (
    echo    Removing node_modules...
    rmdir /s /q node_modules 2>nul
)
if exist package-lock.json del package-lock.json 2>nul

echo    Removing conda environment...
call conda env remove -n fuel-extractor -y 2>nul

echo.
echo  Starting fresh installation...
goto full_install

:python_install
cls
echo.
echo  ============================================================
echo     Python Package Installation
echo  ============================================================
echo.

where conda >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo  [ERROR] Conda is not installed
    pause
    goto setup_menu
)

echo  Creating/updating conda environment...
call conda create -n fuel-extractor python=3.11 -y 2>nul

echo.
echo  Installing Python packages...
call conda run -n fuel-extractor pip install -r requirements.txt
if %ERRORLEVEL% neq 0 (
    echo  [ERROR] Installation failed
) else (
    echo.
    echo  Python packages installed successfully!
)
pause
goto setup_menu

:node_install_only
cls
echo.
echo  ============================================================
echo     Node.js Package Installation
echo  ============================================================
echo.

where node >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo  [ERROR] Node.js is not installed
    echo  Please install from: https://nodejs.org/
    pause
    goto setup_menu
)

echo  Installing Node.js packages and Chromium...
echo  (This may take several minutes)
echo.
set PUPPETEER_SKIP_CHROMIUM_DOWNLOAD=false
call npm install
if %ERRORLEVEL% neq 0 (
    echo.
    echo  [ERROR] Installation failed
    echo.
    echo  Try running: npm cache clean --force
    echo  Then run this option again
) else (
    echo.
    echo  Node.js packages installed successfully!
)
pause
goto setup_menu

:run_app
call conda activate fuel-extractor
python cli.py
pause
goto end

:show_help
echo.
echo  ============================================================
echo     WhatsApp Fuel Extractor - Help
echo  ============================================================
echo.
echo  Usage: run_fuel_extractor [options] [command]
echo.
echo  Options:
echo    --setup     Open the setup menu
echo    --clean     Clean install (remove and reinstall everything)
echo    --help, -h  Show this help message
echo.
echo  Commands (passed to Python CLI):
echo    listen      Start WhatsApp listener
echo    process     Start fuel processor
echo    web         Start web dashboard
echo    status      Show system status
echo    summary     Generate summary reports
echo    reset       Reset all data
echo.
echo  Examples:
echo    run_fuel_extractor              Interactive menu
echo    run_fuel_extractor --setup      Open setup menu
echo    run_fuel_extractor listen       Start listener directly
echo    run_fuel_extractor web          Start web dashboard
echo.
echo  ============================================================
echo.
pause
goto end

:end
endlocal
exit /b 0
