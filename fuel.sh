#!/bin/bash
# WhatsApp Fuel Extractor - CLI Wrapper
# Usage: ./fuel.sh <command> [options]

cd "$(dirname "$0")"

# Source conda if available
if [ -f "$HOME/anaconda3/etc/profile.d/conda.sh" ]; then
    source "$HOME/anaconda3/etc/profile.d/conda.sh"
elif [ -f "$HOME/miniconda3/etc/profile.d/conda.sh" ]; then
    source "$HOME/miniconda3/etc/profile.d/conda.sh"
fi

# Run CLI with all passed arguments
if command -v conda &> /dev/null; then
    conda run -n fuel-extractor python cli.py "$@"
else
    python3 cli.py "$@"
fi
