#!/bin/bash
# Pulls the latest committed changes from GitHub into this installation and
# restarts the running instance (if any). Safe to run any time; run.py/venv
# and config.yaml are never touched by git so your local settings survive.
#
# Usage: ./update.sh   (from anywhere, or via the installed `whisperwriter-update` alias)
set -euo pipefail

main() {
    local install_dir
    install_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    cd "$install_dir"

    if [ ! -d .git ]; then
        echo "Error: $install_dir is not a git checkout." >&2
        exit 1
    fi

    echo "WhisperWriter update — installation: $install_dir"

    local branch
    branch="$(git rev-parse --abbrev-ref HEAD)"

    if [ -n "$(git status --porcelain)" ]; then
        echo "Error: local changes present, refusing to update. Commit or stash them first:" >&2
        git status --short
        exit 1
    fi

    echo "Fetching origin/$branch..."
    git fetch origin "$branch"

    local old_head new_head
    old_head="$(git rev-parse HEAD)"
    new_head="$(git rev-parse "origin/$branch")"

    if [ "$old_head" = "$new_head" ]; then
        echo "Already up to date (${old_head:0:7})."
        exit 0
    fi

    echo "Updating $branch: ${old_head:0:7} -> ${new_head:0:7}"
    git log --oneline "$old_head..$new_head"

    if ! git merge --ff-only "origin/$branch"; then
        echo "Error: local $branch has diverged from origin/$branch and can't fast-forward." >&2
        echo "Resolve manually (git status / git log), then re-run." >&2
        exit 1
    fi

    if ! git diff --quiet "$old_head" "$new_head" -- requirements.txt; then
        echo "requirements.txt changed — updating venv..."
        venv/bin/pip install -q --upgrade pip
        venv/bin/pip install -q -r requirements.txt
    fi

    echo "Update complete."

    local pids
    pids="$(pgrep -f "$install_dir/venv/bin/python3 src/main.py" || true)"

    if [ -z "$pids" ]; then
        echo "No running instance found — nothing to restart."
        exit 0
    fi

    echo "Restarting running instance (pid(s): $pids)..."
    for pid in $pids; do
        kill "$pid" 2>/dev/null || true
    done
    for _ in $(seq 1 20); do
        pids="$(pgrep -f "$install_dir/venv/bin/python3 src/main.py" || true)"
        [ -z "$pids" ] && break
        sleep 0.5
    done
    if [ -n "$pids" ]; then
        for pid in $pids; do
            kill -9 "$pid" 2>/dev/null || true
        done
    fi

    nohup "$install_dir/start.sh" >/tmp/whisper-writer-update-restart.log 2>&1 &
    disown
    echo "Restarted (log: /tmp/whisper-writer-update-restart.log)."
}

main "$@"
