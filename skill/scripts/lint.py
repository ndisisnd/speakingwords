#!/usr/bin/env python3
"""speakingwords deterministic lint pass.

Reads an agent reply from stdin or a file argument, scans it against the strip
rules in ../refs/lexicon.md, adds two structural checks — one voice-dependent
(terse-prose-block), one not (a sentence-length check, which one depending on
the register) — and prints a single JSON object on stdout.

Contract (plan assertion A6): exit 0 on clean, exit 2 on violations, never any
other code. An internal error is never allowed to block a user's reply, so a
crash is reported on stderr and reported as clean with a "lint_error" field.

Usage:
    python3 lint.py --voice terse reply.txt
    cat reply.txt | python3 lint.py --voice convo --conciseness high
    python3 lint.py --register ste reply.txt

--register takes slack or ste. `ste` is the register inspired by ASD-STE100
Simplified Technical English: it turns on the register rows of the lexicon and
swaps the Slack register's 3x35-word `long-sentence` heuristic for a flat
25-word cap on every sentence. Anything else, and the flag's absence, behaves as
`slack` (plan assertion A33): every install that predates the register key was a
Slack-register install, so `slack` is the value that preserves what the user
already had. Nothing about `ste` is conformant STE — the writing rules are
implemented, the approved-word dictionary is not shipped and never will be.

--conciseness takes low or high. `med`, the third position the dial carried
during 0.2.0 development, is still recognised and reads as high — that is the
level med's behaviour and band became. Anything else, and the flag's absence,
also behaves as high (plan assertion A19): only an upgrade from 0.1.0 can leave
the level unset, 0.1.0 behaviour already measured in the high band, and high is
the most aggressive shipped level, so it is the value that preserves what the
user already had.
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
# an older install describes a different shape. Bumped to 3 in P14: the level
# set itself changed membership when the dial dropped to two positions, so a v2
# cache describes rows as firing at levels that no longer exist. Bumped to 4 in
# v0.3.0: every record grew a register set, and a v3 record read as a v4 one
# would place register rows at the wrong register — the exact failure the
# version guard exists to prevent. The version rides in the cache key, so an old
# file simply misses and is reparsed — never misread.
CACHE_VERSION = 4

STRIP_HEADING = re.compile(r"^##\s+Strip rules\s*$", re.MULTILINE)
CONCISENESS_HEADING = re.compile(r"^##\s+Conciseness rules\s*$", re.MULTILINE)
REGISTER_HEADING = re.compile(r"^##\s+Register rules\s*$", re.MULTILINE)
NEXT_HEADING = re.compile(r"^##\s+", re.MULTILINE)

# The conciseness dial (plan §2 W2). Voice says what shape a reply takes; the
# level says how much of it survives. Two positions ship: the rig2 recording
# proved `low` and the old `med`, and `med`'s behaviour and band were promoted
# to become `high` (P14).
LEVELS = ("low", "high")
# Level values a reader still understands but no longer ships, mapped to what
# they became. A 0.2.0-dev pref.json, an old script or a legacy lexicon cell
# that says `med` is read as `high` rather than rejected.
LEGACY_LEVELS = {"med": "high"}
DEFAULT_LEVEL = "high"
# Strip rules are level-independent: a banned phrase is banned at every level.
ALL_LEVELS = frozenset(LEVELS)

# The register axis (plan v0.3.0 W5). Voice says what shape a reply takes, the
# level says how much of it survives, the register says how the sentences are
# built. `ste` is inspired by ASD-STE100 Simplified Technical English and
# implements its writing rules only; the approved-word dictionary is ASD's
# copyright and is not shipped, reproduced or approximated anywhere in this
# tree. `slack` is the register every install behaved as before this key
# existed, which is why it is both the default and the fallback (A33).
REGISTERS = ("slack", "ste")
DEFAULT_REGISTER = "slack"
# Strip, conciseness and language rules are register-neutral: the register
# governs how sentences are built, never which words are banned.
ALL_REGISTERS = frozenset(REGISTERS)

# The STE sentence cap. ASD-STE100 caps procedural sentences at 20 words and
# descriptive ones at 25; nothing here can tell a procedure from a description,
# so the looser number applies to both (plan §8, resolved). One conservative cap
# beats a classifier that would bounce good replies on a guess.
STE_SENTENCE_WORDS = 25

# Inline code spans. Register rows read the reply with its code taken out, so a
# rule about sentence construction never fires on a command or a quoted snippet.
INLINE_CODE = re.compile(r"`[^`\n]*`")

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
    """Coerce anything at all into one of the two shipped levels.

    A legacy value normalises to what it became (`med` -> `high`). Absence and
    nonsense both land on DEFAULT_LEVEL rather than raising (A19) — one fallback
    value everywhere. A bad level is a reason to lint at the level the user
    already had, never a reason to crash and take the reply down with it.
    """
    if not isinstance(value, str):
        return DEFAULT_LEVEL
    candidate = value.strip().lower()
    if candidate in LEVELS:
        return candidate
    return LEGACY_LEVELS.get(candidate, DEFAULT_LEVEL)


def normalise_register(value):
    """Coerce anything at all into one of the two shipped registers.

    Absence and nonsense both land on DEFAULT_REGISTER rather than raising
    (A33), exactly as normalise_level() lands on DEFAULT_LEVEL. The fallback is
    `slack` because every install written before the register key existed was a
    Slack-register install: a bad value is a reason to lint at the register the
    user already had, never a reason to change their register for them.
    """
    if not isinstance(value, str):
        return DEFAULT_REGISTER
    candidate = value.strip().lower()
    return candidate if candidate in REGISTERS else DEFAULT_REGISTER


def parse_registers(cell):
    """Read an `active at register` cell into the set of registers a row fires at.

    An unreadable or empty cell yields {ste} only, and the asymmetry with
    parse_levels() is deliberate. A rule nobody can place must not reach the
    register every existing install runs, because that would change behaviour
    nobody asked to change. It goes to the newer, stricter register instead.
    """
    found = set()
    for token in re.split(r"[,\s/]+", (cell or "").lower()):
        if token in REGISTERS:
            found.add(token)
    return frozenset(found) if found else frozenset(("ste",))


def parse_levels(cell):
    """Read an `active at` cell into the set of levels a row fires at.

    A legacy level name in the cell resolves to the level it became, so a
    lexicon written against the three-position dial still places its rows where
    they belong instead of falling through to the default.

    An unreadable or empty cell yields {high} only. A rule nobody can place
    belongs at the strictest level, not at every level: silently widening a
    rule's reach is how a false positive reaches a `low` user.
    """
    found = set()
    for token in re.split(r"[,\s/]+", (cell or "").lower()):
        if token in LEVELS:
            found.add(token)
        elif token in LEGACY_LEVELS:
            found.add(LEGACY_LEVELS[token])
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


def _parse_table(section, path, levels_column, seen, registers_column=None):
    """Compile one table into (id, pattern, severity, levels, registers, code_exempt).

    `levels_column` is the index of the `active at` cell, or None for a table
    whose rows fire at every level (the strip table: a banned phrase is banned
    regardless of how much text the user wants to survive).

    `registers_column` is the index of the `active at register` cell, or None
    for a table whose rows fire at every register (strip and conciseness rows:
    the register governs sentence construction, not vocabulary).
    """
    gated = [c for c in (levels_column, registers_column) if c is not None]
    width = 4 if not gated else max(gated) + 2
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
        registers = (ALL_REGISTERS if registers_column is None
                     else parse_registers(cells[registers_column]))
        # Code exemption is a property of the table a row came from, not of the
        # registers it happens to name. A register row would still read the
        # code-free text if someone wrote `slack, ste` in its cell.
        rules.append((rule_id, compiled, severity, levels, registers,
                      registers_column is not None))
    return rules


def parse_rules(path=LEXICON_PATH):
    """Parse the rule tables out of lexicon.md.

    The lexicon is the single source of truth; nothing is hardcoded here.
    Returns a list of (rule_id, compiled_pattern, severity, levels, registers,
    code_exempt), where `levels` is the frozenset of conciseness levels the row fires at and
    `registers` the frozenset of registers. Strip rules carry every level and
    every register; conciseness rows carry whatever their `active at` column
    says, at every register; register rows carry every level, at whatever their
    `active at register` column says.

    Both gated sections are optional. A 0.1.0 lexicon that predates the
    conciseness table, or a 0.2.0 one that predates the register table, still
    parses and still lints exactly as it always did.
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

    register = _section(text, REGISTER_HEADING, path, required=False)
    if register:
        rules.extend(_parse_table(register, path, None, seen, registers_column=3))
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
        for rule_id, pattern, severity, levels, registers, code_exempt in payload["rules"]:
            rules.append((
                rule_id,
                re.compile(pattern, re.IGNORECASE | re.MULTILINE),
                severity,
                frozenset(levels),
                frozenset(registers),
                bool(code_exempt),
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
        # Levels and registers are written as sorted lists so the same lexicon
        # always produces byte-identical cache bytes.
        "rules": [
            [rule_id, pattern.pattern, severity, sorted(levels), sorted(registers),
             bool(code_exempt)]
            for rule_id, pattern, severity, levels, registers, code_exempt in rules
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


def strip_code(text):
    """Blank out fenced blocks and inline backtick spans.

    The reading register rows get: a rule about how sentences are written must
    never fire on a command, a path or a quoted snippet, because that text is
    content the user has to keep verbatim. Strip rules do not use this path —
    a banned word is banned wherever it appears.
    """
    return INLINE_CODE.sub(" ", strip_code_fences(text))


def scan_strip_rules(text, rules):
    violations = []
    total = 0
    for rule_id, pattern, severity, _levels, _registers, _exempt in rules:
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


def check_ste_long_sentences(text):
    """Flag every prose sentence over the STE cap. The `ste` register only.

    This replaces check_long_sentences() rather than joining it. The Slack
    register asks whether the reply reads like an essay, so it counts a pattern
    of long sentences and forgives one or two. STE asks whether each individual
    sentence can be read once and acted on, so there is no allowance: one
    sentence over the cap is one violation, reported by itself.

    Exemptions come through the same prose_blocks() path both other structural
    checks use, so code fences, tables, quotes, headings and bullets are never
    counted. A bullet is exempt as a line, not as content — the cap applies to
    the prose the reader reads as sentences.
    """
    violations = []
    for joined in prose_blocks(text):
        for sentence in SENTENCE_SPLIT.split(joined):
            sentence = sentence.strip()
            if len(sentence.split()) > STE_SENTENCE_WORDS:
                violations.append({
                    "rule": "ste-long-sentence",
                    "match": sentence[:MATCH_SNIPPET_CHARS],
                    "severity": "warn",
                })
            if len(violations) >= MAX_MATCHES_PER_RULE:
                return violations
    return violations


def read_input(argv_path):
    if argv_path:
        with open(argv_path, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read()
    return sys.stdin.read()


def parse_args(argv):
    voice = "convo"
    conciseness = DEFAULT_LEVEL
    register = DEFAULT_REGISTER
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
        elif arg == "--register":
            # A trailing `--register` with no value is treated as no value at
            # all, which is DEFAULT_REGISTER — the same answer a bad value gets.
            i += 1
            register = argv[i] if i < len(argv) else DEFAULT_REGISTER
        elif arg.startswith("--register="):
            register = arg.split("=", 1)[1]
        elif arg in ("-h", "--help"):
            sys.stderr.write(__doc__)
            raise SystemExit(EXIT_CLEAN)
        elif not arg.startswith("-"):
            path = arg
        i += 1
    if voice not in ("terse", "convo"):
        voice = "convo"
    return voice, normalise_level(conciseness), normalise_register(register), path


def lint(text, voice, rules, conciseness=DEFAULT_LEVEL, register=DEFAULT_REGISTER):
    """Every violation in `text` at this voice, level and register.

    The level and the register both filter the rule set before the scan: a row
    only fires when its `active at` column names the level in play and its
    `active at register` column names the register. Strip rules carry every
    level and every register, so they fire whatever the dial and the register
    say — those axes govern how much padding survives and how sentences are
    built, never whether a banned phrase is allowed back in.

    The two sentence-length checks are a swap, not a stack. Exactly one runs:
    the Slack register's essay-grammar heuristic at `slack`, the flat 25-word
    cap at `ste`. Running both would report the same sentence twice and give
    the rewrite two different targets to hit.
    """
    level = normalise_level(conciseness)
    reg = normalise_register(register)
    active = [rule for rule in rules if level in rule[3] and reg in rule[4]]
    register_rows = [rule for rule in active if rule[5]]
    plain_rows = [rule for rule in active if not rule[5]]

    violations = scan_strip_rules(text, plain_rows)
    # Register rows read the reply with its code taken out, so a contraction in
    # a shell command is never a violation.
    if register_rows:
        violations.extend(scan_strip_rules(strip_code(text), register_rows))

    # Sentence length is voice-independent and level-independent: a register
    # decides how sentences are built whatever shape the answer takes and
    # however much of it survives.
    violations.extend(
        check_ste_long_sentences(text) if reg == "ste" else check_long_sentences(text)
    )
    if voice == "terse":
        violations.extend(check_terse_structure(text))
    return violations


def main(argv):
    voice, conciseness, register, path = parse_args(argv)
    text = read_input(path)
    rules = read_rules()
    violations = lint(text, voice, rules, conciseness, register)
    verdict = "violations" if violations else "clean"
    # The level and the register are echoed back so a caller can see which ones
    # actually applied after the fallback, rather than guessing at what it asked
    # for.
    sys.stdout.write(
        json.dumps({
            "verdict": verdict,
            "violations": violations,
            "conciseness": normalise_level(conciseness),
            "register": normalise_register(register),
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
