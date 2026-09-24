#!/bin/bash
# Pulls the latest committed changes from GitHub into this installation and
# restarts the running instance (if any) after every successful update. Finish
# dictation before updating.
# Gitignored venv/ and config.yaml survive; dependencies are reconciled by pip.
#
# Usage: ./update.sh                    update the current branch from origin
#        ./update.sh --check-only       exit 0=current, 10=update available,
#                                       11=the branch no longer exists on origin
#        ./update.sh --no-restart       tray-supervised install; caller owns the mandatory restart
#        ./update.sh --switch BRANCH    switch this installation to origin/BRANCH, e.g. to test
#                                       a feature branch before it is merged (main to go back)
#        ./update.sh --list-branches    print the branches available on origin, one per line
#        ./update.sh --rebuild-venv     recreate venv/ from scratch (keeps the old one as
#                                       venv.previous/ until the new one works)
# --no-restart can be combined with --switch and --rebuild-venv.
set -euo pipefail

EXIT_UPDATE_AVAILABLE=10
EXIT_BRANCH_GONE=11

usage() {
    echo "Usage: $0 [--check-only | --list-branches | [--switch BRANCH] [--rebuild-venv] [--no-restart]]" >&2
    exit 2
}

# Fetch one branch into refs/remotes/origin/<branch>. The explicit refspec also works in
# single-branch clones whose configured refspec only covers the branch they were cloned with.
fetch_branch() {
    local branch="$1"
    echo "Fetching origin/$branch..."
    if git fetch --quiet origin "+refs/heads/$branch:refs/remotes/origin/$branch"; then
        return 0
    fi
    local status=0
    git ls-remote --exit-code --heads origin "refs/heads/$branch" >/dev/null 2>&1 || status=$?
    if [ "$status" -eq 2 ]; then
        echo "BRANCH_GONE"
        echo "Error: branch '$branch' does not exist on origin (merged and deleted?)." >&2
        echo "Switch to another branch, e.g.: $0 --switch main" >&2
        exit "$EXIT_BRANCH_GONE"
    fi
    echo "Error: could not fetch origin/$branch. Check the network connection." >&2
    exit 1
}

# Print the first commit reachable from $1 that was never published, i.e. work that exists
# only in this checkout. Published means contained in a current origin branch or in any
# earlier position of origin/$2 (its reflog): after a force-push the branch no longer
# contains the commits this checkout was installed from, but they were not local work.
unpublished_commit() {
    local ref="$1" branch="$2" previous
    previous="$(git reflog show --format=%H "refs/remotes/origin/$branch" -- 2>/dev/null || true)"
    # shellcheck disable=SC2086  # one commit id per word
    git rev-list --max-count=1 "$ref" --not --remotes=origin $previous 2>/dev/null || true
}

require_clean_tree() {
    if [ -n "$(git status --porcelain)" ]; then
        echo "Error: local changes present, refusing to update. Commit or stash them first:" >&2
        git status --short
        exit 1
    fi
}

venv_works() {
    [ -x venv/bin/python3 ] && venv/bin/python3 -c 'import sys; sys.exit(sys.version_info < (3, 10))' >/dev/null 2>&1
}

install_requirements() {
    local python="$1"
    echo "Reconciling $(dirname "$(dirname "$python")") against requirements.txt..."
    "$python" -m pip install -q -r requirements.txt && "$python" -m pip check
}

# Recreate venv/ with the system python3 (or $WHISPER_WRITER_PYTHON). The previous venv is
# moved aside and restored if the new one cannot be built, so a failed rebuild (missing apt
# packages, no network) leaves the venv as it was. Returns non-zero on failure.
rebuild_venv() {
    local python="${WHISPER_WRITER_PYTHON:-python3}"
    if ! "$python" -c 'import sys; sys.exit(not ((3, 10) <= sys.version_info[:2] <= (3, 14)))' 2>/dev/null; then
        echo "Error: $python is missing or not Python 3.10-3.14; set WHISPER_WRITER_PYTHON." >&2
        return 1
    fi
    echo "Rebuilding venv/ with $("$python" --version 2>&1)..."
    rm -rf venv.previous
    if [ -e venv ]; then
        mv venv venv.previous
    fi
    if "$python" -m venv venv && install_requirements venv/bin/python3; then
        echo "New venv is ready; the previous one is kept in venv.previous/ (safe to delete)."
        return 0
    fi
    echo "Error: rebuilding the venv failed; restoring the previous one." >&2
    rm -rf venv
    if [ -e venv.previous ]; then
        mv venv.previous venv
    fi
    return 1
}

# Bring venv/ in line with the checked-out requirements.txt. Returns non-zero on failure.
reconcile_venv() {
    local rebuild="$1"
    # Always reconcile the venv against requirements.txt, not just when it textually
    # changed in this pull — a venv can drift out of sync with a committed
    # requirements.txt for reasons this script can't see (a manual `git pull`/rebase
    # done outside update.sh, a previous run interrupted mid-install, a venv rebuilt
    # from an older checkout, etc). pip is idempotent and fast when nothing is missing,
    # so the safety net costs a couple of seconds even on a no-op update. pip also
    # downgrades packages that the checked-out lock pins lower (switching back to main).
    # A venv whose interpreter disappeared (e.g. after a distribution upgrade replaced
    # Python 3.12 with 3.14) cannot be repaired by pip and is rebuilt instead.
    if [ "$rebuild" -eq 1 ]; then
        rebuild_venv
    elif ! venv_works; then
        echo "venv/ is missing or its Python interpreter no longer runs."
        rebuild_venv
    else
        install_requirements venv/bin/python3
    fi
}

restart_running_instance() {
    local install_dir="$1" pids pid
    pids="$(pgrep -f "$install_dir/venv/bin/python3 src/main.py" || true)"

    if [ -z "$pids" ]; then
        echo "No running instance found — nothing to restart."
        return 0
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

# Bring the current branch up to date with origin. Fast-forwards normally. A branch that
# was rewritten on GitHub (force-pushed, e.g. a test branch restarted from main) is followed
# only when this checkout has no commits of its own, so nothing unpublished is discarded.
update_current_branch() {
    local check_only="$1" branch
    branch="$(git rev-parse --abbrev-ref HEAD)"
    if [ "$branch" = "HEAD" ]; then
        echo "Error: checkout is in detached HEAD state; cannot select an update branch." >&2
        echo "Choose one with: $0 --switch main" >&2
        exit 1
    fi

    fetch_branch "$branch"
    local local_only
    local_only="$(unpublished_commit HEAD "$branch")"

    local old_head new_head
    old_head="$(git rev-parse HEAD)"
    new_head="$(git rev-parse "origin/$branch")"

    if [ "$old_head" = "$new_head" ]; then
        echo "NO_UPDATE"
        if [ "$check_only" -eq 1 ]; then
            exit 0
        fi
        echo "Already up to date on $branch (${old_head:0:7})."
    elif git merge-base --is-ancestor "$old_head" "$new_head"; then
        echo "UPDATE_AVAILABLE"
        if [ "$check_only" -eq 1 ]; then
            exit "$EXIT_UPDATE_AVAILABLE"
        fi

        echo "Updating $branch: ${old_head:0:7} -> ${new_head:0:7}"
        git log --oneline "$old_head..$new_head"

        if ! git merge --ff-only "origin/$branch"; then
            echo "Error: local $branch has diverged from origin/$branch and can't fast-forward." >&2
            echo "Resolve manually (git status / git log), then re-run." >&2
            exit 1
        fi
    elif [ -z "$local_only" ]; then
        echo "UPDATE_AVAILABLE"
        if [ "$check_only" -eq 1 ]; then
            exit "$EXIT_UPDATE_AVAILABLE"
        fi
        echo "origin/$branch was rewritten (force-pushed); this checkout has no commits of its"
        echo "own, so following it: ${old_head:0:7} -> ${new_head:0:7}"
        git reset --keep "origin/$branch"
    else
        echo "Error: local $branch has commits that are not on origin and has diverged from" >&2
        echo "origin/$branch; refusing to update. Push or remove them first." >&2
        exit 1
    fi
}

# Check out origin/<target> as the local branch <target>, which then receives updates.
switch_branch() {
    local target="$1"
    if ! git check-ref-format --branch "$target" >/dev/null 2>&1; then
        echo "Error: '$target' is not a valid branch name." >&2
        exit 2
    fi

    fetch_branch "$target"
    local local_only=""
    if git show-ref --verify --quiet "refs/heads/$target"; then
        local_only="$(unpublished_commit "refs/heads/$target" "$target")"
    fi

    if [ -n "$local_only" ] \
        && ! git merge-base --is-ancestor "refs/heads/$target" "origin/$target"; then
        echo "Error: local branch $target has commits that are not on origin/$target;" >&2
        echo "refusing to replace it. Push or remove them first." >&2
        exit 1
    fi

    local old_branch old_head
    old_branch="$(git rev-parse --abbrev-ref HEAD)"
    old_head="$(git rev-parse --short HEAD)"
    git checkout --quiet -B "$target" "origin/$target"
    git branch --quiet --set-upstream-to="origin/$target" "$target" 2>/dev/null || true
    echo "SWITCHED"
    echo "Switched $old_branch (${old_head}) -> $target ($(git rev-parse --short HEAD))."
}

main() {
    local check_only=0 restart=1 list_branches=0 rebuild=0 target=""
    while [ "$#" -gt 0 ]; do
        case "$1" in
            --check-only) check_only=1 ;;
            --no-restart) restart=0 ;;
            --list-branches) list_branches=1 ;;
            --rebuild-venv) rebuild=1 ;;
            --switch)
                [ "$#" -ge 2 ] || usage
                target="$2"
                shift
                ;;
            *) usage ;;
        esac
        shift
    done
    if [ "$check_only" -eq 1 ] && { [ -n "$target" ] || [ "$rebuild" -eq 1 ] || [ "$list_branches" -eq 1 ]; }; then
        usage
    fi
    if [ "$list_branches" -eq 1 ] && { [ -n "$target" ] || [ "$rebuild" -eq 1 ]; }; then
        usage
    fi

    local install_dir
    install_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    cd "$install_dir"

    if [ ! -d .git ]; then
        echo "Error: $install_dir is not a git checkout." >&2
        exit 1
    fi

    if [ "$list_branches" -eq 1 ]; then
        git ls-remote --heads origin | sed -n 's#^[0-9a-f]*[[:space:]]*refs/heads/##p'
        exit 0
    fi

    echo "WhisperWriter update — installation: $install_dir"
    require_clean_tree

    # Where to return if the new code's dependencies cannot be installed.
    local previous_branch previous_commit
    previous_branch="$(git symbolic-ref -q --short HEAD || true)"
    previous_commit="$(git rev-parse HEAD)"

    if [ -n "$target" ]; then
        switch_branch "$target"
    else
        update_current_branch "$check_only"
    fi

    if ! reconcile_venv "$rebuild"; then
        if [ "$(git rev-parse HEAD)" != "$previous_commit" ]; then
            # Never leave new code on an environment that could not be updated for it:
            # go back to the version that was running and restore its dependencies.
            echo "Error: installing the dependencies failed; returning to ${previous_branch:-the previous commit} (${previous_commit:0:7})." >&2
            if [ -n "$previous_branch" ]; then
                git checkout --quiet -B "$previous_branch" "$previous_commit"
            else
                git checkout --quiet --detach "$previous_commit"
            fi
            if venv_works && install_requirements venv/bin/python3; then
                echo "Returned to ${previous_branch:-${previous_commit:0:7}}; nothing was changed."
            else
                echo "Error: the previous dependencies could not be restored either; run $0 again when the network is available." >&2
            fi
        else
            echo "Error: installing the dependencies failed; run $0 again to retry." >&2
        fi
        exit 1
    fi

    echo "Update complete."
    # The tray owns restart when it supervises this process, and reports failures.
    if [ "$restart" -eq 0 ]; then
        return
    fi
    restart_running_instance "$install_dir"
}

# Kept on one line with exit: bash reads this file while running it, so a checkout that
# replaces update.sh mid-run must not be able to supply further commands.
main "$@"; exit $?
