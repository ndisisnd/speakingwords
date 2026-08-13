#!/usr/bin/env python3
"""speakingwords Stop-hook entry point (Claude Code).

The agent has finished a reply. This script reads the Stop-hook payload on
stdin, pulls the reply text out of the transcript, runs the same deterministic
lint pass `lint.py` runs, and either lets the reply stand or bounces it once.

Contract with the host agent
----------------------------
stdin   JSON object. Fields used here:
          transcript_path   path to the JSONL conversation transcript
          stop_hook_active  true when a Stop hook already bounced this reply
                            (THE loop guard — plan assertion A5)
stdout  nothing when the reply is approved;
        {"decision": "block", "reason": "<feedback>"} when it is bounced
exit    always 0. A hook that exits non-zero is a broken install, and a broken
        install must never be able to wedge a conversation.

Fail-open is the rule throughout: malformed stdin, a missing transcript, an
unreadable lexicon, a lint bug — every one of them approves the reply. The
worst outcome of this script is that a bad reply gets through, never that a
good reply is lost.

Telemetry: one JSON line per violation is appended to <skill root>/hits.jsonl,
but only on a bounce that actually fired. Suppressed second passes are not
counted twice, so `status` counts bounces, not lint passes.

Shared with Codex
-----------------
`decide()` is the whole verdict — payload in, block-dict or None out. Codex
mirrors Claude Code's event names, payload shape and decision contract, so
scripts/hook_codex.py imports this module and calls `decide()` rather than
carrying a second copy of the logic. That import is what makes plan eval E7
(cross-agent parity) true by construction instead of by coincidence: there is
only one linter, one loop guard, and one telemetry writer for both agents.
"""

import datetime
import importlib.util
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL_ROOT = os.path.dirname(HERE)
LINT_PATH = os.path.join(HERE, "lint.py")
PREF_PATH = os.path.join(SKILL_ROOT, "pref.json")
HITS_PATH = os.path.join(SKILL_ROOT, "hits.jsonl")
SKILL_MD = os.path.join(SKILL_ROOT, "SKILL.md")

EXIT_OK = 0

# Keep the feedback string short enough to stay readable in the agent's
# context. lint.py already caps each match at 120 chars.
MAX_REPORTED_VIOLATIONS = 12
MAX_REASON_CHARS = 4000

# Payload key aliases. Claude Code sends the snake_case form; Codex mirrors it,
# but a host that renames or drops a key must degrade to fail-open rather than
# throw, so every lookup goes through these lists.
TRANSCRIPT_KEYS = ("transcript_path", "transcriptPath", "transcript-path", "transcript")
GUARD_KEYS = ("stop_hook_active", "stopHookActive", "stop-hook-active")
# Last resort when no transcript is reachable: some hosts (and the Codex
# `notify` fallback) hand over the finished reply text directly.
MESSAGE_KEYS = ("last_assistant_message", "lastAssistantMessage", "last-assistant-message")


def load_lint():
    """Import lint.py as a module — same code path as the CLI, no subprocess.

    Importing keeps the hook fast (no second interpreter start) while lint.py
    stays runnable standalone, because it guards its own entry point.
    """
    spec = importlib.util.spec_from_file_location("speakingwords_lint", LINT_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def read_payload():
    raw = sys.stdin.read()
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError("stop-hook payload is not an object")
    return payload


def read_voice():
    try:
        with open(PREF_PATH, "r", encoding="utf-8") as fh:
            voice = json.load(fh).get("voice")
    except Exception:
        voice = None
    return voice if voice in ("terse", "convo") else "convo"


def is_assistant(entry):
    if entry.get("type") == "assistant":
        return True
    message = entry.get("message")
    if isinstance(message, dict) and message.get("role") == "assistant":
        return True
    return entry.get("role") == "assistant"


def text_of(entry):
    """Concatenate the text blocks of one transcript entry.

    Tool calls, thinking blocks and images are skipped — only what the user
    actually reads is linted.
    """
    message = entry.get("message")
    content = message.get("content") if isinstance(message, dict) else entry.get("content")
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts = []
    for block in content:
        if isinstance(block, str):
            parts.append(block)
        elif isinstance(block, dict) and block.get("type") == "text":
            value = block.get("text")
            if isinstance(value, str):
                parts.append(value)
    return "\n".join(parts)


def last_assistant_text(transcript_path):
    """Return the text of the final assistant turn, or "" if there is none.

    A turn can span several transcript lines (text, tool call, more text), so
    the trailing run of assistant lines is concatenated in order and anything
    before the last user line is ignored.
    """
    with open(transcript_path, "r", encoding="utf-8", errors="replace") as fh:
        lines = fh.read().splitlines()

    entries = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except ValueError:
            continue  # a partially written line must not sink the pass
        if isinstance(entry, dict):
            entries.append(entry)

    tail = []
    for entry in reversed(entries):
        if is_assistant(entry):
            tail.append(entry)
        elif tail:
            break
        elif entry.get("type") == "user" or entry.get("role") == "user":
            break
    tail.reverse()

    chunks = [t for t in (text_of(e) for e in tail) if t.strip()]
    return "\n\n".join(chunks)


def build_reason(violations, voice):
    shown = violations[:MAX_REPORTED_VIOLATIONS]
    hidden = len(violations) - len(shown)

    lines = [
        "speakingwords: your last reply broke %d style rule%s."
        % (len(violations), "" if len(violations) == 1 else "s"),
        "",
    ]
    for item in shown:
        lines.append(
            "- %s (%s): %s"
            % (item.get("rule", "?"), item.get("severity", "warn"), json.dumps(item.get("match", "")))
        )
    if hidden > 0:
        lines.append("- ... and %d more of the same kind." % hidden)
    lines.append("")
    lines.append(
        "Rewrite the last reply following %s — %s voice. Do not mention the correction."
        % (SKILL_MD, voice)
    )
    reason = "\n".join(lines)
    if len(reason) > MAX_REASON_CHARS:
        reason = reason[: MAX_REASON_CHARS - 3] + "..."
    return reason


def log_hits(violations, voice, extra=None):
    """Append one single-line JSON record per violation (plan A9).

    Best effort: telemetry is never allowed to break enforcement, so any
    failure here is swallowed.

    `extra` merges extra fields into every record. The Codex audit fallback
    uses it to mark records it could observe but not block, so `status` can
    tell an enforced bounce from a downgraded one.
    """
    try:
        stamp = (
            datetime.datetime.now(datetime.timezone.utc)
            .replace(microsecond=0)
            .isoformat()
            .replace("+00:00", "Z")
        )
        with open(HITS_PATH, "a", encoding="utf-8") as fh:
            for item in violations:
                record = {
                    "ts": stamp,
                    "rule": item.get("rule"),
                    "match": item.get("match", ""),
                    "severity": item.get("severity", "warn"),
                    "voice": voice,
                }
                if extra:
                    record.update(extra)
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        pass


def first_string(payload, keys):
    """First key in `keys` holding a non-blank string, else None."""
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return None


def loop_guard(payload):
    """True when a Stop hook already bounced this reply (plan A5)."""
    return any(bool(payload.get(key)) for key in GUARD_KEYS)


def reply_text(payload):
    """The text the user actually read, or "" when it cannot be recovered.

    Preferred source is the transcript, because it is the reply verbatim.
    When the transcript key is named differently, missing, or unreadable, the
    payload's own copy of the last assistant message is used instead. Both
    routes failing means nothing to lint, which means approve.
    """
    path = first_string(payload, TRANSCRIPT_KEYS)
    if path and os.path.isfile(path):
        try:
            text = last_assistant_text(path)
        except Exception:
            text = ""  # unreadable transcript falls through to the message key
        if text.strip():
            return text
    return first_string(payload, MESSAGE_KEYS) or ""


def decide(payload):
    """The whole verdict: payload in, block-dict or None out.

    None means approve — that covers a clean reply, an unrecoverable reply,
    and a suppressed second pass alike, because all three end the same way:
    the hook stays silent and the reply stands.

    Shared verbatim with the Codex entry point, so both agents log the same
    rule ids and reach the same verdict on the same text (plan E7).
    """
    if not isinstance(payload, dict):
        return None

    text = reply_text(payload)
    if not text.strip():
        return None

    voice = read_voice()
    lint = load_lint()
    violations = lint.lint(text, voice, lint.read_rules())
    if not violations:
        return None

    # A5 — one bounce maximum. A second Stop hook on the same reply reports
    # nothing and blocks nothing, so the rewrite always gets to land.
    if loop_guard(payload):
        return None

    log_hits(violations, voice)
    return {"decision": "block", "reason": build_reason(violations, voice)}


def emit(decision):
    """Write the decision contract to stdout. Silence approves."""
    if decision is not None:
        sys.stdout.write(json.dumps(decision) + "\n")
    return EXIT_OK


def run():
    return emit(decide(read_payload()))


def main():
    try:
        return run()
    except BaseException as exc:  # fail open, always
        sys.stderr.write("speakingwords hook error: %r\n" % (exc,))
        return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
