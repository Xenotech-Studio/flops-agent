#!/usr/bin/env bash
# Core commit message validation rules, shared by the local hook and CI:
#   Local (commit-msg hook): commit_msg_check.sh <message-file> --cached
#   CI (per existing commit): commit_msg_check.sh <message-file> --commit <sha>
# The two invocation modes only diverge on "where to read gitlink changes from"
# and "how to detect a merge commit" — the rules themselves are identical:
#   --cached → staged diff-index --cached (against the empty tree for the first commit); MERGE_HEAD marks a merge
#   --commit → diff-tree -r --root of that commit against its parent; existence of <sha>^2 marks a merge
set -euo pipefail

msg_file="$1"
mode="${2:---cached}"
sha="${3:-}"
if [ "$mode" != "--cached" ] && { [ "$mode" != "--commit" ] || [ -z "$sha" ]; }; then
    echo "Usage: commit_msg_check.sh <message-file> --cached | --commit <sha>" >&2
    exit 2
fi

pattern='claude|cursor|codex|copilot|chatgpt|gemini|codeium|windsurf|aider|devin|tabnine|cody'

if grep -qiE "$pattern" "$msg_file"; then
    echo "✗ Commit message failed validation, please rephrase and try again." >&2
    echo "  In an emergency you can skip this with git commit --no-verify (not recommended)." >&2
    exit 1
fi

# Merge commits are exempt from all the title structure checks below
if [ "$mode" = "--commit" ]; then
    git rev-parse -q --verify "${sha}^2" >/dev/null 2>&1 && exit 0
else
    [ -e "$(git rev-parse --git-path MERGE_HEAD)" ] && exit 0
fi

title="$(awk '!/^#/ && NF { print; exit }' "$msg_file")"

# ---------- Type prefix allowlist (all commits) ----------
# A tightened take on Conventional Commits: type is all-lowercase (case-sensitive;
# the spec leaves this to each team, and this repo picked all-lowercase); an
# optional (scope) may follow, with /, \, , etc. allowed as separators inside it;
# breaking changes get a ! before the colon (feat!: or feat(api)!:); the colon
# must be a plain half-width : followed by exactly one space; description must
# be non-empty.
types='feat|fix|docs|style|refactor|perf|test|build|ci|chore|revert'
scope='(\([^)]+\))?'
type_head="^(${types})${scope}!?: "
if ! [[ "$title" =~ ${type_head}[^[:space:]] ]]; then
    {
        echo "✗ Title must be \"type: description\" or \"type(scope): description\"; type must be one of (all-lowercase, case-sensitive):"
        echo "  feat fix docs style refactor perf test build ci chore revert"
        echo "  Use a plain half-width colon : followed by exactly one space; mark breaking changes with ! before the colon (e.g. feat!: or feat(api)!:)."
        echo "  Current title: $title"
        echo "  In an emergency you can skip this with git commit --no-verify (not recommended)."
    } >&2
    exit 1
fi

# ---------- ! breaking-change marker requires a matching BREAKING CHANGE note (per official spec) ----------
bang_head="^(${types})${scope}!: "
if [[ "$title" =~ $bang_head ]] && \
   ! grep -qE '^BREAKING[- ]CHANGE: [^[:space:]]' "$msg_file"; then
    {
        echo "✗ Title has a ! marking a breaking change; the body or footer must include a matching \"BREAKING CHANGE: description\"."
        echo "  Official format (as the first line of a footer, one space after the colon):"
        echo "    chore!: remove legacy config loading"
        echo ""
        echo "    BREAKING CHANGE: .flopsrc is no longer supported, migrate to the new format"
        echo "  In an emergency you can skip this with git commit --no-verify (not recommended)."
    } >&2
    exit 1
fi

# ---------- Bump-specific strict validation: only triggers on titles that self-identify as a bump ----------
# Only fires when "bump" immediately follows the type header (same shape as
# above, including the optional !). Mixed commits that touch a gitlink pointer
# incidentally (regular dev work that happens to move a submodule pointer) but
# whose title doesn't say "bump" pass through here untouched — no bump-format
# checks apply to them.
bump_re="${type_head}[Bb][Uu][Mm][Pp]([^[:alnum:]]|$)"
[[ "$title" =~ $bump_re ]] || exit 0

# Requirements for a commit that calls itself a bump:
# 1) The commit must actually contain a gitlink (mode 160000) pointer change
#    (deleting a submodule doesn't count as a bump). A pure version-number
#    release (e.g. only editing package.json) must not use "bump" wording —
#    use chore(release) or similar instead.
# 2) Every changed submodule must be listed as "name → new-short-SHA" (name is
#    the last path segment; nested ones may write "parent-dir space last-segment",
#    e.g. FlopsWeb cocoder-ui-core); the short SHA must be a genuine prefix
#    (>=7 chars) of that submodule's new pointer. The title must end with
#    parentheses describing what changed in the updated submodule(s) — multiple
#    submodules can share one set of parentheses.

# Source of gitlink changes: --cached reads the staging area (diff-index
# naturally expands full nested paths); --commit reads that commit's diff-tree
# against its parent, which needs -r to see gitlinks inside subdirectories,
# --root covers the first commit (against the empty tree), and --no-commit-id
# strips the leading commit-id line.
gitlink_changes() {
    if [ "$mode" = "--commit" ]; then
        git diff-tree -r --no-commit-id --root -z --raw "$sha"
    else
        local base
        if git rev-parse -q --verify HEAD >/dev/null 2>&1; then
            base=HEAD
        else
            base=$(git hash-object -t tree /dev/null)   # first commit: compare against the empty tree
        fi
        git diff-index --cached -z "$base"
    fi
}

example='chore: bump FlopsDesktop → 15f70f9, FlopsWeb cocoder-ui-core → 1f05e00, flops-chat-ui → 1b0fab3 (load local images in md preview)'

seen_gitlink=0
problems=""
while IFS= read -r -d '' meta && IFS= read -r -d '' path; do
    set -- ${meta#:}
    new_mode="$2"; new_sha="$4"
    [ "$new_mode" = "160000" ] || continue
    case "$new_sha" in *[!0]*) ;; *) continue ;; esac   # new SHA all zeros = submodule deletion, exempt
    seen_gitlink=1
    name="${path##*/}"
    # The name must be immediately followed by "→ short-SHA" (only whitespace
    # allowed in between), so each submodule gets its own arrow pair instead of
    # several names sharing one; the short SHA written must be a prefix of that
    # submodule's new pointer.
    if [[ "$title" =~ (^|[^[:alnum:]_.-])"$name"[[:space:]]*→[[:space:]]*([0-9a-f]{7,40}) ]]; then
        short="${BASH_REMATCH[2]}"
        if [[ "$new_sha" != "$short"* ]]; then
            # A variable immediately followed by a full-width character needs braces:
            # bash 3.2 would otherwise merge the multi-byte character's first byte into the variable name
            problems+="  · ${name}: wrote ${short}, but the new pointer is actually ${new_sha:0:7}"$'\n'
        fi
    else
        problems+="  · ${name}: title is missing \"$name → ${new_sha:0:7}\""$'\n'
    fi
done < <(gitlink_changes)

if [ "$seen_gitlink" = 0 ]; then
    {
        echo "✗ Title claims to be a bump, but this commit has no submodule pointer (gitlink) changes."
        echo "  For non-submodule changes such as a plain version bump, use chore(release): or similar wording instead."
        echo "  In an emergency you can skip this with git commit --no-verify (not recommended)."
    } >&2
    exit 1
fi

if ! [[ "$title" =~ [(（].+[)）][[:space:]]*$ ]]; then
    problems+="  · Title is missing the trailing parenthetical note (describe what changed in the updated submodule(s); multiple submodules can share one note)"$'\n'
fi

if [ -n "$problems" ]; then
    {
        echo "✗ A bump commit's title should let readers know where every pointer landed without looking at the diff:"
        printf '%s' "$problems"
        echo "  Valid example: $example"
        echo "  In an emergency you can skip this with git commit --no-verify (not recommended)."
    } >&2
    exit 1
fi

exit 0
