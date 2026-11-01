#!/bin/bash
# Claude Code PostToolUse hook: formats a Python file Claude has just edited
# with pre-commit's ruff-format, the same pinned Ruff the commit hook runs, so
# formatting is fixed as Claude goes rather than failing the commit later.
# Only formats: `ruff --fix` would delete an import Claude added a step before
# using it.
#
# Reads the hook's JSON on stdin. Always exits 0, so a missing pre-commit
# never blocks an edit.

f=$(jq -r '.tool_input.file_path // .tool_response.filePath // empty')
case "$f" in
    *.py) ;;
    *) exit 0 ;;
esac
[ -f "$f" ] || exit 0

cd "${CLAUDE_PROJECT_DIR:-$(dirname "$0")/../..}" || exit 0

# pre-commit may only be installed for the Python the git hook was installed
# with (`pre-commit install` records it in the hook as INSTALL_PYTHON).
if pre-commit --version >/dev/null 2>&1; then
    pre_commit=(pre-commit)
else
    hook="$(git rev-parse --git-common-dir)/hooks/pre-commit"
    python=$(sed -n 's/^INSTALL_PYTHON=//p' "$hook" 2>/dev/null)
    [ -x "$python" ] || exit 0
    pre_commit=("$python" -m pre_commit)
fi

"${pre_commit[@]}" run ruff-format --files "$f" >/dev/null 2>&1
exit 0
