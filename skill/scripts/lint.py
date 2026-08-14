#!/usr/bin/env python3
"""speakingwords deterministic lint pass.

Reads an agent reply from stdin or a file argument, scans it against the strip
rules in ../refs/lexicon.md, adds two structural checks — one voice-dependent
(terse-prose-block), one not (long-sentence) — and prints a single JSON object
on stdout.

Contract (plan assertion A6): exit 0 on clean, exit 2 on violations, never any
other code. An internal error is never allowed to block a user's reply, so a
crash is reported on stderr and reported as clean with a "lint_error" field.

Usage:
    python3 lint.py --voice terse reply.txt
    cat reply.txt | python3 lint.py --voice convo --conciseness med

--conciseness takes low, med or high. Anything else, and the flag's absence,
behaves as high (plan assertion A19): only an upgrade from 0.1.0 can leave the
level unset, and 0.1.0 behaviour already measured in the high band, so high is
the value that preserves what the user already had.
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

# Sidecar cache of the compiled-rule listing, written beside the lexicon and
# keyed by its mtime and size (plan §2 W4.1). It is a latency device, never a
# source of truth: a missing, stale, corrupt or unreadable cache falls straight
# back to a full reparse, so no cache state can change a verdict (A21, E10).
CACHE_SUFFIX = ".cache.json"
# Bumped to 2 in v0.2.0: rule records grew a level set, so a v1 cache written by
# an older install describes a different shape. The version rides in the cache
# key, so an old file simply misses and is reparsed — never misread.
CACHE_VERSION = 2

STRIP_HEADING = re.compile(r"^##\s+Strip rules\s*$", re.MULTILINE)
CONCISENESS_HEADING = re.compile(r"^##\s+Conciseness rules\s*$", re.MULTILINE)
NEXT_HEADING = re.compile(r"^##\s+", re.MULTILINE)

# The conciseness dial (plan §2 W2). Voice says what shape a reply takes; the
# level says how much of it survives.
LEVELS = ("low", "med", "high")
DEFAULT_LEVEL = "high"
# Strip rules are level-independent: a banned phrase is banned at every level.
ALL_LEVELS = frozenset(LEVELS)

# Lines that are structurally not prose: bullets, numbered items, headings,
# quotes, table rows, fence markers, horizontal rules.
NON_PROSE_LINE = re.compile(r"^\s*(?:[-*+•]\s|\d+[.)]\s|#{1,6}\s|>|\||```|~~~|---)")
SENTENCE_SPLIT = re.compile(r"(?<=[.!?])[\s]+")
FENCE = re.compile(r"^\s*(?:```|~~~)")

# Terse allowance: a block may carry up to this many prose sentences before it
# counts as a paragraph-form answer. Two is enough for a one-line lead-in plus a
# qualifier; three or more is prose.
TERSE_SENTENCE_ALLOWANCE = 2

# Register check (plan §2 W3). A sentence over LONG_SENTENCE_WORDS words is long;
# LONG_SENTENCE_ALLOWANCE of them is still style, and the third one is the drift.
# Both numbers are deliberately generous: one long sentence is how people write,
# a pattern of them is essay grammar, and a false positive bounces a good reply.
LONG_SENTENCE_WORDS = 35
LONG_SENTENCE_ALLOWANCE = 2


class LexiconError(Exception):
    pass


def normalise_level(value):
    """Coerce anything at all into one of the three levels.

    Absence and nonsense both land on DEFAULT_LEVEL rather than raising (A19).
    A bad level is a reason to lint at the level the user already had, never a
    reason to crash and take the reply down with it.
    """
    if not isinstance(value, str):
        return DEFAULT_LEVEL
    candidate = value.strip().lower()
    return candidate if candidate in LEVELS else DEFAULT_LEVEL


def parse_levels(cell):
    """Read an `active at` cell into the set of levels a row fires at.

    An unreadable or empty cell yields {high} only. A rule nobody can place
    belongs at the strictest level, not at every level: silently widening a
    rule's reach is how a false positive reaches a `low` user.
    """
    found = {token for token in re.split(r"[,\s/]+", (cell or "").lower()) if token in LEVELS}
    return frozenset(found) if found else frozenset((DEFAULT_LEVEL,))


def _section(text, heading, path, required):
    """Slice the markdown between a `## <heading>` line and the next `##`."""
    start = heading.search(text)
    if not start:
        if required:
            raise LexiconError("no '## Strip rules' section in %s" % path)
        return None
    rest = text[start.end():]
    nxt = NEXT_HEADING.search(rest)
    return rest[: nxt.start()] if nxt else rest


def _rows(section):
    """Yield the cell lists of every table row in a section.

    Cells split on an escaped pipe inside a pattern are rebuilt here, so both
    tables read `\\|` the same way.
    """
    for line in section.splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        merged = []
        for cell in cells:
            if merged and merged[-1].endswith("\\"):
                merged[-1] = merged[-1] + "|" + cell
            else:
                merged.append(cell)
        yield merged


def _parse_table(section, path, levels_column, seen):
    """Compile one rule table into (rule_id, pattern, severity, levels) tuples.

    `levels_column` is the index of the `active at` cell, or None for a table
    whose rows fire at every level (the strip table: a banned phrase is banned
    regardless of how much text the user wants to survive).
    """
    width = 4 if levels_column is None else levels_column + 2
    rules = []
    for cells in _rows(section):
        if len(cells) < width:
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
        levels = ALL_LEVELS if levels_column is None else parse_levels(cells[levels_column])
        rules.append((rule_id, compiled, severity, levels))
    return rules


def parse_rules(path=LEXICON_PATH):
    """Parse the rule tables out of lexicon.md.

    The lexicon is the single source of truth; nothing is hardcoded here.
    Returns a list of (rule_id, compiled_pattern, severity, levels), where
    `levels` is the frozenset of conciseness levels the row fires at. Strip
    rules carry all three; conciseness rows carry whatever their `active at`
    column says.

    The conciseness section is optional. A 0.1.0 lexicon that predates it still
    parses, and still lints exactly as it always did.
    """
    try:
        with open(path, "r", encoding="utf-8") as fh:
            text = fh.read()
    except OSError as exc:
        raise LexiconError("cannot read lexicon at %s: %s" % (path, exc))

    seen = set()
    rules = _parse_table(
        _section(text, STRIP_HEADING, path, required=True), path, None, seen
    )
    if not rules:
        raise LexiconError("strip-rule table in %s is empty" % path)

    conciseness = _section(text, CONCISENESS_HEADING, path, required=False)
    if conciseness:
        rules.extend(_parse_table(conciseness, path, 3, seen))
    return rules


# ------------------------------------------------------------- rule cache


def cache_path(path):
    """Sidecar cache file for a lexicon, hidden beside the lexicon itself."""
    directory, name = os.path.split(os.path.abspath(path))
    return os.path.join(directory, "." + name + CACHE_SUFFIX)


def cache_key(path):
    """What the cache is keyed on: the lexicon's mtime and size."""
    st = os.stat(path)
    return {"version": CACHE_VERSION, "mtime_ns": st.st_mtime_ns, "size": st.st_size}


def read_cache(path):
    """Return the cached rule listing, or None when it cannot be trusted.

    Every failure mode — no cache, unreadable cache, garbage bytes, a key that
    no longer matches the lexicon on disk, a pattern that no longer compiles —
    returns None, which the caller reads as "reparse from source". The cache is
    never allowed to be the reason a rule is missing or extra (A21).
    """
    try:
        with open(cache_path(path), "r", encoding="utf-8") as fh:
            payload = json.load(fh)
        if not isinstance(payload, dict) or payload.get("key") != cache_key(path):
            return None
        rules = []
        for rule_id, pattern, severity, levels in payload["rules"]:
            rules.append((
                rule_id,
                re.compile(pattern, re.IGNORECASE | re.MULTILINE),
                severity,
                frozenset(levels),
            ))
        return rules or None
    except Exception:
        return None


def write_cache(path, rules):
    """Write the compiled-rule listing beside the lexicon, atomically.

    Temp file then rename, so a crash mid-write leaves the old cache or the new
    one, never a torn one (A22). An unwritable directory is not an error: the
    linter simply keeps reparsing.
    """
    target = cache_path(path)
    tmp = "%s.%d.tmp" % (target, os.getpid())
    payload = {
        "key": cache_key(path),
        # Levels are written as a sorted list so the same lexicon always
        # produces byte-identical cache bytes.
        "rules": [
            [rule_id, pattern.pattern, severity, sorted(levels)]
            for rule_id, pattern, severity, levels in rules
        ],
    }
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(payload, fh)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, target)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def read_rules(path=LEXICON_PATH, use_cache=True):
    """Rules for a lexicon, from the sidecar cache when it is current.

    Same return value as parse_rules() in every cache state; the cache only
    decides how much work it took to get there.
    """
    if use_cache:
        cached = read_cache(path)
        if cached is not None:
            return cached
    rules = parse_rules(path)
    if use_cache:
        write_cache(path, rules)
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
    for rule_id, pattern, severity, _levels in rules:
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
    for joined in prose_blocks(text):
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


def prose_blocks(text):
    """Blank-line-separated blocks, reduced to their prose lines only.

    The exemption path both structural checks share: fenced code is blanked
    out first, then every line that is structurally not prose (bullet, number,
    heading, quote, table row) is dropped. What comes back is the running text
    a reader actually reads as sentences.
    """
    for block in re.split(r"\n\s*\n", strip_code_fences(text)):
        prose_lines = [
            ln.strip()
            for ln in block.splitlines()
            if ln.strip() and not NON_PROSE_LINE.match(ln)
        ]
        if prose_lines:
            yield " ".join(prose_lines)


def check_long_sentences(text):
    """Flag essay grammar: three or more very long sentences in one reply.

    Voice-independent, unlike check_terse_structure — the Slack register is the
    same in terse and convo, because it is about sentence construction, not
    about whether the answer is shaped as bullets.

    The count is taken across the whole reply, not per block: three long
    sentences spread over three paragraphs is the same drift as three in a row.
    Nothing is reported below the threshold, so a reply with one or two long
    sentences comes back clean.
    """
    long_sentences = []
    for joined in prose_blocks(text):
        for sentence in SENTENCE_SPLIT.split(joined):
            sentence = sentence.strip()
            if len(sentence.split()) > LONG_SENTENCE_WORDS:
                long_sentences.append(sentence)

    if len(long_sentences) <= LONG_SENTENCE_ALLOWANCE:
        return []
    # Every offending sentence is listed, capped like a strip rule, so the
    # rewrite pass knows which ones to split rather than guessing.
    return [
        {
            "rule": "long-sentence",
            "match": sentence[:MATCH_SNIPPET_CHARS],
            "severity": "warn",
        }
        for sentence in long_sentences[:MAX_MATCHES_PER_RULE]
    ]


def read_input(argv_path):
    if argv_path:
        with open(argv_path, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read()
    return sys.stdin.read()


def parse_args(argv):
    voice = "convo"
    conciseness = DEFAULT_LEVEL
    path = None
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--voice":
            i += 1
            voice = argv[i] if i < len(argv) else "convo"
        elif arg.startswith("--voice="):
            voice = arg.split("=", 1)[1]
        elif arg == "--conciseness":
            # A trailing `--conciseness` with no value is treated as no value at
            # all, which is DEFAULT_LEVEL — the same answer a bad value gets.
            i += 1
            conciseness = argv[i] if i < len(argv) else DEFAULT_LEVEL
        elif arg.startswith("--conciseness="):
            conciseness = arg.split("=", 1)[1]
        elif arg in ("-h", "--help"):
            sys.stderr.write(__doc__)
            raise SystemExit(EXIT_CLEAN)
        elif not arg.startswith("-"):
            path = arg
        i += 1
    if voice not in ("terse", "convo"):
        voice = "convo"
    return voice, normalise_level(conciseness), path


def lint(text, voice, rules, conciseness=DEFAULT_LEVEL):
    """Every violation in `text` at this voice and this conciseness level.

    The level filters the rule set before the scan: a row only fires when its
    `active at` column names the level in play. Strip rules carry all three
    levels, so they fire whatever the dial says — the level governs how much
    padding survives, never whether a banned phrase is allowed back in.
    """
    level = normalise_level(conciseness)
    active = [rule for rule in rules if level in rule[3]]
    violations = scan_strip_rules(text, active)
    # Register is voice-independent and level-independent: a colleague in a DM
    # writes short sentences whatever shape the answer takes and however much
    # of it survives.
    violations.extend(check_long_sentences(text))
    if voice == "terse":
        violations.extend(check_terse_structure(text))
    return violations


def main(argv):
    voice, conciseness, path = parse_args(argv)
    text = read_input(path)
    rules = read_rules()
    violations = lint(text, voice, rules, conciseness)
    verdict = "violations" if violations else "clean"
    # The level is echoed back so a caller can see which one actually applied
    # after the fallback, rather than guessing at what it asked for.
    sys.stdout.write(
        json.dumps({
            "verdict": verdict,
            "violations": violations,
            "conciseness": normalise_level(conciseness),
        }) + "\n"
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
