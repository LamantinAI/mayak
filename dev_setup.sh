#!/bin/bash

# Path to the hooks directory in the repository
HOOKS_DIR=".githooks"

echo "Setting up git hooks..."

# Check if the .githooks directory exists
if [ -d "$HOOKS_DIR" ]; then
    # Make all files in the directory executable
    chmod +x "$HOOKS_DIR"/*

    # Configure Git to use this directory for hooks
    git config core.hooksPath "$HOOKS_DIR"

    echo "Success! Git hooks are now active from $HOOKS_DIR."
else
    echo "Error: Directory $HOOKS_DIR not found."
    exit 1
fi

echo "Installing dependencies..."
# **LOGIC_STEP**: --frozen ensures the lockfile is the source of truth on first checkout.
# To intentionally update dependencies, use `make update-deps`.
# No --extra flag: the dev dependency group installs by default, and pyproject declares no extras.
uv sync --frozen

# **LOGIC_STEP**: A plain `cp` was what this did until 2026-08-20. It carried the sample's
# placeholder database password into .env, and validate_runtime() refuses to start on that value
# when APP_DEBUG=false — so a fresh checkout following the README got a container that exits 1.
# The script below copies the file and generates that one value; the guard stays as strict as it
# was, and it no longer fires on someone who has done nothing wrong.
uv run python scripts/create_env_file.py

echo "Dev setup complete!"
