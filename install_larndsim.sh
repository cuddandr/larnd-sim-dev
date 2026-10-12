#!/usr/bin/env bash

# Stop on any error
set -e

# Usage function
usage() {
    echo "Usage: $0 [options]"
    echo ""
    echo "Options:"
    echo "  -s path     Path to the setup script (default: ./setup.sh)"
    echo "  -b branch   Git branch to clone (default: develop)"
    echo "  -d depth    Git clone depth (e.g., 1 for shallow clone)"
    echo "  -a          Activate the environment at the end"
    echo "  -h          Show this help message"
}

# Default values
SETUP_SCRIPT="./setup.sh"
GIT_BRANCH="develop"
GIT_DEPTH=""
ACTIVATE=false

# Parse arguments using getopts
# -s: setup script path
# -b: git branch
# -d: git depth
# -a: activate environment at the end
# -h: help
while getopts "s:b:d:ah" opt; do
    case "$opt" in
        s)
            SETUP_SCRIPT="$OPTARG"
            ;;
        b)
            GIT_BRANCH="$OPTARG"
            ;;
        d)
            GIT_DEPTH="--depth $OPTARG"
            ;;
        a)
            ACTIVATE=true
            ;;
        h)
            usage
            exit 0
            ;;
        *)
            usage
            exit 1
            ;;
    esac
done

echo "Running setup/install for larnd-sim..."

# Check if setup script exists
if [[ ! -f "${SETUP_SCRIPT}" ]]; then
    echo "Error: Setup script '$SETUP_SCRIPT' not found."
    exit 1
fi

# Source the setup script to set the 'venv_name' variable
source "${SETUP_SCRIPT}"

echo "Using virtual environment: $venv_name"

# Virtual Environment Management
if [[ ! -d "$venv_name" ]]; then
    echo "Creating new virtual environment: $venv_name"
    python3 -m venv "$venv_name"

    # Activate for subsequent commands in this script
    source "$venv_name/bin/activate"

    echo "Upgrading pip and installing base dependencies..."
    pip install --upgrade pip setuptools wheel
    pip install matplotlib
else
    echo "Using existing virtual environment: $venv_name"
    source "$venv_name/bin/activate"
fi

# Repository Management
REPO_DIR="larnd-sim"
if [[ ! -d "${REPO_DIR}" ]]; then
    echo "Cloning larnd-sim repository..."
    git clone -b ${GIT_BRANCH} ${GIT_DEPTH} https://github.com/DUNE/larnd-sim ${REPO_DIR}
else
    echo "Using existing repository in ${REPO_DIR}"
fi

# Editable Installation
echo "Checking if larndsim is already installed..."
if pip show larndsim > /dev/null 2>&1; then
    echo "larndsim is already installed. Skipping installation."
else
    echo "Performing editable install of larnd-sim..."
    cd "${REPO_DIR}"
    pip install -e .[runner]
    cd ..
fi

echo "--------------------------------------------------------------------------------"
echo "Setup complete."

if [ "${ACTIVATE}" = true ]; then
    echo "Activating environment: $venv_name"
    source "$venv_name/bin/activate"
    echo "--------------------------------------------------------------------------------"
    echo "NOTE: If you ran this script as './install_larndsim.sh', the activation only applied"
    echo "to the script's subshell. To activate in your current shell, run:"
    echo "  source $venv_name/bin/activate"
    echo "  OR run this script as: source install_larndsim.sh -a"
    echo "--------------------------------------------------------------------------------"
else
    echo "To activate the environment in your current shell, run: source $venv_name/bin/activate"
fi
