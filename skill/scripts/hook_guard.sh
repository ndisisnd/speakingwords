#!/bin/sh
# speakingwords hook wrapper — the python3 probe (plan §2 W4.4, assertion A24).
#
# Claude Code and Codex both run the Stop hook as a shell command. When that
# command is `python3 <script>` on a machine with no python3, the shell exits
# 127 and the agent reports a broken hook after every single reply. This wrapper
# turns that into a clean exit instead: no interpreter, no lint, nothing
# blocked, and one note left beside pref.json so `speakingwords status` can
# explain the silence rather than leaving the user to guess.
#
# The degraded path always exits 0. A missing interpreter must never cost a
# user a reply — the tool degrades to memory-mode honesty, not to breakage.
#
# Usage:  sh hook_guard.sh <path to hook_stop.py|hook_codex.py>

script="$1"
[ -n "$script" ] || exit 0

if command -v python3 >/dev/null 2>&1; then
	exec python3 "$script"
fi

# Best effort and quiet: the skill root is two levels up from scripts/.
root=$(dirname "$(dirname "$script")")
printf '%s\n' "python3 was not on PATH when the hook ran, so linting is off." \
	>"$root/lint_disabled" 2>/dev/null

exit 0
