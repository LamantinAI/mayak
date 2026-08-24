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

# **LOGIC_STEP**: .claude/skills/* ships as symlinks into .agents/skills/ (`git ls-files -s
# .claude/skills` shows mode 120000). A Windows checkout without `core.symlinks` enabled — the
# default for a non-admin user — materialises each of those as a plain text file containing the
# literal target path, not the skill content. The two skills here are the entire difference
# between an agent reading the one worked example and an agent following the add-vertical
# procedure, so a silently missing skill is worse than almost any other silent failure in this
# template: the agent has no file to open, prints no error, and just proceeds as if the skill
# did not exist.
# A loud warning here is the only point in setup that can catch it, so this does not exit
# non-zero — the rest of setup still runs, and a macOS or Linux checkout (where the symlinks
# check out correctly) must see nothing change.
for skill_path in .claude/skills/*; do
    # Glob with no match leaves the literal pattern as the one entry; skip it rather than warn
    # about a path that was never a real skill.
    [ -e "$skill_path" ] || continue
    if [ ! -L "$skill_path" ]; then
        echo ""
        echo "WARNING: $skill_path is a regular file, not a symlink."
        echo "  git checked this out as a plain text file containing a path, not the skill it"
        echo "  points to (.agents/skills/...). This happens on Windows when core.symlinks is"
        echo "  off, which is the default for a non-admin user. Claude Code will find no skill"
        echo "  here and will not tell you — it just runs without the procedure this skill"
        echo "  carries."
        echo "  Fix: git config core.symlinks true && git checkout -- .claude/skills"
        echo "  On Windows this also needs Developer Mode enabled, or an elevated shell, before"
        echo "  git will create real symlinks."
        echo ""
    fi
done

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
