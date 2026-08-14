#!/usr/bin/env python3
"""speakingwords SessionStart injector (Claude Code).

Why this exists
---------------
No hook event can intercept reply text before the user sees it. `Stop` fires
after the reply is written, so enforcement is inherently lint-after: the reply
is already spent by the time a violation is known, and fixing it costs a full
regeneration. The only way to reduce that cost is to stop the violation being
written in the first place, which means saying what the rules are *before* the
first reply rather than after it.

So this hook states the installed style contract once, early in the session, as
`additionalContext`. One ~200-400 token block near the top of the context —
prompt-cache friendly, not repeated per prompt — against one avoided reply
regeneration per prevented bounce. The Stop hook stays exactly as it was: this
is prevention, not enforcement, and the backstop is unaffected either way.

Contract with the host agent
----------------------------
  stdin   JSON object. Fields used here:
            session_id  identifies the session, so the block goes in once
            source      "startup", "resume", "clear", "compact"
  stdout  nothing, or the SessionStart hookSpecificOutput carrying the block
  exit    always 0

Fail-open rule (assertion A26): unreadable stdin, missing pref.json, unwritable
marker directory, an exception anywhere at all — every one of them exits 0 and
prints nothing. Absence, failure and timeout must all change nothing, because
the value of this hook is a saved regeneration, never a blocked reply. It has
no authority to stop anything and must never behave as if it does.

Once per session
----------------
`source` is "startup" on a fresh session but also "resume", "clear" and
"compact" later in the same one, so the event alone is not a once-per-session
signal. Session ids seen are recorded in a small capped file beside pref.json;
a repeat id prints nothing. If that file cannot be read or written, the hook
prefers to stay silent rather than repeat itself.

Claude Code only. Codex has no confirmed pre-reply context channel, so its
adapter ships lint-after only (plan §8) and nothing here is wired for it.
"""

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL_ROOT = os.path.dirname(HERE)
PREF_PATH = os.path.join(SKILL_ROOT, "pref.json")
SEEN_PATH = os.path.join(SKILL_ROOT, "sessions.json")

EXIT_OK = 0

# How many session ids to remember. Enough that a long day of parallel sessions
# never wraps around into a repeat injection, small enough that the file stays
# a few kilobytes and never becomes a second telemetry log to rotate.
MAX_REMEMBERED = 200

SESSION_KEYS = ("session_id", "sessionId", "session-id")
SOURCE_KEYS = ("source", "trigger")

# The register is one line because it is one rule, the same at every voice and
# every level (plan §2 W3). It goes first: the linter can catch a banned word
# after the fact, but sentence construction is the part that is cheapest to get
# right before the reply is written, not after it bounces.
REGISTER_RULE = (
    "Register: write like a colleague in a Slack DM. Short sentences, everyday "
    "words, contractions where they read naturally. Technical terms stay — it is "
    "the grammar around them that stays simple, not the vocabulary of the domain. "
    "No essay connectives (furthermore, moreover, thus, hence, whilst, prior to)."
)

VOICE_RULES = {
    "terse": (
        "Voice is terse: point form only. Bullets, short headed lists, tables and "
        "code blocks. More than two consecutive prose sentences is a violation. "
        "One idea per bullet."
    ),
    "convo": (
        "Voice is convo: prose is retained and paragraphs are correct. Point form "
        "only where the content is genuinely a list. Brevity is not forced — do "
        "not cut an explanation to hit a word count."
    ),
}

CONCISENESS_RULES = {
    "low": (
        "Conciseness is low: keep the prose, cut the decoration. Filler, "
        "restatement and stacked hedges go; explanations stay."
    ),
    "med": (
        "Conciseness is med: every sentence earns its place. Explanations stay, "
        "elaborations go."
    ),
    "high": (
        "Conciseness is high: load-bearing content only. Every fact, number, "
        "path and code block still survives."
    ),
}

# lang-function-over-inventory, the one level-gated language rule. It is stated
# only at the level it is active at, so a low or high session never carries an
# instruction it is not meant to follow. It sits next to the conciseness line
# because that is where a reader looks for what a report may leave out, but it is
# a language rule: it changes what the report says, not how much survives.
FUNCTION_RULE = {
    "med": (
        "Report what a change does, not the parts it is made of. Keep the count "
        "and a pointer — a file, a table, a diff — and drop the roll call. "
        "Numbers, paths, code blocks and caveats always stay."
    ),
}


def first_string(payload, keys):
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def read_pref():
    try:
        with open(PREF_PATH, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def voice_of(pref):
    voice = pref.get("voice")
    return voice if voice in VOICE_RULES else "convo"


def conciseness_of(pref):
    """The installed level, or `high` when the key is missing.

    Same fallback as lint.py and hook_stop.py: only a 0.1.0 install can be
    missing the key, and 0.1.0 behaviour measured in the `high` band.
    """
    level = pref.get("conciseness")
    return level if level in CONCISENESS_RULES else "high"


def build_block(pref):
    """The style block, scoped so it cannot leak into non-prose output.

    The scoping line is load-bearing, not politeness. Without it a conciseness
    instruction reads as licence to shorten code, file paths, command output and
    quoted text, which is the exact failure the anti-loss invariant exists to
    prevent.
    """
    voice = voice_of(pref)
    level = conciseness_of(pref)
    rules = [REGISTER_RULE, VOICE_RULES[voice], CONCISENESS_RULES[level]]
    if level in FUNCTION_RULE:
        rules.append(FUNCTION_RULE[level])
    return "\n".join([
        "speakingwords style rules are installed for this session.",
        "",
        "Scope: these apply to user-facing prose only. They never apply to code, "
        "file paths, command output, quoted text, tool arguments or literal data.",
        "",
    ] + ["- " + rule for rule in rules] + [
        "- No fact, number, path or code block may be lost to any of these rules. "
        "Losing content is worse than the violation it was meant to fix.",
        "",
        "A reply that breaks these is linted and bounced once, and the rewrite "
        "costs a full regeneration. Writing it this way first is cheaper.",
    ])


def read_seen():
    try:
        with open(SEEN_PATH, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        seen = data.get("sessions") if isinstance(data, dict) else None
        return [s for s in seen if isinstance(s, str)] if isinstance(seen, list) else []
    except FileNotFoundError:
        return []
    except Exception:
        # A corrupt marker file is a reason to stay quiet, not to inject twice.
        return None


def remember(session_id, seen):
    """Record a session id, newest last, capped. Best effort.

    Temp file then rename, so a crash mid-write leaves the old list or the new
    one and never a torn file (A22).
    """
    kept = [s for s in seen if s != session_id][-(MAX_REMEMBERED - 1):]
    kept.append(session_id)
    tmp = "%s.%d.tmp" % (SEEN_PATH, os.getpid())
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"sessions": kept}, fh)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, SEEN_PATH)
        return True
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        return False


def decide(payload):
    """The whole verdict: payload in, block text or None out.

    None means stay silent, which is what an already-injected session, an
    unreadable marker file and a malformed payload all get.
    """
    if not isinstance(payload, dict):
        return None

    session_id = first_string(payload, SESSION_KEYS)
    if not session_id:
        # No id means no way to tell a resume from a fresh start. Injecting on
        # every event would put the block in the context several times over.
        return None

    seen = read_seen()
    if seen is None or session_id in seen:
        return None

    if not remember(session_id, seen):
        # Nothing recorded the injection, so nothing can stop it repeating on the
        # next SessionStart in this same session. Silence is the safe half.
        return None

    return build_block(read_pref())


def emit(block):
    """Write the SessionStart contract to stdout. Silence injects nothing."""
    if block:
        sys.stdout.write(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "SessionStart",
                "additionalContext": block,
            }
        }) + "\n")
    return EXIT_OK


def main():
    try:
        payload = json.loads(sys.stdin.read())
        return emit(decide(payload))
    except BaseException as exc:  # fail open, always (A26)
        sys.stderr.write("speakingwords session hook error: %r\n" % (exc,))
        return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
