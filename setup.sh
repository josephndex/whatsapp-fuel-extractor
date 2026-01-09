#!/bin/bash
# WhatsApp Fuel Extractor - Setup Script for Linux/Mac
# This script sets up the environment and installs all dependencies

cd "$(dirname "$0")"

echo ""
echo "============================================================"
echo "  WhatsApp Fuel Extractor - Setup"
echo "============================================================"
echo ""

# Check if conda is available
if ! command -v conda &> /dev/null; then
    echo "[ERROR] Conda is not installed or not in PATH"
    echo "Please install Anaconda or Miniconda from:"
    echo "  https://docs.conda.io/en/latest/miniconda.html"
    echo ""
    exit 1
fi

# Source conda
if [ -f "$HOME/anaconda3/etc/profile.d/conda.sh" ]; then
    source "$HOME/anaconda3/etc/profile.d/conda.sh"
elif [ -f "$HOME/miniconda3/etc/profile.d/conda.sh" ]; then
    source "$HOME/miniconda3/etc/profile.d/conda.sh"
fi

echo "[1/4] Creating conda environment 'fuel-extractor'..."
echo ""
conda create -n fuel-extractor python=3.11 -y 2>/dev/null || echo "[WARNING] Environment may already exist, continuing..."

echo ""
echo "[2/4] Installing Python dependencies..."
echo ""
conda run -n fuel-extractor pip install -r requirements.txt
if [ $? -ne 0 ]; then
    echo "[ERROR] Failed to install Python dependencies"
    exit 1
fi

# Check if Node.js is available
if ! command -v node &> /dev/null; then
    echo ""
    echo "[WARNING] Node.js is not installed or not in PATH"
    echo "Please install Node.js from: https://nodejs.org/"
    echo "Skipping Node.js dependencies..."
else
    echo ""
    echo "[3/4] Installing Node.js dependencies..."
    echo ""
    npm install
    if [ $? -ne 0 ]; then
        echo "[ERROR] Failed to install Node.js dependencies"
        exit 1
    fi
fi

echo ""
echo "[4/4] Creating data directories..."
echo ""
mkdir -p data/raw_messages data/processed data/errors data/output data/session

# Create default JSON files if they don't exist
[ ! -f data/car_last_update.json ] && echo "{}" > data/car_last_update.json
[ ! -f data/driver_history.json ] && echo "{}" > data/driver_history.json
[ ! -f data/last_processed.json ] && echo "{}" > data/last_processed.json
[ ! -f data/car_summary.json ] && echo "{}" > data/car_summary.json
[ ! -f data/pending_approvals.json ] && echo "[]" > data/pending_approvals.json

# Create .gitkeep files
touch data/raw_messages/.gitkeep
touch data/processed/.gitkeep
touch data/errors/.gitkeep
touch data/output/.gitkeep
touch data/session/.gitkeep

# Check for .env file
if [ ! -f .env ]; then
    if [ -f .env.example ]; then
        echo ""
        echo "[NOTE] Copying .env.example to .env"
        cp .env.example .env
        echo "Please edit .env with your database and Google Sheets credentials"
    else
        echo ""
        echo "[NOTE] No .env file found. Create one if you need DB/Sheets integration."
    fi
fi

# Make scripts executable
chmod +x fuel fuel.sh cli.py 2>/dev/null

echo ""
echo "============================================================"
echo "  ✅ Setup Complete!"
echo "============================================================"
echo ""
echo "Next steps:"
echo "  1. Edit config.json with your WhatsApp group name"
echo "  2. Edit .env with database/Google Sheets credentials (optional)"
echo "  3. Run: ./fuel.sh listen   (scan QR code)"
echo "  4. Run: ./fuel.sh process  (in another terminal)"
echo ""
echo "For help: ./fuel.sh --help"
echo ""
