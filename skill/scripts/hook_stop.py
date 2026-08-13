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


def log_hits(violations, voice):
    """Append one single-line JSON record per violation (plan A9).

    Best effort: telemetry is never allowed to break enforcement, so any
    failure here is swallowed.
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
                fh.write(
                    json.dumps(
                        {
                            "ts": stamp,
                            "rule": item.get("rule"),
                            "match": item.get("match", ""),
                            "severity": item.get("severity", "warn"),
                            "voice": voice,
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )
    except Exception:
        pass


def run():
    payload = read_payload()

    transcript_path = payload.get("transcript_path")
    if not transcript_path or not os.path.isfile(transcript_path):
        return EXIT_OK  # nothing to lint — approve

    text = last_assistant_text(transcript_path)
    if not text.strip():
        return EXIT_OK

    voice = read_voice()
    lint = load_lint()
    violations = lint.lint(text, voice, lint.read_rules())
    if not violations:
        return EXIT_OK

    # A5 — one bounce maximum. A second Stop hook on the same reply reports
    # nothing and blocks nothing, so the rewrite always gets to land.
    if payload.get("stop_hook_active"):
        return EXIT_OK

    log_hits(violations, voice)
    sys.stdout.write(
        json.dumps({"decision": "block", "reason": build_reason(violations, voice)})
        + "\n"
    )
    return EXIT_OK


def main():
    try:
        return run()
    except BaseException as exc:  # fail open, always
        sys.stderr.write("speakingwords hook error: %r\n" % (exc,))
        return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
