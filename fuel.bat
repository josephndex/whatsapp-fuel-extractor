@echo off
REM WhatsApp Fuel Extractor - CLI Wrapper
REM Usage: fuel <command> [options]

cd /d "%~dp0"

REM Run CLI with all passed arguments
call conda run -n fuel-extractor python cli.py %*
