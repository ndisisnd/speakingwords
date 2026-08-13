#!/usr/bin/env python3
"""speakingwords Stop-hook entry point (OpenAI Codex CLI).

Codex's hooks engine deliberately mirrors Claude Code's lifecycle event names,
its stdin payload shape, and its `{"decision": "block", "reason": ...}` stdout
contract. So this file is deliberately thin: it reads the payload and hands it
to hook_stop.decide(), which is the same verdict function the Claude Code entry
point uses. One linter, one loop guard, one telemetry writer, two entry points.

That sharing is the point. Plan eval E7 requires identical verdicts and
identical hits.jsonl rule ids across both agents; delegating rather than
duplicating makes that true by construction.

Contract with Codex
-------------------
stdin   JSON object, same fields as the Claude Code Stop payload
stdout  nothing when the reply is approved;
        {"decision": "block", "reason": "<feedback>"} when it is bounced
exit    always 0

Payload-shape defence
---------------------
Key names are read through alias lists in hook_stop (transcript_path and
friends, stop_hook_active and friends). When no transcript is reachable at all,
the verdict falls back to the payload's own copy of the last assistant message.
When even that is absent, the reply is approved. Every unknown is resolved in
favour of letting the reply through: a bad reply slipping past is a small cost,
a good reply lost to a broken install is not.

Trust: Codex will not run this hook until the user grants hook trust once. Until
they do, replies pass unlinted. The installer prints that step; nothing here can
grant it.
"""

import importlib.util
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
HOOK_STOP_PATH = os.path.join(HERE, "hook_stop.py")

EXIT_OK = 0


def load_hook_stop():
    """Import the shared Claude Code hook module from beside this file.

    Loaded by path rather than by name because the skill root is not on
    sys.path when the agent invokes the hook by absolute path.
    """
    spec = importlib.util.spec_from_file_location("speakingwords_hook_stop", HOOK_STOP_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run():
    raw = sys.stdin.read()
    payload = json.loads(raw)
    hook_stop = load_hook_stop()
    return hook_stop.emit(hook_stop.decide(payload))


def main():
    try:
        return run()
    except BaseException as exc:  # fail open, always
        sys.stderr.write("speakingwords codex hook error: %r\n" % (exc,))
        return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
