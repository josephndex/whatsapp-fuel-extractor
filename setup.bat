@echo off
REM WhatsApp Fuel Extractor - Setup Script for Windows
REM This script sets up the environment and installs all dependencies

cd /d "%~dp0"

echo.
echo ============================================================
echo   WhatsApp Fuel Extractor - Setup
echo ============================================================
echo.

REM Check if conda is available
where conda >nul 2>nul
if %ERRORLEVEL% neq 0 (
    echo [ERROR] Conda is not installed or not in PATH
    echo Please install Anaconda or Miniconda from:
    echo   https://docs.conda.io/en/latest/miniconda.html
    echo.
    pause
    exit /b 1
)

echo [1/4] Creating conda environment 'fuel-extractor'...
echo.
call conda create -n fuel-extractor python=3.11 -y
if %ERRORLEVEL% neq 0 (
    echo [WARNING] Environment may already exist, continuing...
)

echo.
echo [2/4] Installing Python dependencies...
echo.
call conda run -n fuel-extractor pip install -r requirements.txt
if %ERRORLEVEL% neq 0 (
    echo [ERROR] Failed to install Python dependencies
    pause
    exit /b 1
)

REM Check if Node.js is available
where node >nul 2>nul
if %ERRORLEVEL% neq 0 (
    echo.
    echo [WARNING] Node.js is not installed or not in PATH
    echo Please install Node.js from: https://nodejs.org/
    echo Skipping Node.js dependencies...
    goto :skip_node
)

echo.
echo [3/4] Installing Node.js dependencies...
echo.
call npm install
if %ERRORLEVEL% neq 0 (
    echo [ERROR] Failed to install Node.js dependencies
    pause
    exit /b 1
)

:skip_node

echo.
echo [4/4] Creating data directories...
echo.
if not exist data\raw_messages mkdir data\raw_messages
if not exist data\processed mkdir data\processed
if not exist data\errors mkdir data\errors
if not exist data\output mkdir data\output
if not exist data\session mkdir data\session

REM Create default JSON files if they don't exist
if not exist data\car_last_update.json echo {} > data\car_last_update.json
if not exist data\driver_history.json echo {} > data\driver_history.json
if not exist data\last_processed.json echo {} > data\last_processed.json
if not exist data\car_summary.json echo {} > data\car_summary.json
if not exist data\pending_approvals.json echo [] > data\pending_approvals.json

REM Check for .env file
if not exist .env (
    if exist .env.example (
        echo.
        echo [NOTE] Copying .env.example to .env
        copy .env.example .env
        echo Please edit .env with your database and Google Sheets credentials
    ) else (
        echo.
        echo [NOTE] No .env file found. Create one if you need DB/Sheets integration.
    )
)

echo.
echo ============================================================
echo   Setup Complete!
echo ============================================================
echo.
echo Next steps:
echo   1. Edit config.json with your WhatsApp group name
echo   2. Edit .env with database/Google Sheets credentials (optional)
echo   3. Run: fuel.bat listen   (scan QR code)
echo   4. Run: fuel.bat process  (in another window)
echo.
echo For help: fuel.bat --help
echo.
pause
