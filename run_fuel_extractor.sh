#!/bin/bash
# ============================================================
#   WhatsApp Fuel Extractor - Unified Launcher
#   All-in-one setup and run script for Linux/Mac
# ============================================================

cd "$(dirname "$0")"

# Colors for better UI
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color
BOLD='\033[1m'

# Source conda
source_conda() {
    if [ -f "$HOME/anaconda3/etc/profile.d/conda.sh" ]; then
        source "$HOME/anaconda3/etc/profile.d/conda.sh"
    elif [ -f "$HOME/miniconda3/etc/profile.d/conda.sh" ]; then
        source "$HOME/miniconda3/etc/profile.d/conda.sh"
    elif [ -f "/opt/conda/etc/profile.d/conda.sh" ]; then
        source "/opt/conda/etc/profile.d/conda.sh"
    fi
}

source_conda

# ============================================================
#   HELPER FUNCTIONS
# ============================================================

check_conda() {
    command -v conda &> /dev/null
}

check_env() {
    conda env list 2>/dev/null | grep -q "fuel-extractor"
}

check_node() {
    command -v node &> /dev/null
}

check_npm_modules() {
    [ -d "node_modules/whatsapp-web.js" ]
}

print_status() {
    echo ""
    echo -e "  ${BOLD}Current Status:${NC}"
    check_conda && echo -e "    ${GREEN}[OK]${NC} Conda installed" || echo -e "    ${RED}[X]${NC}  Conda NOT found"
    check_env && echo -e "    ${GREEN}[OK]${NC} Python environment ready" || echo -e "    ${RED}[X]${NC}  Python environment NOT found"
    check_node && echo -e "    ${GREEN}[OK]${NC} Node.js installed" || echo -e "    ${RED}[X]${NC}  Node.js NOT found"
    [ -d "node_modules/puppeteer" ] && echo -e "    ${GREEN}[OK]${NC} Node modules installed" || echo -e "    ${RED}[X]${NC}  Node modules NOT installed"
}

# ============================================================
#   INSTALLATION ROUTINES
# ============================================================

full_install() {
    clear
    echo ""
    echo -e "  ${BOLD}============================================================${NC}"
    echo -e "  ${CYAN}   Full Installation${NC}"
    echo -e "  ${BOLD}============================================================${NC}"
    echo ""
    
    if ! check_conda; then
        echo -e "  ${RED}[ERROR]${NC} Conda is not installed or not in PATH"
        echo ""
        echo "  Please install Anaconda or Miniconda from:"
        echo "    https://docs.conda.io/en/latest/miniconda.html"
        echo ""
        read -p "  Press Enter to continue..."
        return 1
    fi
    
    echo -e "  ${YELLOW}[1/5]${NC} Creating conda environment 'fuel-extractor'..."
    conda create -n fuel-extractor python=3.11 -y 2>/dev/null || echo "        Environment may already exist, continuing..."
    
    echo ""
    echo -e "  ${YELLOW}[2/5]${NC} Installing Python dependencies..."
    conda run -n fuel-extractor pip install -r requirements.txt --quiet
    if [ $? -ne 0 ]; then
        echo -e "  ${RED}[ERROR]${NC} Failed to install Python dependencies"
        read -p "  Press Enter to continue..."
        return 1
    fi
    
    if ! check_node; then
        echo ""
        echo -e "  ${YELLOW}[WARNING]${NC} Node.js is not installed"
        echo "  Please install Node.js from: https://nodejs.org/"
        echo "  Then run: ./run_fuel_extractor.sh --setup"
        echo ""
    else
        echo ""
        echo -e "  ${YELLOW}[3/5]${NC} Installing Node.js dependencies..."
        echo "        (This may take a few minutes to download Chromium)"
        export PUPPETEER_SKIP_CHROMIUM_DOWNLOAD=false
        npm install --loglevel=error
        if [ $? -ne 0 ]; then
            echo -e "  ${YELLOW}[WARNING]${NC} Node.js installation had issues"
        fi
    fi
    
    echo ""
    echo -e "  ${YELLOW}[4/5]${NC} Creating data directories..."
    mkdir -p data/raw_messages data/processed data/errors data/output data/session
    
    # Create default JSON files
    [ ! -f data/car_last_update.json ] && echo "{}" > data/car_last_update.json
    [ ! -f data/driver_history.json ] && echo "{}" > data/driver_history.json
    [ ! -f data/last_processed.json ] && echo "{}" > data/last_processed.json
    [ ! -f data/car_summary.json ] && echo "{}" > data/car_summary.json
    [ ! -f data/pending_approvals.json ] && echo "[]" > data/pending_approvals.json
    
    # Create .gitkeep files
    touch data/raw_messages/.gitkeep data/processed/.gitkeep data/errors/.gitkeep data/output/.gitkeep
    
    echo ""
    echo -e "  ${YELLOW}[5/5]${NC} Checking configuration..."
    if [ ! -f .env ] && [ -f .env.example ]; then
        cp .env.example .env
        echo "        Created .env from template"
    fi
    
    # Make scripts executable
    chmod +x run_fuel_extractor.sh cli.py 2>/dev/null
    
    echo ""
    echo -e "  ${BOLD}============================================================${NC}"
    echo -e "  ${GREEN}   Installation Complete!${NC}"
    echo -e "  ${BOLD}============================================================${NC}"
    echo ""
    echo "  Next steps:"
    echo "    1. Edit config.json with your WhatsApp group name"
    echo "    2. Start the application to begin"
    echo ""
    
    read -p "  Start the application now? (y/n): " start_now
    if [[ "$start_now" =~ ^[Yy]$ ]]; then
        run_app
    fi
}

update_install() {
    clear
    echo ""
    echo -e "  ${BOLD}============================================================${NC}"
    echo -e "  ${CYAN}   Update Installation${NC}"
    echo -e "  ${BOLD}============================================================${NC}"
    echo ""
    echo "  Updating all dependencies without removing existing data..."
    echo ""
    
    echo -e "  ${YELLOW}[1/2]${NC} Updating Python packages..."
    conda run -n fuel-extractor pip install -r requirements.txt --upgrade --quiet 2>/dev/null
    if [ $? -ne 0 ]; then
        echo -e "  ${YELLOW}[WARNING]${NC} Python update had issues"
    fi
    
    echo ""
    echo -e "  ${YELLOW}[2/2]${NC} Updating Node.js packages..."
    npm update --loglevel=error 2>/dev/null
    if [ $? -ne 0 ]; then
        echo -e "  ${YELLOW}[WARNING]${NC} Node.js update had issues"
    fi
    
    echo ""
    echo -e "  ${GREEN}Update complete!${NC}"
    echo ""
    read -p "  Press Enter to continue..."
}

clean_install() {
    clear
    echo ""
    echo -e "  ${BOLD}============================================================${NC}"
    echo -e "  ${RED}   Clean Installation${NC}"
    echo -e "  ${BOLD}============================================================${NC}"
    echo ""
    echo -e "  ${YELLOW}WARNING:${NC} This will remove:"
    echo "    - node_modules folder"
    echo "    - package-lock.json"
    echo "    - Conda environment (will be recreated)"
    echo ""
    echo "  Your data and configuration will be preserved."
    echo ""
    
    read -p "  Are you sure? (y/n): " confirm
    if [[ ! "$confirm" =~ ^[Yy]$ ]]; then
        return
    fi
    
    echo ""
    echo "  Cleaning previous installation..."
    
    if [ -d "node_modules" ]; then
        echo "    Removing node_modules..."
        rm -rf node_modules
    fi
    rm -f package-lock.json
    
    echo "    Removing conda environment..."
    conda env remove -n fuel-extractor -y 2>/dev/null
    
    echo ""
    echo "  Starting fresh installation..."
    sleep 1
    full_install
}

python_install() {
    clear
    echo ""
    echo -e "  ${BOLD}============================================================${NC}"
    echo -e "  ${CYAN}   Python Package Installation${NC}"
    echo -e "  ${BOLD}============================================================${NC}"
    echo ""
    
    if ! check_conda; then
        echo -e "  ${RED}[ERROR]${NC} Conda is not installed"
        read -p "  Press Enter to continue..."
        return
    fi
    
    echo "  Creating/updating conda environment..."
    conda create -n fuel-extractor python=3.11 -y 2>/dev/null
    
    echo ""
    echo "  Installing Python packages..."
    conda run -n fuel-extractor pip install -r requirements.txt
    if [ $? -ne 0 ]; then
        echo -e "\n  ${RED}[ERROR]${NC} Installation failed"
    else
        echo -e "\n  ${GREEN}Python packages installed successfully!${NC}"
    fi
    read -p "  Press Enter to continue..."
}

node_install() {
    clear
    echo ""
    echo -e "  ${BOLD}============================================================${NC}"
    echo -e "  ${CYAN}   Node.js Package Installation${NC}"
    echo -e "  ${BOLD}============================================================${NC}"
    echo ""
    
    if ! check_node; then
        echo -e "  ${RED}[ERROR]${NC} Node.js is not installed"
        echo "  Please install from: https://nodejs.org/"
        read -p "  Press Enter to continue..."
        return
    fi
    
    echo "  Installing Node.js packages and Chromium..."
    echo "  (This may take several minutes)"
    echo ""
    export PUPPETEER_SKIP_CHROMIUM_DOWNLOAD=false
    npm install
    if [ $? -ne 0 ]; then
        echo ""
        echo -e "  ${RED}[ERROR]${NC} Installation failed"
        echo ""
        echo "  Try running: npm cache clean --force"
        echo "  Then run this option again"
    else
        echo ""
        echo -e "  ${GREEN}Node.js packages installed successfully!${NC}"
    fi
    read -p "  Press Enter to continue..."
}

run_app() {
    conda activate fuel-extractor
    python cli.py "$@"
}

# ============================================================
#   SETUP MENU
# ============================================================

setup_menu() {
    while true; do
        clear
        echo ""
        echo -e "  ${BOLD}============================================================${NC}"
        echo -e "  ${CYAN}   WhatsApp Fuel Extractor - Setup Menu${NC}"
        echo -e "  ${BOLD}============================================================${NC}"
        
        print_status
        
        echo ""
        echo "  ------------------------------------------------------------"
        echo ""
        echo "     [1] Full Install (first time setup)"
        echo "     [2] Update Only (reinstall dependencies)"
        echo "     [3] Clean Install (remove everything and reinstall)"
        echo "     [4] Install Python packages only"
        echo "     [5] Install Node.js packages only"
        echo ""
        echo "     [R] Run Application"
        echo "     [Q] Quit"
        echo ""
        echo -e "  ${BOLD}============================================================${NC}"
        echo ""
        
        read -p "  Select option: " setup_choice
        
        case "$setup_choice" in
            1) full_install ;;
            2) update_install ;;
            3) clean_install ;;
            4) python_install ;;
            5) node_install ;;
            [rR]) run_app; return ;;
            [qQ]) exit 0 ;;
            *) echo -e "\n  Invalid option."; sleep 1 ;;
        esac
    done
}

show_help() {
    echo ""
    echo -e "  ${BOLD}============================================================${NC}"
    echo -e "  ${CYAN}   WhatsApp Fuel Extractor - Help${NC}"
    echo -e "  ${BOLD}============================================================${NC}"
    echo ""
    echo "  Usage: ./run_fuel_extractor.sh [options] [command]"
    echo ""
    echo "  Options:"
    echo "    --setup     Open the setup menu"
    echo "    --clean     Clean install (remove and reinstall everything)"
    echo "    --help, -h  Show this help message"
    echo ""
    echo "  Commands (passed to Python CLI):"
    echo "    listen      Start WhatsApp listener"
    echo "    process     Start fuel processor"
    echo "    web         Start web dashboard"
    echo "    status      Show system status"
    echo "    summary     Generate summary reports"
    echo "    reset       Reset all data"
    echo ""
    echo "  Examples:"
    echo "    ./run_fuel_extractor.sh              Interactive menu"
    echo "    ./run_fuel_extractor.sh --setup      Open setup menu"
    echo "    ./run_fuel_extractor.sh listen       Start listener directly"
    echo "    ./run_fuel_extractor.sh web          Start web dashboard"
    echo ""
    echo -e "  ${BOLD}============================================================${NC}"
    echo ""
}

# ============================================================
#   MAIN ENTRY POINT
# ============================================================

# Handle command line arguments
case "$1" in
    --setup)
        setup_menu
        exit 0
        ;;
    --clean)
        clean_install
        exit 0
        ;;
    --help|-h)
        show_help
        exit 0
        ;;
esac

# Check if conda is available
if ! check_conda; then
    echo ""
    echo -e "  ${BOLD}============================================================${NC}"
    echo -e "  ${CYAN}   WhatsApp Fuel Extractor${NC}"
    echo -e "  ${BOLD}============================================================${NC}"
    echo ""
    echo -e "  ${RED}[ERROR]${NC} Conda is not installed or not in PATH"
    echo ""
    echo "  Please install Anaconda or Miniconda from:"
    echo "    https://docs.conda.io/en/latest/miniconda.html"
    echo ""
    echo "  After installation, restart this script."
    echo ""
    exit 1
fi

# Check if environment exists
if ! check_env; then
    echo ""
    echo -e "  ${BOLD}============================================================${NC}"
    echo -e "  ${CYAN}   WhatsApp Fuel Extractor - First Time Setup${NC}"
    echo -e "  ${BOLD}============================================================${NC}"
    echo ""
    echo "  Python environment 'fuel-extractor' not found."
    echo ""
    read -p "  Would you like to set it up now? (y/n): " do_setup
    if [[ "$do_setup" =~ ^[Yy]$ ]]; then
        full_install
        exit 0
    else
        echo ""
        echo "  Setup cancelled. Run this script again when ready."
        echo ""
        exit 0
    fi
fi

# Check if Node modules are installed
if ! check_npm_modules; then
    echo ""
    echo -e "  ${BOLD}============================================================${NC}"
    echo -e "  ${CYAN}   WhatsApp Fuel Extractor - Node.js Setup Required${NC}"
    echo -e "  ${BOLD}============================================================${NC}"
    echo ""
    echo "  Node.js dependencies are not installed."
    echo ""
    
    if ! check_node; then
        echo -e "  ${YELLOW}[WARNING]${NC} Node.js is not installed."
        echo "  Please install from: https://nodejs.org/"
        echo ""
        echo "  You can still use some features without Node.js."
        echo ""
        read -p "  Press Enter to continue..."
    else
        read -p "  Would you like to install them now? (y/n): " do_npm
        if [[ "$do_npm" =~ ^[Yy]$ ]]; then
            node_install
        fi
    fi
fi

# All set - run the interactive CLI
run_app "$@"
