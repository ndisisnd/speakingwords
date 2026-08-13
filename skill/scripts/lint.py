#!/usr/bin/env python3
"""speakingwords deterministic lint pass.

Reads an agent reply from stdin or a file argument, scans it against the strip
rules in ../refs/lexicon.md, adds one voice-dependent structural check, and
prints a single JSON object on stdout.

Contract (plan assertion A6): exit 0 on clean, exit 2 on violations, never any
other code. An internal error is never allowed to block a user's reply, so a
crash is reported on stderr and reported as clean with a "lint_error" field.

Usage:
    python3 lint.py --voice terse reply.txt
    cat reply.txt | python3 lint.py --voice convo
"""

import json
import os
import re
import sys

EXIT_CLEAN = 0
EXIT_VIOLATIONS = 2

LEXICON_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), os.pardir, "refs", "lexicon.md"
)

# Cap the reported matches so a pathological input cannot produce a megabyte of
# JSON. The verdict is unaffected; only the listing is truncated.
MAX_MATCHES_PER_RULE = 20
MAX_TOTAL_MATCHES = 200
MATCH_SNIPPET_CHARS = 120

STRIP_HEADING = re.compile(r"^##\s+Strip rules\s*$", re.MULTILINE)
NEXT_HEADING = re.compile(r"^##\s+", re.MULTILINE)

# Lines that are structurally not prose: bullets, numbered items, headings,
# quotes, table rows, fence markers, horizontal rules.
NON_PROSE_LINE = re.compile(r"^\s*(?:[-*+•]\s|\d+[.)]\s|#{1,6}\s|>|\||```|~~~|---)")
SENTENCE_SPLIT = re.compile(r"(?<=[.!?])[\s]+")
FENCE = re.compile(r"^\s*(?:```|~~~)")

# Terse allowance: a block may carry up to this many prose sentences before it
# counts as a paragraph-form answer. Two is enough for a one-line lead-in plus a
# qualifier; three or more is prose.
TERSE_SENTENCE_ALLOWANCE = 2


class LexiconError(Exception):
    pass


def read_rules(path=LEXICON_PATH):
    """Parse the strip-rule table out of lexicon.md.

    The lexicon is the single source of truth; nothing is hardcoded here.
    Returns a list of (rule_id, compiled_pattern, severity).
    """
    try:
        with open(path, "r", encoding="utf-8") as fh:
            text = fh.read()
    except OSError as exc:
        raise LexiconError("cannot read lexicon at %s: %s" % (path, exc))

    start = STRIP_HEADING.search(text)
    if not start:
        raise LexiconError("no '## Strip rules' section in %s" % path)

    rest = text[start.end():]
    nxt = NEXT_HEADING.search(rest)
    section = rest[: nxt.start()] if nxt else rest

    rules = []
    seen = set()
    for line in section.splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        # Rebuild cells that were split on an escaped pipe inside a pattern.
        merged = []
        for cell in cells:
            if merged and merged[-1].endswith("\\"):
                merged[-1] = merged[-1] + "|" + cell
            else:
                merged.append(cell)
        cells = merged
        if len(cells) < 4:
            continue
        rule_id, pattern, severity = cells[0], cells[1], cells[2].lower()
        if not rule_id or rule_id.startswith("#") or rule_id.lower() == "id":
            continue
        if set(rule_id) <= set("-: "):  # markdown separator row
            continue
        # A literal pipe in a pattern is written \| so it survives the table.
        pattern = pattern.strip("`").strip().replace("\\|", "|")
        if not pattern:
            continue
        if severity not in ("error", "warn"):
            severity = "warn"
        if rule_id in seen:
            raise LexiconError("duplicate rule id: %s" % rule_id)
        seen.add(rule_id)
        try:
            compiled = re.compile(pattern, re.IGNORECASE | re.MULTILINE)
        except re.error as exc:
            raise LexiconError("bad pattern for %s: %s" % (rule_id, exc))
        rules.append((rule_id, compiled, severity))

    if not rules:
        raise LexiconError("strip-rule table in %s is empty" % path)
    return rules


def strip_code_fences(text):
    """Blank out fenced code blocks so structural checks ignore them."""
    out = []
    inside = False
    for line in text.splitlines():
        if FENCE.match(line):
            inside = not inside
            out.append("")
            continue
        out.append("" if inside else line)
    return "\n".join(out)


def scan_strip_rules(text, rules):
    violations = []
    total = 0
    for rule_id, pattern, severity in rules:
        count = 0
        for match in pattern.finditer(text):
            if total >= MAX_TOTAL_MATCHES or count >= MAX_MATCHES_PER_RULE:
                break
            snippet = match.group(0).strip()[:MATCH_SNIPPET_CHARS]
            violations.append(
                {"rule": rule_id, "match": snippet, "severity": severity}
            )
            count += 1
            total += 1
        if total >= MAX_TOTAL_MATCHES:
            break
    return violations


def check_terse_structure(text):
    """Heuristic: flag paragraph-form answers in terse voice.

    Split the reply into blank-line-separated blocks. Drop code fences and any
    line that is structurally not prose (bullet, number, heading, quote, table
    row). Whatever prose is left in a block is counted in sentences; more than
    TERSE_SENTENCE_ALLOWANCE consecutive sentences is a prose block.

    This is deliberately crude. It cannot tell a well-formed paragraph from a
    rambling one, only that point-form was not used where the voice demands it.
    """
    violations = []
    body = strip_code_fences(text)
    for block in re.split(r"\n\s*\n", body):
        prose_lines = [
            ln.strip()
            for ln in block.splitlines()
            if ln.strip() and not NON_PROSE_LINE.match(ln)
        ]
        if not prose_lines:
            continue
        joined = " ".join(prose_lines)
        sentences = [s for s in SENTENCE_SPLIT.split(joined) if s.strip()]
        if len(sentences) > TERSE_SENTENCE_ALLOWANCE:
            violations.append(
                {
                    "rule": "terse-prose-block",
                    "match": joined[:MATCH_SNIPPET_CHARS],
                    "severity": "error",
                }
            )
    return violations


def read_input(argv_path):
    if argv_path:
        with open(argv_path, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read()
    return sys.stdin.read()


def parse_args(argv):
    voice = "convo"
    path = None
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--voice":
            i += 1
            voice = argv[i] if i < len(argv) else "convo"
        elif arg.startswith("--voice="):
            voice = arg.split("=", 1)[1]
        elif arg in ("-h", "--help"):
            sys.stderr.write(__doc__)
            raise SystemExit(EXIT_CLEAN)
        elif not arg.startswith("-"):
            path = arg
        i += 1
    if voice not in ("terse", "convo"):
        voice = "convo"
    return voice, path


def lint(text, voice, rules):
    violations = scan_strip_rules(text, rules)
    if voice == "terse":
        violations.extend(check_terse_structure(text))
    return violations


def main(argv):
    voice, path = parse_args(argv)
    text = read_input(path)
    rules = read_rules()
    violations = lint(text, voice, rules)
    verdict = "violations" if violations else "clean"
    sys.stdout.write(
        json.dumps({"verdict": verdict, "violations": violations}) + "\n"
    )
    return EXIT_VIOLATIONS if violations else EXIT_CLEAN


if __name__ == "__main__":
    try:
        code = main(sys.argv[1:])
    except SystemExit as exc:
        code = EXIT_VIOLATIONS if exc.code == EXIT_VIOLATIONS else EXIT_CLEAN
    except BaseException as exc:  # a linter bug must never block a user
        sys.stderr.write("speakingwords lint error: %r\n" % (exc,))
        sys.stdout.write(
            json.dumps(
                {"verdict": "clean", "violations": [], "lint_error": str(exc)}
            )
            + "\n"
        )
        code = EXIT_CLEAN
    sys.exit(EXIT_VIOLATIONS if code == EXIT_VIOLATIONS else EXIT_CLEAN)
