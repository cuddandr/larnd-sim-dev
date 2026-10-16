#!/usr/bin/env bash

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
export EXIT_CODE=1
if [[ ! -f "${SETUP_SCRIPT}" ]]; then
    echo "Error: Setup script '$SETUP_SCRIPT' not found."

    if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
        # Script is being run (subshell)
        exit "${EXIT_CODE}"
    else
        # Script is being sourced
        return "${EXIT_CODE}"
    fi
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
    echo "-------------------------------------------------"
    pip install --upgrade pip setuptools wheel
    pip install matplotlib
else
    echo "Using existing virtual environment: $venv_name"
    source "$venv_name/bin/activate"
fi

# Repository Management
REPO_DIR="larnd-sim"
if [[ ! -d "${REPO_DIR}" ]]; then
    echo "-------------------------------------------------"
    echo "Cloning larnd-sim repository..."

    # Determine whether to use SSH or HTTPS
    if ssh -T -o BatchMode=yes git@github.com 2>&1 | grep -q "successfully authenticated"; then
        echo "SSH authentication successful. Using SSH URL."
        CLONE_URL="git@github.com:DUNE/larnd-sim.git"
    else
        echo "SSH authentication failed or not configured. Using HTTPS URL."
        CLONE_URL="https://github.com/DUNE/larnd-sim"
    fi

    echo "Checking out ${GIT_BRANCH}"
    if [[ -n ${GIT_DEPTH} ]]; then
        echo "...at depth ${GIT_DEPTH}"
    fi
    git clone -b ${GIT_BRANCH} ${GIT_DEPTH} "${CLONE_URL}" "${REPO_DIR}"
else
    echo "Using existing repository in ${REPO_DIR}"
fi

# Editable Installation
echo "-------------------------------------------------"
echo "Checking if larndsim is already installed..."
if pip show larndsim > /dev/null 2>&1; then
    echo "larndsim is already installed. Skipping installation."
else
    echo "Performing editable install of larnd-sim..."
    pushd "${REPO_DIR}"
    pip install -e .[runner]
    popd
fi

echo "---------------------------------------------------------------"
echo "Setup complete."

if [ "${ACTIVATE}" = true ]; then
    echo "Activating environment: $venv_name"
    source "$venv_name/bin/activate"
else
    echo "To activate the environment in your current shell, run: source $venv_name/bin/activate"
fi
echo "---------------------------------------------------------------"
