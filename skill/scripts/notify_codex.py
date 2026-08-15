#!/usr/bin/env python3
"""speakingwords audit-only fallback for Codex CLI below v0.124.0 (plan A13).

Why this file exists
--------------------
The Codex hooks engine only became stable at v0.124.0. On an older Codex there
is no Stop hook to wire, so there is no way to bounce a reply. What is still
available is the `notify` key in ~/.codex/config.toml: Codex runs a program with
a single JSON argument on `agent-turn-complete`, after the turn has already been
delivered to the user.

That gives observation without enforcement. This script lints the finished reply
and records what it found, so `status` still has an honest hit table and the
user can see what hook mode *would* have caught. It never blocks, never prints a
decision, and never exits non-zero — none of those would mean anything here, and
a notify program that misbehaves can disrupt the session.

Every record it writes carries "audit": true, so `status` can separate a hit
that was enforced from one that was merely witnessed. The fix is to upgrade
Codex; the installer says so in its summary.

Contract with Codex
-------------------
argv[1]  JSON object. Fields used:
           type                   event name; only "agent-turn-complete" is acted on
           last-assistant-message the finished reply text
           turn-id                echoed into telemetry when present
stdout   nothing, ever
exit     always 0
"""

import importlib.util
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
HOOK_STOP_PATH = os.path.join(HERE, "hook_stop.py")

EXIT_OK = 0

TURN_COMPLETE = "agent-turn-complete"
MESSAGE_KEYS = ("last-assistant-message", "last_assistant_message", "lastAssistantMessage")
TURN_ID_KEYS = ("turn-id", "turn_id", "turnId")


def load_hook_stop():
    """Reuse the shared voice reader, linter loader and telemetry writer."""
    spec = importlib.util.spec_from_file_location("speakingwords_hook_stop", HOOK_STOP_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def first_string(payload, keys):
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return None


def run(argv):
    if len(argv) < 2:
        return EXIT_OK
    payload = json.loads(argv[1])
    if not isinstance(payload, dict):
        return EXIT_OK

    # Codex sends several notify event types; only a finished turn carries a
    # reply worth linting. Anything else is left alone.
    if payload.get("type") != TURN_COMPLETE:
        return EXIT_OK

    text = first_string(payload, MESSAGE_KEYS)
    if not text:
        return EXIT_OK

    hook_stop = load_hook_stop()
    pref = hook_stop.read_pref()
    voice = hook_stop.read_voice(pref)
    lint = hook_stop.load_lint()
    # Same level and register as the Stop hook would use, so the audit log
    # records what an enforcing install would have blocked, not a different rule
    # set (E7).
    violations = lint.lint(
        text, voice, lint.read_rules(),
        hook_stop.read_conciseness(pref), hook_stop.read_register(pref),
    )
    if not violations:
        return EXIT_OK

    extra = {"audit": True, "agent": "codex"}
    turn_id = first_string(payload, TURN_ID_KEYS)
    if turn_id:
        extra["turn"] = turn_id
    hook_stop.log_hits(violations, voice, extra=extra)
    return EXIT_OK


def main(argv=None):
    try:
        return run(sys.argv if argv is None else argv)
    except BaseException as exc:  # observation must never disrupt a session
        sys.stderr.write("speakingwords codex notify error: %r\n" % (exc,))
        return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
