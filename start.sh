#!/bin/bash
# WhatsApp Fuel Extractor - Start Script (Evolution API Version)

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "╔═══════════════════════════════════════════════════════════════╗"
echo "║       WhatsApp Fuel Extractor - Evolution API Version         ║"
echo "╚═══════════════════════════════════════════════════════════════╝"

# Check for .env file
if [ ! -f ".env" ]; then
    echo ""
    echo "⚠️  No .env file found!"
    echo "Please create a .env file with your database and Evolution API settings."
    echo "See .env.example for required variables."
    exit 1
fi

# Check Python
if ! command -v python3 &> /dev/null; then
    echo "❌ Python 3 not found!"
    exit 1
fi

echo ""
echo "Python: $(python3 --version)"

# Check if in virtual environment
if [ -z "$VIRTUAL_ENV" ]; then
    # Check for venv directory
    if [ -d "venv" ]; then
        echo "Activating virtual environment..."
        source venv/bin/activate
    elif [ -d ".venv" ]; then
        echo "Activating virtual environment..."
        source .venv/bin/activate
    else
        echo "⚠️  No virtual environment found"
        echo "   Consider: python3 -m venv venv && source venv/bin/activate"
    fi
fi

# Install/update dependencies
echo ""
echo "Checking dependencies..."
pip install -q -r requirements.txt

# Run startup script
echo ""
python3 -m python.startup
