#!/bin/bash
# Pulls the latest committed changes from GitHub into this installation and
# restarts the running instance (if any) after every successful update. Finish
# dictation before updating.
# Gitignored venv/ and config.yaml survive; dependencies are reconciled by pip.
#
# Usage: ./update.sh   (from anywhere, or via the installed `whisperwriter-update` alias)
#        ./update.sh --check-only   (exit 0=current, 10=update available)
#        ./update.sh --no-restart   (tray-supervised install; caller owns the mandatory restart)
set -euo pipefail

main() {
    local check_only=0 restart=1
    case "${1:-}" in
        "") ;;
        --check-only) check_only=1 ;;
        --no-restart) restart=0 ;;
        *)
            echo "Usage: $0 [--check-only|--no-restart]" >&2
            exit 2
            ;;
    esac

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
    if [ "$branch" = "HEAD" ]; then
        echo "Error: checkout is in detached HEAD state; cannot select an update branch." >&2
        exit 1
    fi

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
        echo "NO_UPDATE"
        if [ "$check_only" -eq 1 ]; then
            exit 0
        fi
        echo "Already up to date (${old_head:0:7})."
    elif git merge-base --is-ancestor "$old_head" "$new_head"; then
        echo "UPDATE_AVAILABLE"
        if [ "$check_only" -eq 1 ]; then
            exit 10
        fi

        echo "Updating $branch: ${old_head:0:7} -> ${new_head:0:7}"
        git log --oneline "$old_head..$new_head"

        if ! git merge --ff-only "origin/$branch"; then
            echo "Error: local $branch has diverged from origin/$branch and can't fast-forward." >&2
            echo "Resolve manually (git status / git log), then re-run." >&2
            exit 1
        fi
    else
        echo "Error: local $branch is ahead of or diverged from origin/$branch; refusing to update." >&2
        exit 1
    fi

    # Always reconcile the venv against requirements.txt, not just when it textually
    # changed in this pull — a venv can drift out of sync with a committed
    # requirements.txt for reasons this script can't see (a manual `git pull`/rebase
    # done outside update.sh, a previous run interrupted mid-install, a venv rebuilt
    # from an older checkout, etc). pip is idempotent and fast when nothing is missing,
    # so the safety net costs a couple of seconds even on a no-op update.
    echo "Reconciling venv against requirements.txt..."
    venv/bin/python3 -m pip install -q -r requirements.txt
    venv/bin/python3 -m pip check

    echo "Update complete."
    # The tray owns restart when it supervises this process, and reports failures.
    if [ "$restart" -eq 0 ]; then
        return
    fi

    local pids pid
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

    local log_dir="${XDG_CACHE_HOME:-$HOME/.cache}/whisper-writer"
    mkdir -p "$log_dir"
    chmod 700 "$log_dir"
    nohup "$install_dir/start.sh" >"$log_dir/restart.log" 2>&1 &
    disown
    echo "Restart launched (log: $log_dir/restart.log)."
}

main "$@"
