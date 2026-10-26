#!/bin/sh
#
# Point this clone's hooks at scripts/hooks/, so the component-SVG pre-commit
# check runs on every commit.
#
# Uses core.hooksPath rather than copying files into .git/hooks, so the hooks
# stay version-controlled and an update arrives with a normal pull instead of
# needing a re-install.
#
# Install:   scripts/install_git_hooks.sh
# Uninstall: git config --unset core.hooksPath

set -eu

REPO=$(git rev-parse --show-toplevel)
cd "$REPO"

if [ ! -d scripts/hooks ]; then
    echo "scripts/hooks/ not found — run this from inside the repo" >&2
    exit 1
fi

chmod +x scripts/hooks/* 2>/dev/null || true

EXISTING=$(git config --local --get core.hooksPath || true)
if [ -n "$EXISTING" ] && [ "$EXISTING" != "scripts/hooks" ]; then
    echo "core.hooksPath is already set to '$EXISTING'." >&2
    echo "Leaving it alone — merge the hooks by hand, or unset it first." >&2
    exit 1
fi

# Warn about hooks already in .git/hooks: core.hooksPath replaces that
# directory wholesale, so anything live in there silently stops running.
if [ -d .git/hooks ]; then
    SHADOWED=$(find .git/hooks -maxdepth 1 -type f ! -name '*.sample' 2>/dev/null || true)
    if [ -n "$SHADOWED" ]; then
        echo "note: these existing hooks will stop running once core.hooksPath"
        echo "      is set, because it replaces .git/hooks entirely:"
        printf '        %s\n' $SHADOWED
        echo "      Move them into scripts/hooks/ to keep them active."
        echo
    fi
fi

git config --local core.hooksPath scripts/hooks

echo "Installed: core.hooksPath -> scripts/hooks"
echo
echo "On each commit, staged SVGs under src/diagrammer/components/ are"
echo "normalized (editor mangling repaired in place and re-staged), then"
echo "validated against the app's parser. Errors block the commit."
echo
echo "The validator needs an interpreter with PySide6. If yours is not at"
echo "~/miniforge3/envs/CMT/bin/python, set it once:"
echo "    git config --local hooks.python /path/to/python   # (informational)"
echo "    export DIAGRAMMER_PYTHON=/path/to/python          # (what the hook reads)"
echo
echo "Bypass a single commit with: git commit --no-verify"
