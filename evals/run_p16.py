#!/usr/bin/env python3
"""Deterministic evals for speakingwords Phase 16 (STE register).

No model calls, no network. SPEAKINGWORDS_HOME fakes the home directory, so
every install path runs inside a throwaway temp tree.

What is gated here
------------------
  A33  `lint.py --register` accepts slack|ste, and any other value — or the
       flag's absence entirely — behaves as `slack`. Exit codes stay inside the
       A6 contract (0 or 2, never anything else) and the linter never crashes,
       whatever it is handed. `slack` is the fallback because every install
       written before the register key existed was a Slack-register install:
       the fallback is the value that changes nothing for a user who asked for
       no change.
  A34  Every `## Register rules` row, and the structural `ste-long-sentence`
       check, ships at least one planted fixture and at least one clean
       control. `ste-contraction` ships a possessive control and a quoted-code
       control by name. An uncovered row fails CI, so the coverage claim is a
       measurement rather than bookkeeping.
  A30  The memory block at `register: ste` still renders inside the 9-line
       budget: the STE line REPLACES the Slack line rather than joining it.
       Every voice x level x register combination is rendered and counted.
  A35  A 0.2.0-shaped pref.json — no `register` key at all — works with every
       0.3.0 util unmodified, and the register readers all fall back to slack.

  P16  Plumbing and the two things a swap must never break:
       - slack is byte-identical to no flag at all across the whole E1 fixture
         set. The register is new behaviour for people who ask for it, and
         nothing at all for everyone else.
       - the cache cannot misapply a register rule. Cold, warm and corrupt
         caches produce identical verdicts at both registers (A21, E10
         discipline extended to the new column).
       - init asks a fifth question, `--register` and `--defaults` work, and
         `--defaults` is idempotent.
       - `update` hints move the register and re-render the block with it.

E11 (the judged STE run) costs model calls and is recorded at release, like E8,
E9 and E12. Nothing here calls a model.

Usage:  python3 evals/run_p16.py
Exit:   0 all gates pass, 1 any gate fails.
"""

import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CLI = os.path.join(ROOT, "bin", "speakingwords.js")
SCRIPTS = os.path.join(ROOT, "skill", "scripts")
LINT = os.path.join(SCRIPTS, "lint.py")
LEXICON = os.path.join(ROOT, "skill", "refs", "lexicon.md")
FIXTURES = os.path.join(HERE, "fixtures")
MANIFEST = os.path.join(FIXTURES, "manifest.json")
REGISTER_FIXTURES = os.path.join(FIXTURES, "register")

MODERN_CODEX = "0.124.0"
REGISTERS = ("slack", "ste")
LEVELS = ("low", "high")
VOICES = ("terse", "convo")
START_MARKER = "<!-- speakingwords:start -->"

# The structural check has no lexicon row of its own — it is applied by lint.py,
# like terse-prose-block — but A34's coverage claim is about every register rule
# a reply can be bounced for, so it is covered here alongside the table rows.
STRUCTURAL_REGISTER_RULES = ("ste-long-sentence",)

results = []
notes = []

sys.dont_write_bytecode = True  # keep __pycache__ out of the shipped tree
sys.path.insert(0, SCRIPTS)
import lint as lint_mod  # noqa: E402


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


stop_mod = load_module("speakingwords_hook_stop", os.path.join(SCRIPTS, "hook_stop.py"))
session_mod = load_module("speakingwords_hook_session", os.path.join(SCRIPTS, "hook_session.py"))


def check(assertion, name, ok, detail=""):
    results.append((assertion, name, bool(ok), detail))


# ------------------------------------------------------------------ helpers


def lint_run(args, stdin=""):
    """Run lint.py as the hook runs it: a real process, real exit code."""
    proc = subprocess.run(
        [sys.executable, LINT] + args, input=stdin, capture_output=True, text=True
    )
    try:
        payload = json.loads(proc.stdout)
    except ValueError:
        payload = None
    return proc.returncode, payload, proc.stderr


def run_cli(args, home, cwd, stdin=""):
    env = dict(os.environ, SPEAKINGWORDS_HOME=home, SPEAKINGWORDS_CODEX_VERSION=MODERN_CODEX)
    return subprocess.run(
        ["node", CLI] + args, cwd=cwd, env=env, input=stdin, capture_output=True, text=True
    )


def make_home():
    home = tempfile.mkdtemp(prefix="speakingwords-home-")
    os.makedirs(os.path.join(home, ".claude"), exist_ok=True)
    os.makedirs(os.path.join(home, ".codex"), exist_ok=True)
    return home


def make_project():
    return tempfile.mkdtemp(prefix="speakingwords-proj-")


def claude_root(home):
    return os.path.join(home, ".claude", "skills", "speakingwords")


def read(path):
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


def read_json(path):
    try:
        return json.loads(read(path))
    except (OSError, ValueError):
        return None


def load_manifest():
    return json.loads(read(MANIFEST))


def snapshot(root):
    out = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        for name in sorted(filenames):
            full = os.path.join(dirpath, name)
            with open(full, "rb") as fh:
                out[os.path.relpath(full, root)] = hashlib.sha256(fh.read()).hexdigest()
    return out


def diff_trees(before, after):
    return [k for k in sorted(set(before) | set(after)) if before.get(k) != after.get(k)]


def register_rows():
    """The (id, registers) pairs of the `## Register rules` table.

    Read out of the lexicon rather than listed here, so adding a row to the
    table adds it to this gate at the same time (A34).
    """
    text = read(LEXICON)
    section = lint_mod._section(text, lint_mod.REGISTER_HEADING, LEXICON, required=False)
    rows = []
    for cells in lint_mod._rows(section or ""):
        if len(cells) < 5:
            continue
        rule_id = cells[0]
        if not rule_id or rule_id.startswith("#") or rule_id.lower() == "id":
            continue
        if set(rule_id) <= set("-: "):
            continue
        rows.append((rule_id, lint_mod.parse_registers(cells[3])))
    return rows


# ------------------------------------------------ A33: the register flag itself


# Everything a user, a script or a corrupted pref.json could plausibly hand the
# flag. None of these may crash, and every one that is not a register must land
# on `slack`.
BAD_REGISTERS = [
    "", "simplified", "STE100", "asd-ste100", "bogus", "0", "-1", "slack,ste",
    "--voice", "'; rm -rf /", "stê", "\t", "null", "true", "med",
]

# Not exactly a register, but unambiguously one. Whitespace and casing arrive
# routinely from shell variables and hand-edited pref files, and reading such a
# value as `slack` would silently undo a register the user did choose.
TOLERATED_REGISTERS = {
    "STE": "ste", "Ste": "ste", "ste\n": "ste", " ste ": "ste",
    "SLACK": "slack", "Slack\n": "slack",
}

REGISTER_PROBE = "We can't ship this today. The pump's seal is fine.\n"


def eval_a33_flag():
    codes = set()

    for register in REGISTERS:
        code, payload, err = lint_run(["--register", register], REGISTER_PROBE)
        codes.add(code)
        check("A33", "--register %s is accepted" % register,
              payload is not None and payload.get("register") == register,
              "%r / %s" % (payload, err[:120]))

    for value in BAD_REGISTERS:
        code, payload, err = lint_run(["--register", value], REGISTER_PROBE)
        codes.add(code)
        check("A33", "%r falls back to slack" % value,
              payload is not None and payload.get("register") == "slack",
              "%r / %s" % (payload, err[:120]))
        check("A33", "%r does not crash the linter" % value,
              payload is not None and "lint_error" not in payload, err[:160])

    for value, expected in TOLERATED_REGISTERS.items():
        code, payload, err = lint_run(["--register", value], REGISTER_PROBE)
        codes.add(code)
        check("A33", "%r normalises to %s" % (value, expected),
              payload is not None and payload.get("register") == expected,
              "%r / %s" % (payload, err[:120]))

    code, payload, _ = lint_run([], REGISTER_PROBE)
    codes.add(code)
    check("A33", "the flag's absence behaves as slack",
          payload is not None and payload.get("register") == "slack", "%r" % payload)

    code, payload, err = lint_run(["--register"], REGISTER_PROBE)
    codes.add(code)
    check("A33", "a trailing --register with no value behaves as slack",
          payload is not None and payload.get("register") == "slack",
          "%r / %s" % (payload, err[:120]))

    code, payload, _ = lint_run(["--register=ste"], REGISTER_PROBE)
    codes.add(code)
    check("A33", "--register=ste is accepted",
          payload is not None and payload.get("register") == "ste", "%r" % payload)

    check("A33", "exit codes stay inside the A6 contract",
          codes <= {0, 2}, "observed %s" % sorted(codes))

    # The flag has to actually gate the rule set, or it is decoration.
    _, slack, _ = lint_run(["--register", "slack"], REGISTER_PROBE)
    _, ste, _ = lint_run(["--register", "ste"], REGISTER_PROBE)
    check("A33", "a contraction bounces at ste and passes at slack",
          "ste-contraction" in {v["rule"] for v in ste["violations"]}
          and not slack["violations"],
          json.dumps({"slack": slack, "ste": ste})[:300])

    # The two axes do not interfere: the register never changes which strip
    # rules or which conciseness rows fire.
    banned = "Landed the fix.\n"
    fired = []
    for register in REGISTERS:
        _, payload, _ = lint_run(["--register", register], banned)
        fired.append({v["rule"] for v in payload["violations"]} == {"strip-landed"})
    check("A33", "strip rules fire identically at both registers", all(fired), str(fired))

    padding = "In other words, we are done.\n"
    for register in REGISTERS:
        _, high, _ = lint_run(["--register", register, "--conciseness", "high"], padding)
        _, low, _ = lint_run(["--register", register, "--conciseness", "low"], padding)
        check("A33", "[%s] the conciseness dial still works" % register,
              "conc-in-other-words" in {v["rule"] for v in high["violations"]}
              and not low["violations"],
              json.dumps({"high": high, "low": low})[:200])

    # Every reader of a register agrees on the shipped set and the fallback.
    # They restate it rather than import it — lint.py is loaded by the hook only
    # after the preference has been read — so the agreement is checked here.
    check("A33", "lint.py ships exactly the two registers",
          lint_mod.REGISTERS == ("slack", "ste"), str(lint_mod.REGISTERS))
    check("A33", "hook_stop.py agrees with lint.py on the register set",
          tuple(stop_mod.REGISTERS) == lint_mod.REGISTERS, str(stop_mod.REGISTERS))
    for value, expected in (("ste", "ste"), ("STE", "ste"), (" ste ", "ste"),
                            ("slack", "slack"), ("bogus", "slack"), (None, "slack")):
        check("A33", "hook_stop reads %r as %s" % (value, expected),
              stop_mod.read_register({"register": value}) == expected,
              stop_mod.read_register({"register": value}))
        check("A33", "lint.normalise_register reads %r as %s" % (value, expected),
              lint_mod.normalise_register(value) == expected,
              lint_mod.normalise_register(value))
    check("A33", "hook_stop reads a missing key as slack",
          stop_mod.read_register({}) == "slack")
    check("A33", "the injector reads a missing key as slack",
          session_mod.register_of({}) == "slack")
    check("A33", "the injector states the STE rules at ste",
          "contractions" in session_mod.build_block({"register": "ste"})
          and "Slack DM" not in session_mod.build_block({"register": "ste"}),
          session_mod.build_block({"register": "ste"})[:200])


# ------------------------------------------------------- A34: row coverage


def eval_a34_coverage():
    manifest = load_manifest()
    rows = register_rows()
    check("A34", "the lexicon has a register table to cover", len(rows) > 0, str(len(rows)))

    coverage = manifest.get("register") or {}
    planted = coverage.get("planted") or {}
    controls = coverage.get("controls") or {}

    covered = [r for r, _ in rows] + list(STRUCTURAL_REGISTER_RULES)
    missing_planted = [r for r in covered if not planted.get(r)]
    missing_control = [r for r in covered if not controls.get(r)]
    check("A34", "every register rule has a planted fixture",
          not missing_planted, ", ".join(missing_planted))
    check("A34", "every register rule has a clean control",
          not missing_control, ", ".join(missing_control))

    # Named fixtures have to exist, or the coverage claim is bookkeeping.
    stray = []
    for group in (planted, controls):
        for rule_id, names in group.items():
            for name in names:
                if not os.path.isfile(os.path.join(REGISTER_FIXTURES, name)):
                    stray.append("%s -> %s" % (rule_id, name))
    check("A34", "every named fixture exists on disk", not stray, ", ".join(stray))

    # The two controls the assertion names by hand. A possessive is the failure
    # mode a bare-apostrophe pattern would have, and quoted code is the one a
    # register rule reading raw text would have.
    contraction_controls = " ".join(controls.get("ste-contraction", []))
    check("A34", "ste-contraction ships a possessive control",
          "possessive" in contraction_controls, contraction_controls)
    check("A34", "ste-contraction ships a quoted-code control",
          "code" in contraction_controls, contraction_controls)

    # Each planted fixture must actually trip its rule at ste...
    misses = []
    for rule_id, names in planted.items():
        for name in names:
            path = os.path.join(REGISTER_FIXTURES, name)
            _, payload, _ = lint_run(["--register", "ste", "--voice", "convo", path])
            if rule_id not in {v["rule"] for v in payload["violations"]}:
                misses.append("%s did not fire on %s" % (rule_id, name))
    check("A34", "every planted fixture trips its rule at ste", not misses, "; ".join(misses))

    # ...and must stay silent at slack, or the register is not a swap.
    leaks = []
    for rule_id, names in planted.items():
        for name in names:
            path = os.path.join(REGISTER_FIXTURES, name)
            _, payload, _ = lint_run(["--register", "slack", "--voice", "convo", path])
            for violation in payload["violations"]:
                leaks.append("%s -> %s at slack" % (name, violation["rule"]))
    check("A34", "no planted register fixture fires at slack", not leaks, "; ".join(leaks))

    # Controls stay clean at every register and every level. A false positive
    # bounces a good reply, which is still the worst failure class.
    false_positives = []
    for rule_id, names in controls.items():
        for name in names:
            path = os.path.join(REGISTER_FIXTURES, name)
            for register in REGISTERS:
                for level in LEVELS:
                    _, payload, _ = lint_run(
                        ["--register", register, "--conciseness", level,
                         "--voice", "convo", path]
                    )
                    for violation in payload["violations"]:
                        false_positives.append(
                            "%s@%s/%s -> %s" % (name, register, level, violation["rule"])
                        )
    check("A34", "no control fires a rule at any register or level",
          not false_positives, "; ".join(false_positives[:6]))

    # The cap is a boundary, so both sides of it are measured: 26 words bounces,
    # 25 does not. An off-by-one here is the difference between a rule and a
    # nuisance.
    _, over, _ = lint_run(["--register", "ste", "--voice", "convo",
                           os.path.join(REGISTER_FIXTURES, "r03_long-sentence-26.txt")])
    _, at_cap, _ = lint_run(["--register", "ste", "--voice", "convo",
                             os.path.join(REGISTER_FIXTURES, "rc03_long-sentence-25.txt")])
    check("A34", "26 words bounces at ste",
          {v["rule"] for v in over["violations"]} == {"ste-long-sentence"}, json.dumps(over))
    check("A34", "25 words is clean at ste", not at_cap["violations"], json.dumps(at_cap))
    check("A34", "the cap is the documented 25 words",
          lint_mod.STE_SENTENCE_WORDS == 25, str(lint_mod.STE_SENTENCE_WORDS))

    # And the Slack heuristic is genuinely off at ste: a reply with three
    # 36-word sentences must report ste-long-sentence, never long-sentence.
    essay = " ".join(["word"] * 35 + ["end."]) + "\n"
    _, ste_payload, _ = lint_run(["--register", "ste", "--voice", "convo"], essay * 3)
    _, slack_payload, _ = lint_run(["--register", "slack", "--voice", "convo"], essay * 3)
    check("A34", "ste reports ste-long-sentence, not long-sentence",
          {v["rule"] for v in ste_payload["violations"]} == {"ste-long-sentence"},
          json.dumps(ste_payload)[:200])
    check("A34", "slack still reports long-sentence",
          {v["rule"] for v in slack_payload["violations"]} == {"long-sentence"},
          json.dumps(slack_payload)[:200])


# --------------------------------------------- P16: slack is byte-identical


def eval_slack_unchanged():
    """The whole E1 set, linted with and without the flag, verdict for verdict.

    This is the promise the register makes to everyone who does not want it:
    nothing changed. It is checked as byte equality of the JSON payload, not as
    a count, so a reordered violation list would fail too.
    """
    manifest = load_manifest()
    names = ([("violations", n) for n in sorted(manifest["violations"])]
             + [("clean", n) for n in sorted(manifest["clean"])])
    drift = []
    for folder, name in names:
        path = os.path.join(FIXTURES, folder, name)
        _, bare, _ = lint_run(["--voice", "convo", path])
        _, flagged, _ = lint_run(["--voice", "convo", "--register", "slack", path])
        if bare != flagged:
            drift.append(name)
    check("P16", "--register slack is byte-identical to no flag across the E1 set",
          not drift, ", ".join(drift[:6]))
    check("P16", "the E1 set is actually being compared", len(names) > 100, str(len(names)))

    # The clean controls stay clean at ste as well, wherever they can. A clean
    # control that bounces at ste for a contraction is expected and allowed —
    # that is the register doing its job — but nothing else may fire.
    unexpected = []
    for name in sorted(manifest["clean"]):
        path = os.path.join(FIXTURES, "clean", name)
        _, payload, _ = lint_run(["--voice", "convo", "--register", "ste", path])
        for violation in payload["violations"]:
            if violation["rule"] not in ("ste-contraction", "ste-long-sentence"):
                unexpected.append("%s -> %s" % (name, violation["rule"]))
    check("P16", "the clean set fires nothing but register rules at ste",
          not unexpected, ", ".join(unexpected[:6]))


def eval_cache_cannot_lie():
    """Cold, warm and corrupt caches produce identical verdicts (A21, E10).

    The cache grew a register column in this phase, which is exactly the kind of
    change that can make a stale sidecar file misapply a rule. The gate is the
    same one E10 set: no cache state may change a verdict.
    """
    tmp = tempfile.mkdtemp(prefix="speakingwords-cache-")
    try:
        target = os.path.join(tmp, "lexicon.md")
        shutil.copyfile(LEXICON, target)
        cache = lint_mod.cache_path(target)

        verdicts = {}
        for state in ("cold", "warm"):
            rules = lint_mod.read_rules(target)
            verdicts[state] = {
                register: lint_mod.lint(REGISTER_PROBE, "convo", rules, "high", register)
                for register in REGISTERS
            }
        check("P16", "a warm cache matches a cold parse at both registers",
              verdicts["cold"] == verdicts["warm"], json.dumps(str(verdicts))[:200])
        check("P16", "the cache file was actually written", os.path.exists(cache), cache)

        # A v3 cache — the shape this phase replaced — must miss, not be read.
        stale = json.loads(read(cache))
        stale["key"]["version"] = 3
        with open(cache, "w", encoding="utf-8") as fh:
            json.dump(stale, fh)
        rules = lint_mod.read_rules(target)
        verdicts["stale"] = {
            register: lint_mod.lint(REGISTER_PROBE, "convo", rules, "high", register)
            for register in REGISTERS
        }
        check("P16", "a previous-version cache is reparsed, not misread",
              verdicts["stale"] == verdicts["cold"], json.dumps(str(verdicts["stale"]))[:200])

        # Garbage bytes, and a cache claiming the register rows fire at slack.
        with open(cache, "w", encoding="utf-8") as fh:
            fh.write("{not json at all")
        rules = lint_mod.read_rules(target)
        corrupt = {
            register: lint_mod.lint(REGISTER_PROBE, "convo", rules, "high", register)
            for register in REGISTERS
        }
        check("P16", "a corrupt cache is reparsed, not misread",
              corrupt == verdicts["cold"], json.dumps(str(corrupt))[:200])

        # A cache that claims the register rows fire everywhere. The key still
        # matches, so this file IS trusted — which is exactly why the lexicon's
        # mtime is part of the key.
        lint_mod.read_rules(target)  # rewrite a good cache over the garbage
        poisoned = json.loads(read(cache))
        for row in poisoned["rules"]:
            if row[0].startswith("ste-"):
                row[4] = ["slack", "ste"]
        with open(cache, "w", encoding="utf-8") as fh:
            json.dump(poisoned, fh)
        os.utime(target, None)
        rules = lint_mod.read_rules(target)
        after = lint_mod.lint(REGISTER_PROBE, "convo", rules, "high", "slack")
        check("P16", "a poisoned cache cannot survive a lexicon touch",
              after == verdicts["cold"]["slack"], json.dumps(str(after))[:200])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# --------------------------------------------------- A30: the block budget


def eval_a30_block_budget():
    """Every voice x level x register combination, rendered and counted."""
    script = (
        "const m = require('%s');\n"
        "const out = [];\n"
        "for (const voice of ['terse','convo'])\n"
        "  for (const level of ['low','high'])\n"
        "    for (const register of ['slack','ste',undefined,'bogus']) {\n"
        "      const b = m.renderBlock({ voice, conciseness: level, register });\n"
        "      out.push({ voice, level, register: String(register),\n"
        "                 bullets: m.countBullets(b), block: b });\n"
        "    }\n"
        "process.stdout.write(JSON.stringify(out));\n"
    ) % os.path.join(ROOT, "lib", "memory.js")
    proc = subprocess.run(["node", "-e", script], capture_output=True, text=True)
    check("A30", "every combination renders without throwing", proc.returncode == 0,
          proc.stderr.strip()[:300])
    if proc.returncode != 0:
        return
    rendered = json.loads(proc.stdout)
    check("A30", "all 16 combinations were rendered", len(rendered) == 16, str(len(rendered)))

    over = [r for r in rendered if r["bullets"] > 9]
    check("A30", "no combination exceeds the 9-line budget", not over,
          ", ".join("%s/%s/%s=%d" % (r["voice"], r["level"], r["register"], r["bullets"])
                    for r in over))

    # The swap, stated as arithmetic: the same voice and level render the same
    # number of lines at either register. A line appended instead of swapped
    # would show up here as 9 vs 10 at low, and as a throw at high.
    counts = {}
    for r in rendered:
        counts.setdefault((r["voice"], r["level"]), {})[r["register"]] = r["bullets"]
    mismatched = [k for k, v in counts.items() if len(set(v.values())) != 1]
    check("A30", "the register never changes the line count", not mismatched,
          str(mismatched))

    for r in rendered:
        block = r["block"]
        if r["register"] == "ste":
            check("A30", "[%s/%s] ste renders the STE line" % (r["voice"], r["level"]),
                  "Simplified Technical English" in block, block[:160])
            check("A30", "[%s/%s] ste drops the Slack line" % (r["voice"], r["level"]),
                  "Slack DM" not in block, block[:160])
        else:
            check("A30", "[%s/%s/%s] slack renders the Slack line"
                  % (r["voice"], r["level"], r["register"]),
                  "Slack DM" in block and "Simplified Technical English" not in block,
                  block[:160])

    # The STE line has to obey its own rule, or the block teaches by
    # counter-example on every reply.
    ste_line = [r for r in rendered if r["register"] == "ste"][0]["block"]
    line = [l for l in ste_line.split("\n") if "Simplified Technical English" in l][0]
    _, payload, _ = lint_run(["--register", "ste", "--voice", "convo"], line.lstrip("- ") + "\n")
    check("A30", "the STE block line is itself clean at ste",
          payload is not None and not payload["violations"], json.dumps(payload)[:200])


# ------------------------------------------ A35: a 0.2.0 pref, untouched


def eval_a35_forward_compat():
    """A pref.json with no register key runs every util unmodified."""
    home, proj = make_home(), make_project()
    try:
        # Install, then rewrite pref.json into the shape 0.2.0 wrote: no
        # register key at all, plus a key from a version that does not exist.
        # `both` mode, because that is the one whose status header prints even
        # with an empty hit log — the register has to show up there.
        run_cli(["init", "--both", "--agent", "claude", "--scope", "global",
                 "--voice", "terse", "--conciseness", "low"], home, proj)
        pref_path = os.path.join(claude_root(home), "pref.json")
        record = read_json(pref_path)
        record.pop("register", None)
        record["from_a_later_version"] = {"register": "klingon"}
        with open(pref_path, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(record, indent=2) + "\n")

        check("A35", "the fixture pref really has no register key",
              "register" not in read_json(pref_path), read(pref_path))

        proc = run_cli(["status"], home, proj)
        check("A35", "status runs on a 0.2.0 pref", proc.returncode == 0, proc.stderr[:200])
        check("A35", "status reports the fallback register",
              "slack register" in proc.stdout, proc.stdout[:200])
        check("A35", "status never invents a register the pref does not carry",
              "ste register" not in proc.stdout, proc.stdout[:200])

        proc = run_cli(["update", "less flimflam"], home, proj)
        check("A35", "update runs on a 0.2.0 pref", proc.returncode == 0, proc.stderr[:200])

        after = read_json(pref_path) or {}
        check("A35", "the unknown key survives", after.get("from_a_later_version"),
              json.dumps(after))
        check("A35", "voice and conciseness survive",
              after.get("voice") == "terse" and after.get("conciseness") == "low",
              json.dumps(after))

        # The hook path: the Stop hook has to lint a reply against slack when
        # the key is absent, not crash and not pick ste.
        installed_pref = os.path.join(claude_root(home), "pref.json")
        check("A35", "the installed pref is where the hook reads it",
              os.path.exists(installed_pref), installed_pref)
        check("A35", "hook_stop falls back to slack on this pref",
              stop_mod.read_register(read_json(installed_pref)) == "slack",
              stop_mod.read_register(read_json(installed_pref)))

        # And a register move on that pref adds the key without disturbing the
        # rest, which is what makes the upgrade a non-event.
        proc = run_cli(["update", "simplified technical english"], home, proj)
        check("A35", "update can set the register on a 0.2.0 pref",
              proc.returncode == 0 and "Register is now ste" in proc.stdout,
              proc.stdout[:200] + proc.stderr[:200])
        after = read_json(pref_path) or {}
        check("A35", "the register key lands last, everything else intact",
              after.get("register") == "ste"
              and after.get("voice") == "terse"
              and after.get("from_a_later_version") == {"register": "klingon"},
              json.dumps(after))
    finally:
        shutil.rmtree(home, ignore_errors=True)
        shutil.rmtree(proj, ignore_errors=True)


# ------------------------------------------------------------- P16 plumbing


def eval_p16_init():
    """The fifth question, the flag, and `--defaults`."""
    home, proj = make_home(), make_project()
    try:
        proc = run_cli(
            ["init", "--memory", "--agent", "claude", "--scope", "local",
             "--voice", "terse", "--conciseness", "high", "--register", "ste"],
            home, proj,
        )
        check("P16", "--register ste is accepted", proc.returncode == 0, proc.stderr[:200])
        check("P16", "init names the register it installed",
              "ste register" in proc.stdout, proc.stdout[:200])
        pref = read_json(os.path.join(claude_root(home), "pref.json")) or {}
        check("P16", "pref.json records the register", pref.get("register") == "ste",
              json.dumps(pref))
        check("P16", "the block carries the STE line",
              "Simplified Technical English" in read(os.path.join(proj, "CLAUDE.local.md")),
              proj)

        proc = run_cli(
            ["init", "--memory", "--agent", "claude", "--scope", "local",
             "--voice", "terse", "--register", "klingon"],
            home, proj,
        )
        check("P16", "a bad --register exits 1", proc.returncode == 1, proc.stdout)
        check("P16", "a bad --register names the two registers",
              "slack" in proc.stderr and "ste" in proc.stderr, proc.stderr[:200])
        check("P16", "a bad --register writes nothing to stdout", proc.stdout == "", proc.stdout)

        # A fully specified 0.2.0 command line — no register at all — still
        # installs, and lands on slack. That is the upgrade path (A35).
        home2 = make_home()
        proc = run_cli(
            ["init", "--memory", "--agent", "claude", "--scope", "local",
             "--voice", "terse", "--conciseness", "high"],
            home2, proj,
        )
        pref = read_json(os.path.join(claude_root(home2), "pref.json")) or {}
        check("P16", "a 0.2.0-shaped command line still installs",
              proc.returncode == 0 and pref.get("register") == "slack", json.dumps(pref))
        shutil.rmtree(home2, ignore_errors=True)

        proc = run_cli(["help", "init"], home, proj)
        check("P16", "help init lists --register", "--register" in proc.stdout, proc.stdout[:400])
        check("P16", "help init lists --defaults", "--defaults" in proc.stdout, proc.stdout[:400])
        proc = run_cli(["help"], home, proj)
        check("P16", "the overview says five questions",
              "five questions" in proc.stdout, proc.stdout[-300:])
    finally:
        shutil.rmtree(home, ignore_errors=True)
        shutil.rmtree(proj, ignore_errors=True)


def eval_p16_defaults():
    """`init --defaults` asks nothing, and running it twice changes nothing."""
    home, proj = make_home(), make_project()
    try:
        # No stdin at all: a prompt here would hang or fail, which is the point.
        proc = run_cli(["init", "--defaults"], home, proj)
        check("P16", "init --defaults exits 0", proc.returncode == 0, proc.stderr[:300])
        check("P16", "init --defaults asks nothing",
              "Choose 1-" not in proc.stdout, proc.stdout[:200])

        pref = read_json(os.path.join(claude_root(home), "pref.json")) or {}
        check("P16", "init --defaults takes every default",
              pref.get("mode") == "memory" and pref.get("voice") == "terse"
              and pref.get("conciseness") == "high" and pref.get("register") == "slack"
              and pref.get("scope") == "local",
              json.dumps(pref))

        before = snapshot(home)
        proc = run_cli(["init", "--defaults"], home, proj)
        moved = diff_trees(before, snapshot(home))
        check("P16", "init --defaults is idempotent",
              proc.returncode == 0 and not moved, ", ".join(moved))

        # Flags win, defaults fill the rest.
        home2 = make_home()
        proc = run_cli(["init", "--defaults", "--voice", "convo", "--register", "ste"],
                       home2, proj)
        pref = read_json(os.path.join(claude_root(home2), "pref.json")) or {}
        check("P16", "a flag beside --defaults wins",
              proc.returncode == 0 and pref.get("voice") == "convo"
              and pref.get("register") == "ste" and pref.get("conciseness") == "high",
              json.dumps(pref) + proc.stderr[:200])
        shutil.rmtree(home2, ignore_errors=True)
    finally:
        shutil.rmtree(home, ignore_errors=True)
        shutil.rmtree(proj, ignore_errors=True)


def eval_p16_update():
    """`update` moves the register and re-renders the block in the same pass."""
    home, proj = make_home(), make_project()
    try:
        run_cli(["init", "--memory", "--agent", "claude", "--scope", "local",
                 "--voice", "convo", "--conciseness", "low"], home, proj)
        block_path = os.path.join(proj, "CLAUDE.local.md")
        pref_path = os.path.join(claude_root(home), "pref.json")

        proc = run_cli(["update", "simplified technical english"], home, proj)
        check("P16", "an ste hint exits 0", proc.returncode == 0, proc.stderr[:200])
        check("P16", "an ste hint says what changed",
              "Register is now ste (was slack)" in proc.stdout, proc.stdout[:300])
        check("P16", "an ste hint records the register",
              (read_json(pref_path) or {}).get("register") == "ste", read(pref_path))
        check("P16", "an ste hint re-renders the block",
              "Simplified Technical English" in read(block_path), block_path)
        check("P16", "the level is left alone",
              (read_json(pref_path) or {}).get("conciseness") == "low", read(pref_path))
        # A4: every touched file was backed up first.
        check("P16", "an ste hint backs up pref.json", os.path.exists(pref_path + ".bak"))
        check("P16", "an ste hint backs up the memory file", os.path.exists(block_path + ".bak"))

        proc = run_cli(["update", "back to slack register"], home, proj)
        check("P16", "a slack hint moves it back",
              "Register is now slack (was ste)" in proc.stdout, proc.stdout[:300])
        check("P16", "the block goes back to the Slack line",
              "Slack DM" in read(block_path)
              and "Simplified Technical English" not in read(block_path), block_path)

        proc = run_cli(["update", "slack register"], home, proj)
        check("P16", "a no-op register hint says so and exits 0",
              proc.returncode == 0 and "already slack" in proc.stdout, proc.stdout[:200])

        # A hint that means a rule change must still mean a rule change: "ste"
        # is checked first, so nothing else may be swallowed by it.
        proc = run_cli(["update", "less flimflam"], home, proj)
        check("P16", "an ordinary ban hint is unaffected",
              proc.returncode == 0 and "strip-user-flimflam" in proc.stdout,
              proc.stdout[:200])
    finally:
        shutil.rmtree(home, ignore_errors=True)
        shutil.rmtree(proj, ignore_errors=True)


def eval_p16_no_dictionary():
    """The copyright boundary, checked as a property of the tree.

    Nothing here may ship an approved-word list, and every user-facing mention
    of the standard has to say inspired, not conformant.
    """
    docs = {
        "README.md": read(os.path.join(ROOT, "README.md")),
        "skill/SKILL.md": read(os.path.join(ROOT, "skill", "SKILL.md")),
        "skill/refs/lexicon.md": read(LEXICON),
    }
    for name, text in docs.items():
        lowered = text.lower()
        if "ste" not in lowered:
            continue
        mentions = "asd-ste100" in lowered or "simplified technical english" in lowered
        if not mentions:
            continue
        check("P16", "[%s] says inspired, never conformant" % name,
              "inspired" in lowered and "conformant ste" not in lowered.replace("not conformant ste", ""),
              name)
    check("P16", "the README links the free specification",
          "asd-ste100.org" in docs["README.md"], "missing link")
    shipped = []
    for folder in ("bin", "lib", "skill", "evals"):
        for dirpath, _, files in os.walk(os.path.join(ROOT, folder)):
            for name in files:
                if "approved" in name.lower() or "dictionary" in name.lower():
                    shipped.append(os.path.join(dirpath, name))
    check("P16", "no approved-word dictionary file exists in the shipped tree",
          not shipped, ", ".join(shipped))


# -------------------------------------------------------------------- main


def main():
    eval_a33_flag()
    eval_a34_coverage()
    eval_a30_block_budget()
    eval_a35_forward_compat()
    eval_slack_unchanged()
    eval_cache_cannot_lie()
    eval_p16_init()
    eval_p16_defaults()
    eval_p16_update()
    eval_p16_no_dictionary()

    notes.append("E11 (judged STE rewrite) needs model calls and is recorded at release.")

    grouped = {}
    for assertion, name, ok, detail in results:
        grouped.setdefault(assertion, []).append((name, ok, detail))

    out = ["", "speakingwords — Phase 16 deterministic evals", ""]
    for assertion in sorted(grouped):
        group = grouped[assertion]
        failed = [(n, d) for n, ok, d in group if not ok]
        out.append("%-4s %s  (%d/%d)" % (
            assertion, "PASS" if not failed else "FAIL",
            len(group) - len(failed), len(group)))
        for name, detail in failed:
            out.append("       FAILED: %s%s" % (name, (" — %s" % detail) if detail else ""))

    if notes:
        out.append("")
        for note in notes:
            out.append("  %s" % note)

    failures = [r for r in results if not r[2]]
    out.append("")
    out.append("%d/%d checks passed" % (len(results) - len(failures), len(results)))
    out.append("PHASE 16 PASS" if not failures else "PHASE 16 FAIL")
    out.append("")
    sys.stdout.write("\n".join(out))
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
