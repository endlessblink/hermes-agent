#!/usr/bin/env bash
# Fail if Hermes has drifted back into more than one working tree.
#
# The duplicate `hermes-agent-updated-<date>` tree was created as a manual
# workaround: `dirtyUpdateGuard` (apps/desktop/electron/main.ts) refuses to
# update a checkout with uncommitted changes, and this checkout is habitually
# dirty with work in progress. The workaround split development across two
# trees for two days, and a fix written in one of them never ran, because the
# desktop launcher pointed at the other.
#
# Update in place instead: commit or stash, then `git fetch upstream && git
# merge upstream/main`.

set -euo pipefail

repo_root="$(git rev-parse --show-toplevel)"
launcher="${HOME}/.local/bin/hermes-desktop"
status=0

worktrees="$(git worktree list --porcelain | grep -c '^worktree ' || true)"
if [ "${worktrees}" -gt 1 ]; then
  echo "FAIL: ${worktrees} git worktrees. Hermes must live in exactly one." >&2
  git worktree list >&2
  echo "  Fix: git worktree remove <path>   (import its work first)" >&2
  status=1
fi

# The launcher hardcodes the tree twice. A stale path silently runs old code.
if [ -f "${launcher}" ]; then
  if ! grep -q "${repo_root}/apps/desktop/release/linux-unpacked/Hermes" "${launcher}"; then
    echo "FAIL: ${launcher} does not point at this checkout (${repo_root})." >&2
    grep -n 'hermes-agent' "${launcher}" >&2 || true
    status=1
  fi
fi

if [ "${status}" -eq 0 ]; then
  echo "OK: one worktree, launcher points here."
fi
exit "${status}"
