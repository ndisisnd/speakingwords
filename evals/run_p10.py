#!/usr/bin/env python3
"""Deterministic evals for speakingwords Phase 10 (simple language / register).

No model calls, no network. SPEAKINGWORDS_HOME fakes the home directory, so
every install path runs inside a throwaway temp tree.

What is gated here
------------------
  A20  Every register row added in P10 — the eleven formal connectives in the
       strip table plus the `long-sentence` structural rule — ships at least one
       planted violation fixture and at least one clean control, and an
       uncovered row fails CI. The control half matters most: these rows are all
       ordinary English, so a false positive here is likelier than anywhere else
       in the lexicon, and a false positive bounces a good reply.

  P10  `long-sentence` behaviour. It fires on three long sentences and stays
       silent on one or two, at every voice and every conciseness level, and it
       counts nothing inside a code fence, a table, a quote, a heading or a
       bullet — the same exemption path `terse-prose-block` uses.

       Park-the-rule. A user whose domain needs "aforementioned" takes the row
       out with `speakingwords update "more aforementioned"`, keeps the .bak
       (A4), and the rest of the register rows keep firing.

  A1   The memory block states the register, keeps the P9 conciseness line and
       the voice line, and still fits the 9-line budget in all four targets.

  P11  `lang-function-over-inventory`. The rule is stated on all four surfaces
       (lexicon row, SKILL.md, memory template, SessionStart block) in the same
       words, and it is gated to `med` on every one of them. The re-scoped
       anti-loss invariant is checked to still name the things a reader cannot
       re-derive, so the scoping cannot be read as licence to drop real facts.

  E9   Runner scaffold. The prompt set, the two-axis judge rubric and the
       pass arithmetic are exercised here; the judged half costs model calls and
       is recorded at release — see `e9_report()` at the bottom of this file.

Usage:  python3 evals/run_p10.py
Exit:   0 all gates pass, 1 any gate fails.
"""

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
SKILL_MD = os.path.join(ROOT, "skill", "SKILL.md")
FIXTURES = os.path.join(HERE, "fixtures")
MANIFEST = os.path.join(FIXTURES, "manifest.json")

LEVELS = ("low", "med", "high")
VOICES = ("terse", "convo")

# The rows P10 adds. Listed rather than inferred, because A20 is a claim about
# the rows this phase introduced: the older strip rows are covered by E1's
# recall gate and predate the fixture-plus-control convention.
REGISTER_ROWS = [
    "strip-furthermore",
    "strip-moreover",
    "strip-thus",
    "strip-hence",
    "strip-nevertheless",
    "strip-aforementioned",
    "strip-whilst",
    "strip-it-should-be-noted",
    "strip-in-order-to",
    "strip-prior-to",
    "strip-subsequent-to",
]
LONG_SENTENCE = "long-sentence"

# The one register, written once. Every surface that states it must state it in
# these words, or the memory block and the rewrite skill drift apart.
REGISTER_PHRASE = "colleague in a Slack DM"

# E9 (plan §5): two axes, scored independently, both required. Simplicity bought
# with a lost fact is a failure, not a partial pass — which is why the rubric is
# two questions and not one blended score.
E9_AXES = ("register", "fidelity")
E9_RUBRIC = {
    "register": "Does this read like a Slack message from a colleague? "
                "Short sentences, everyday words, no essay connectives, no "
                "report grammar.",
    "fidelity": "Is the technical content fully intact? Every fact, number, "
                "file path, command and code block in the original still "
                "appears, unchanged in meaning. One thing is not a loss: an "
                "enumeration the reader can retrieve elsewhere, replaced by its "
                "function, its count and a pointer. A missing count or pointer "
                "is a loss, and so is anything the pointer does not carry.",
}
E9_FIRST_REPLY_GATE = 0.85
E9_POST_BOUNCE_GATE = 0.95
E9_MIN_PROMPTS = 20

# lang-function-over-inventory is the one language rule that is level-gated, so
# it is the one place the register axis is not the same question at every level.
# The addendum is appended to the register question at `med` and nowhere else.
E9_MED_REGISTER_ADDENDUM = (
    "At conciseness med the rule lang-function-over-inventory also applies: a "
    "completed-work report that reads out its parts instead of naming its "
    "function fails this axis. Function plus count plus a pointer passes; the "
    "roll call does not. At low and high the roll call is not a register fault."
)

# Keep lint.py importable in-process without leaving __pycache__ in the shipped
# tree, exactly as run_p9.py does.
sys.dont_write_bytecode = True
sys.path.insert(0, SCRIPTS)
import lint as lint_mod  # noqa: E402
import hook_session as session_mod  # noqa: E402

results = []
notes = []


def check(assertion, name, ok, detail=""):
    results.append((assertion, name, bool(ok), detail))


# ------------------------------------------------------------------ helpers


def run_cli(args, home, cwd, stdin=""):
    env = dict(os.environ, SPEAKINGWORDS_HOME=home)
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


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def load_manifest():
    with open(MANIFEST, "r", encoding="utf-8") as fh:
        return json.load(fh)


def lint_run(args, text):
    proc = subprocess.run(
        [sys.executable, LINT] + args, input=text, capture_output=True, text=True
    )
    try:
        payload = json.loads(proc.stdout)
    except ValueError:
        payload = None
    return proc.returncode, payload, proc.stderr


def fired(text, voice="convo", level="high", lexicon=None):
    """The set of rule ids a reply trips, in-process."""
    rules = lint_mod.read_rules(lexicon or LEXICON, use_cache=False)
    return {v["rule"] for v in lint_mod.lint(text, voice, rules, level)}


def strip_rows():
    """Every row of the `## Strip rules` table, straight from the lexicon.

    Read from the file rather than from a list in this runner, so a row edited
    by a later phase is covered the moment it lands.
    """
    text = read(LEXICON)
    section = lint_mod._section(text, lint_mod.STRIP_HEADING, LEXICON, required=True)
    rows = {}
    for cells in lint_mod._rows(section):
        if len(cells) < 4:
            continue
        rule_id = cells[0]
        if not rule_id or rule_id.startswith("#") or rule_id.lower() == "id":
            continue
        if set(rule_id) <= set("-: "):
            continue
        rows[rule_id] = {"pattern": cells[1], "severity": cells[2].lower(), "guidance": cells[3]}
    return rows


# --------------------------------------------------------- A20: row coverage


def eval_a20_coverage():
    manifest = load_manifest()
    rows = strip_rows()

    missing_rows = [r for r in REGISTER_ROWS if r not in rows]
    check("A20", "every register row is in the lexicon", not missing_rows,
          ", ".join(missing_rows))

    # `warn`, not `error`: each of these words has a legitimate use somewhere,
    # which is the whole reason the park-the-rule note exists.
    wrong_severity = [r for r in REGISTER_ROWS
                      if r in rows and rows[r]["severity"] != "warn"]
    check("A20", "every register row ships at warn severity", not wrong_severity,
          ", ".join(wrong_severity))

    planted = {}
    for fixture, rule_ids in manifest["violations"].items():
        for rule_id in rule_ids:
            planted.setdefault(rule_id, []).append(fixture)
    controls = manifest.get("controls", {})

    covered = REGISTER_ROWS + [LONG_SENTENCE]
    uncovered_violation = [r for r in covered if not planted.get(r)]
    uncovered_control = [r for r in covered if not controls.get(r)]
    check("A20", "every register row has a planted violation fixture",
          not uncovered_violation, ", ".join(uncovered_violation))
    check("A20", "every register row has a clean control",
          not uncovered_control, ", ".join(uncovered_control))

    # A fixture named in the manifest has to exist and, for a control, has to be
    # in the clean set — otherwise the coverage claim is bookkeeping, not proof.
    stray = []
    for rule_id in covered:
        for name in controls.get(rule_id, []):
            if name not in manifest["clean"]:
                stray.append("%s -> %s not in clean set" % (rule_id, name))
            if not os.path.isfile(os.path.join(FIXTURES, "clean", name)):
                stray.append("%s -> %s missing on disk" % (rule_id, name))
        for name in planted.get(rule_id, []):
            if not os.path.isfile(os.path.join(FIXTURES, "violations", name)):
                stray.append("%s -> %s missing on disk" % (rule_id, name))
    check("A20", "every named fixture exists where it claims to", not stray,
          "; ".join(stray[:6]))

    # Each planted fixture trips its row at every level and both voices: these
    # rows are register, and register does not move with the dial.
    misses = []
    for rule_id in covered:
        for fixture in planted.get(rule_id, []):
            text = read(os.path.join(FIXTURES, "violations", fixture))
            for voice in VOICES:
                for level in LEVELS:
                    if rule_id not in fired(text, voice, level):
                        misses.append("%s silent on %s @ %s/%s"
                                      % (rule_id, fixture, voice, level))
    check("A20", "every planted register fixture trips its row everywhere",
          not misses, "; ".join(misses[:6]))

    # And each control stays silent at every level. Controls are near misses on
    # purpose — "thus far", "in order,", "the prior run", "henceforth" — so this
    # is the check that the patterns are gated and not merely present.
    false_positives = []
    for rule_id in covered:
        for name in controls.get(rule_id, []):
            path = os.path.join(FIXTURES, "clean", name)
            for level in LEVELS:
                _, payload, _ = lint_run(["--conciseness", level, path], "")
                for violation in payload["violations"]:
                    false_positives.append("%s@%s -> %s" % (name, level, violation["rule"]))
    check("A20", "no register control fires a rule at any level",
          not false_positives, "; ".join(false_positives[:6]))

    # The park-the-rule promise is part of the row, not folklore: the rows that
    # expect a domain conflict have to say how to park themselves.
    unhelpful = [r for r in ("strip-aforementioned",)
                 if r in rows and "update" not in rows[r]["guidance"]]
    check("A20", "the park-the-rule note names the update command",
          not unhelpful, ", ".join(unhelpful))


# ------------------------------------------------------- P10: long-sentence


def sentence_of(word_count):
    """A single sentence of exactly `word_count` words, ending in a full stop."""
    return "The migration job " + " ".join(["waits"] * (word_count - 3)) + "."


def eval_long_sentence():
    long_one = sentence_of(40)
    short_one = sentence_of(10)

    for count, expected in ((0, False), (1, False), (2, False), (3, True), (5, True)):
        text = "\n\n".join([long_one] * count + [short_one])
        hit = LONG_SENTENCE in fired(text)
        check("P10", "%d long sentences %s" % (count, "fire" if expected else "stay silent"),
              hit == expected, text[:80])

    # The threshold itself, from both sides. 35 words is long-ish prose; 36 is
    # over the line. Getting this off by one moves the rule onto ordinary text.
    at_limit = "\n\n".join([sentence_of(lint_mod.LONG_SENTENCE_WORDS)] * 3)
    over_limit = "\n\n".join([sentence_of(lint_mod.LONG_SENTENCE_WORDS + 1)] * 3)
    check("P10", "three sentences at the word limit stay silent",
          LONG_SENTENCE not in fired(at_limit))
    check("P10", "three sentences one word over the limit fire",
          LONG_SENTENCE in fired(over_limit))

    # Voice- and level-independent: the register is the same everywhere, which
    # is what separates this rule from terse-prose-block.
    for voice in VOICES:
        for level in LEVELS:
            check("P10", "long-sentence fires at %s/%s" % (voice, level),
                  LONG_SENTENCE in fired(over_limit, voice, level))

    # One violation per offending sentence, so the rewrite pass knows which
    # sentences to split rather than being told a count.
    rules = lint_mod.read_rules(LEXICON, use_cache=False)
    listed = [v for v in lint_mod.lint(over_limit, "convo", rules, "high")
              if v["rule"] == LONG_SENTENCE]
    check("P10", "each long sentence is reported once", len(listed) == 3, str(len(listed)))
    check("P10", "long-sentence reports at warn severity",
          all(v["severity"] == "warn" for v in listed), str(listed[:1]))

    # Exemptions, one structure at a time. Each of these is three long lines in
    # a shape that is content rather than prose, and none of them may count.
    long_line = " ".join(["deploy"] * 45)
    exempt = {
        "a code fence": "```\n%s\n%s\n%s\n```\n" % (long_line, long_line, long_line),
        "a table": "| a | b |\n|---|---|\n| %s | x |\n| %s | y |\n| %s | z |\n"
                   % (long_line, long_line, long_line),
        "a quote": "> %s\n\n> %s\n\n> %s\n" % (long_line, long_line, long_line),
        "a bullet list": "- %s\n- %s\n- %s\n" % (long_line, long_line, long_line),
        "a heading run": "# %s\n\n## %s\n\n### %s\n" % (long_line, long_line, long_line),
    }
    for label, text in exempt.items():
        check("P10", "%s is exempt from long-sentence" % label,
              LONG_SENTENCE not in fired(text), text[:60])

    # The fixtures the manifest claims, end to end through the CLI.
    three = os.path.join(FIXTURES, "violations", "v67_long-sentences.txt")
    one = os.path.join(FIXTURES, "clean", "c67_one-long-sentence.txt")
    mixed = os.path.join(FIXTURES, "clean", "c68_long-in-fence-table-quote.txt")
    code, payload, _ = lint_run([three], "")
    check("P10", "the three-long-sentence fixture bounces",
          code == 2 and LONG_SENTENCE in {v["rule"] for v in payload["violations"]},
          json.dumps(payload)[:200])
    for label, path in (("one long sentence", one), ("long lines in fence/table/quote", mixed)):
        code, payload, _ = lint_run([path], "")
        check("P10", "%s stays clean" % label,
              code == 0 and not payload["violations"], json.dumps(payload)[:200])

    # The register exemplar in the lexicon has to agree with the rules: the
    # "before" half must fire, the "after" half must not. A worked example that
    # contradicts the linter teaches the rewrite pass the wrong lesson.
    before = ("Prior to the deployment it should be noted that the aforementioned "
              "migration must be applied, whilst the read replicas remain in a "
              "lagging state.")
    after = "Run the migration before you deploy. The read replicas are still lagging."
    check("P10", "the register exemplar's before half fires register rules",
          {"strip-prior-to", "strip-it-should-be-noted", "strip-aforementioned",
           "strip-whilst"} <= fired(before), sorted(fired(before)))
    check("P10", "the register exemplar's after half is clean",
          not fired(after), sorted(fired(after)))


# ----------------------------------------------------------- park the rule


def eval_park_the_rule():
    """`update "more aforementioned"` is the documented escape hatch (A20)."""
    home, project = make_home(), make_project()
    try:
        proc = run_cli(["init", "--hook", "--agent", "claude", "--scope", "local",
                        "--voice", "convo", "--conciseness", "med"], home, project)
        if proc.returncode != 0:
            check("P10", "hook install succeeds", False, proc.stderr.strip()[:200])
            return
        root = claude_root(home)
        installed = os.path.join(root, "refs", "lexicon.md")
        domain = "The aforementioned case law is cited in `docs/policy.md`.\n"

        check("P10", "the rule fires before it is parked",
              "strip-aforementioned" in fired(domain, lexicon=installed))

        parked = run_cli(["update", "more aforementioned"], home, project)
        check("P10", "`update \"more aforementioned\"` exits 0",
              parked.returncode == 0, parked.stderr[:200])
        check("P10", "the update names the row it removed",
              "strip-aforementioned" in parked.stdout, parked.stdout[:300])
        check("P10", "the lexicon edit takes a .bak first (A4)",
              os.path.isfile(installed + ".bak"), installed)
        check("P10", "the .bak still holds the row",
              "strip-aforementioned" in read(installed + ".bak"))

        after = lint_mod.read_rules(installed, use_cache=False)
        ids = {r[0] for r in after}
        check("P10", "the parked row is gone from the rule set",
              "strip-aforementioned" not in ids, sorted(ids)[:6])
        check("P10", "the parked reply now passes",
              not fired(domain, lexicon=installed), sorted(fired(domain, lexicon=installed)))

        # Parking one row is not parking the register. Everything else stands.
        survivors = [r for r in REGISTER_ROWS if r != "strip-aforementioned"]
        check("P10", "the other register rows survive the park",
              all(r in ids for r in survivors),
              ", ".join(r for r in survivors if r not in ids))
        check("P10", "a multiword row parks the same way",
              "strip-it-should-be-noted" in ids)

        multi = run_cli(["update", "more it should be noted"], home, project)
        ids_after = {r[0] for r in lint_mod.read_rules(installed, use_cache=False)}
        check("P10", "`update \"more it should be noted\"` removes the multiword row",
              multi.returncode == 0 and "strip-it-should-be-noted" not in ids_after,
              multi.stdout[:300])

        # A park is a rule-table edit, so it must not have invented a new one.
        strays = [r for r in ids_after if r.startswith("strip-user-")]
        check("P10", "parking a rule adds no rule", not strays, ", ".join(strays))
    finally:
        shutil.rmtree(home, ignore_errors=True)
        shutil.rmtree(project, ignore_errors=True)


# ------------------------------------------------------ A1: the memory block


MEMORY_TARGETS = [
    ("claude", "local", "CLAUDE.local.md"),
    ("claude", "global", os.path.join(".claude", "CLAUDE.md")),
    ("codex", "local", "AGENTS.md"),
    ("codex", "global", os.path.join(".codex", "AGENTS.md")),
]


def eval_memory_block():
    """The block states the register, keeps its P9 lines, and still fits (A1)."""
    for agent, scope, rel in MEMORY_TARGETS:
        home, project = make_home(), make_project()
        try:
            proc = run_cli(["init", "--memory", "--agent", agent, "--scope", scope,
                            "--voice", "convo", "--conciseness", "med"], home, project)
            base = project if scope == "local" else home
            target = os.path.join(base, rel)
            label = "%s/%s" % (agent, scope)
            if proc.returncode != 0 or not os.path.isfile(target):
                check("A1", "%s: memory block written" % label, False,
                      proc.stderr.strip()[:200])
                continue
            block = read(target).split("<!-- speakingwords:start -->")[1] \
                                .split("<!-- speakingwords:end -->")[0]
            bullets = [ln for ln in block.split("\n") if ln.startswith("- ")]
            check("A1", "%s: block stays inside the 9-line budget" % label,
                  len(bullets) <= 9, "%d lines" % len(bullets))
            check("A1", "%s: block states the register" % label,
                  any(REGISTER_PHRASE in ln for ln in bullets), block[:300])
            check("A1", "%s: block keeps the conciseness line" % label,
                  any(ln.startswith("- Conciseness is ") for ln in bullets), block[:300])
            check("A1", "%s: block keeps the voice line" % label,
                  any(ln.startswith("- Voice is ") for ln in bullets), block[:300])
            check("A1", "%s: block still lists the banned phrases" % label,
                  any("Never use these words" in ln for ln in bullets), block[:300])
            # These installs are `med`, the level lang-function-over-inventory is
            # active at, so the line has to be here and the block has to still fit.
            check("P11", "%s: block states the function-over-inventory rule" % label,
                  any(FUNCTION_PHRASE in ln for ln in bullets), block[:300])

            # The template is written in the register it asks for, so it has to
            # survive its own rules: no essay connectives, no long sentences.
            # The banned-phrase line is the one exception — it quotes the words
            # it bans, and quoting them is the point.
            prose = "\n".join(ln for ln in bullets if "Never use these words" not in ln)
            check("A1", "%s: the block obeys its own register" % label,
                  not fired(prose), sorted(fired(prose)))
        finally:
            shutil.rmtree(home, ignore_errors=True)
            shutil.rmtree(project, ignore_errors=True)


# --------------------------------------------------- one register, stated once


def eval_register_statement():
    lexicon = read(LEXICON)
    skill = read(SKILL_MD)

    check("P10", "the lexicon carries the lang-slack-register row",
          "lang-slack-register" in lexicon)
    check("P10", "the register row ships a before/after exemplar",
          "lang-slack-register" in lexicon
          and "Before:" in lexicon.split("lang-slack-register")[1].split("\n")[0]
          and "After:" in lexicon.split("lang-slack-register")[1].split("\n")[0])
    check("P10", "SKILL.md has a register section", "## Register" in skill)
    check("P10", "SKILL.md states the register once", skill.count(REGISTER_PHRASE) == 1,
          str(skill.count(REGISTER_PHRASE)))
    check("P10", "the lexicon states the register once",
          lexicon.count(REGISTER_PHRASE) == 1, str(lexicon.count(REGISTER_PHRASE)))
    check("P10", "SKILL.md tells the rewrite pass what long-sentence means",
          "long-sentence" in skill)
    check("P10", "the lexicon documents the long-sentence rule",
          "long-sentence" in lexicon)


# ------------------------------------------ P11: lang-function-over-inventory


FUNCTION_ROW = "lang-function-over-inventory"

# The one sentence every surface states, so the lexicon, the skill, the memory
# block and the session block cannot drift apart on what the rule asks for.
FUNCTION_PHRASE = "not the parts it is made of"

MEMORY_JS = os.path.join(ROOT, "lib", "memory.js")


def memory_bullets(voice, level):
    """The rendered memory block's bullet lines, straight from lib/memory.js."""
    script = (
        "const m = require(%s);"
        "const b = m.renderBlock({ voice: %s, conciseness: %s });"
        "process.stdout.write(JSON.stringify("
        "b.split('\\n').filter((l) => l.startsWith('- '))));"
    ) % (json.dumps(MEMORY_JS), json.dumps(voice), json.dumps(level))
    proc = subprocess.run(["node", "-e", script], capture_output=True, text=True)
    if proc.returncode != 0:
        return None
    try:
        return json.loads(proc.stdout)
    except ValueError:
        return None


def eval_function_over_inventory():
    """The rule is stated on every surface, and gated to `med` on every one."""
    lexicon = read(LEXICON)
    skill = read(SKILL_MD)

    # --- the lexicon row ---
    check("P11", "the lexicon carries the %s row" % FUNCTION_ROW,
          FUNCTION_ROW in lexicon)
    # The table row itself, not the sentence in the preamble that names the rule.
    row = next((ln for ln in lexicon.split("\n")
                if ln.startswith("| %s |" % FUNCTION_ROW)), "")
    check("P11", "the row ships a before/after exemplar",
          "Before:" in row and "After:" in row, row[:200])
    check("P11", "the row states the rule in the shared words",
          FUNCTION_PHRASE in row, row[:200])
    check("P11", "the row names its level, and names only med",
          "Active at: `med`" in row and "`low`" not in row and "`high`" not in row,
          row[:200])
    check("P11", "the row keeps the count and the pointer",
          "count" in row and "pointer" in row, row[:200])
    check("P11", "the language table explains how a row is level-gated",
          "Active at:" in lexicon.split("## Language rules")[1].split("|")[0],
          lexicon.split("## Language rules")[1][:400])

    # --- the re-scoped invariant in SKILL.md ---
    check("P11", "SKILL.md scopes what counts as a fact",
          "What counts as a fact" in skill)
    check("P11", "the scoping still protects the things a reader cannot re-derive",
          all(word in skill.split("What counts as a fact")[1][:900]
              for word in ("number", "path", "caveat", "code block", "re-derive")),
          skill.split("What counts as a fact")[1][:200])
    check("P11", "the scoping still requires the count and the pointer",
          "count" in skill.split("What counts as a fact")[1][:900]
          and "pointer" in skill.split("What counts as a fact")[1][:900])
    check("P11", "the rewrite procedure names the rule",
          FUNCTION_ROW in skill and FUNCTION_PHRASE in skill)
    check("P11", "SKILL.md ships an inventory-collapsing exemplar",
          "parts list instead of a result" in skill)
    check("P11", "the exemplar says the rule is med only",
          "`med` only" in skill or "at `med` only" in skill)

    # --- the memory template, every voice and every level (A1) ---
    for voice in VOICES:
        for level in LEVELS:
            bullets = memory_bullets(voice, level)
            label = "%s/%s" % (voice, level)
            if bullets is None:
                check("A1", "%s: memory block renders" % label, False, MEMORY_JS)
                continue
            check("A1", "%s: memory block stays inside the 9-line budget" % label,
                  len(bullets) <= 9, "%d lines" % len(bullets))
            stated = any(FUNCTION_PHRASE in ln for ln in bullets)
            check("P11", "%s: memory block states the rule only at med" % label,
                  stated == (level == "med"), "\n".join(bullets)[:300])
            if stated:
                # The template is written in the register it asks for.
                line = [ln for ln in bullets if FUNCTION_PHRASE in ln][0]
                check("P11", "%s: the rule line obeys its own register" % label,
                      not fired(line), sorted(fired(line)))
                check("P11", "%s: the rule line keeps count and pointer" % label,
                      "count" in line and "pointer" in line, line[:200])

    # --- the SessionStart block, in process ---
    for level in LEVELS:
        block = session_mod.build_block({"voice": "convo", "conciseness": level})
        stated = FUNCTION_PHRASE in block
        check("P11", "the session block states the rule only at med (%s)" % level,
              stated == (level == "med"), block[:400])
        check("P11", "the session block keeps the anti-loss line (%s)" % level,
              "Losing content is worse" in block, block[:400])
    med_block = session_mod.build_block({"voice": "convo", "conciseness": "med"})
    check("P11", "the session block still scopes itself to prose",
          "user-facing prose only" in med_block, med_block[:200])


# --------------------------------------------------------- E9 scaffolding


def e9_pass(scores):
    """A reply passes E9 only when both axes pass.

    Deliberately not an average. A reply that reads beautifully and drops a file
    path is a failure, and an average would score it as half a success.
    """
    return all(bool(scores.get(axis)) for axis in E9_AXES)


def e9_rate(judged):
    """Fraction of judged replies passing both axes."""
    return sum(1 for s in judged if e9_pass(s)) / float(len(judged)) if judged else 0.0


def e9_register_question(level):
    """The register question the judge is asked at a given level.

    One question everywhere, plus the med addendum. Written as a function so the
    level-gating lives in one place and cannot drift between the runner and the
    recorded run.
    """
    if level == "med":
        return E9_RUBRIC["register"] + " " + E9_MED_REGISTER_ADDENDUM
    return E9_RUBRIC["register"]


def stub_register_judge(reply, level):
    """A deterministic stand-in for the judge on the register axis.

    Same role as run_p9's stub_rewriter: it proves the level-gating plumbing, not
    the skill. It recognises exactly one shape — a single sentence reading out a
    long list of backticked names — and only at `med`.

    This heuristic deliberately lives here and nowhere else. It is not a lint
    rule and must never become one: a real reply can list five things for a good
    reason, and a false positive bounces a good reply, which is the worst failure
    this project has.
    """
    if level != "med":
        return True
    for sentence in reply.replace("\n", " ").split(". "):
        if sentence.count("`") >= 10:
            return False
    return True


def eval_e9_scaffold():
    manifest = load_manifest()
    prompts = manifest.get("e9_prompts", [])

    check("E9", "the prompt set has at least %d prompts" % E9_MIN_PROMPTS,
          len(prompts) >= E9_MIN_PROMPTS, str(len(prompts)))
    check("E9", "every prompt is a non-empty string",
          all(isinstance(p, str) and p.strip() for p in prompts))
    check("E9", "no prompt is duplicated", len(set(prompts)) == len(prompts),
          str(len(prompts) - len(set(prompts))))

    check("E9", "the rubric scores exactly two axes",
          tuple(sorted(E9_RUBRIC)) == tuple(sorted(E9_AXES)), ", ".join(sorted(E9_RUBRIC)))
    check("E9", "the register axis asks about a colleague's message",
          "Slack" in E9_RUBRIC["register"] and "colleague" in E9_RUBRIC["register"])
    check("E9", "the fidelity axis asks about facts, numbers and paths",
          all(word in E9_RUBRIC["fidelity"] for word in ("fact", "number", "path")))

    # The axes are independent and both are required, which is the entire point
    # of scoring them separately.
    check("E9", "both axes passing is a pass",
          e9_pass({"register": True, "fidelity": True}))
    check("E9", "register without fidelity is a failure",
          not e9_pass({"register": True, "fidelity": False}))
    check("E9", "fidelity without register is a failure",
          not e9_pass({"register": False, "fidelity": True}))
    check("E9", "a missing axis is a failure", not e9_pass({"register": True}))

    # Gate arithmetic, checked rather than trusted.
    judged = [{"register": True, "fidelity": True}] * 17 + \
             [{"register": True, "fidelity": False}] * 3
    check("E9", "the rate counts only replies passing both axes",
          abs(e9_rate(judged) - 0.85) < 1e-9, "%.3f" % e9_rate(judged))
    check("E9", "the first-reply gate is met at exactly 85%%",
          e9_rate(judged) >= E9_FIRST_REPLY_GATE)
    check("E9", "the post-bounce gate is stricter than the first-reply gate",
          E9_POST_BOUNCE_GATE > E9_FIRST_REPLY_GATE)

    # The half of E9 that needs no model: the deterministic register rules and
    # the judged register axis must not disagree. A reply written in the wrong
    # register trips the linter before a judge ever sees it.
    report = ("Furthermore, prior to the deploy it should be noted that the "
              "aforementioned migration must run, whilst the replicas lag.")
    message = "Run the migration before you deploy. The replicas are still lagging."
    check("E9", "a report-register reply is caught without a judge",
          len(fired(report)) >= 3, sorted(fired(report)))
    check("E9", "a message-register reply passes the deterministic half",
          not fired(message), sorted(fired(message)))
    # The register axis is level-aware in exactly one place: the med addendum for
    # lang-function-over-inventory. Everywhere else the question is identical, or
    # the axis would stop measuring one register.
    check("E9", "the register question gains the med addendum at med",
          E9_MED_REGISTER_ADDENDUM in e9_register_question("med"))
    for level in ("low", "high"):
        check("E9", "the register question is unchanged at %s" % level,
              e9_register_question(level) == E9_RUBRIC["register"])
    check("E9", "the med addendum names the rule it comes from",
          "lang-function-over-inventory" in E9_MED_REGISTER_ADDENDUM)

    # The fidelity axis has to agree with the re-scoped invariant, or the two
    # halves of the rubric would score the same rewrite in opposite directions.
    check("E9", "the fidelity axis still protects facts, numbers and paths",
          all(word in E9_RUBRIC["fidelity"] for word in ("fact", "number", "path")))
    check("E9", "the fidelity axis exempts a retrievable enumeration",
          "enumeration" in E9_RUBRIC["fidelity"])
    check("E9", "the fidelity axis still requires the count and the pointer",
          "count" in E9_RUBRIC["fidelity"] and "pointer" in E9_RUBRIC["fidelity"])

    # The gate this phase adds: the same reply is a register failure at med and a
    # pass at low, and the function-first version passes at both.
    roll_call = ("Added strip rules for `furthermore`, `moreover`, `thus`, "
                 "`hence`, `nevertheless`, `aforementioned` and `whilst`.")
    function_first = ("Formal essay connectives now bounce. 11 rules, each with a "
                      "near-miss control, in the strip table of "
                      "`skill/refs/lexicon.md`.")
    check("E9", "a roll-call report fails the register axis at med",
          not stub_register_judge(roll_call, "med"))
    for level in ("low", "high"):
        check("E9", "the same roll-call report passes at %s" % level,
              stub_register_judge(roll_call, level))
    for level in LEVELS:
        check("E9", "the function-first report passes at %s" % level,
              stub_register_judge(function_first, level))
    check("E9", "a med register failure fails the reply",
          not e9_pass({"register": stub_register_judge(roll_call, "med"),
                       "fidelity": True}))

    notes.append("E9 prompt set: %d prompts, judged on %s"
                 % (len(prompts), " + ".join(E9_AXES)))


def e9_report():
    """How the judged half of E9 gets recorded at release.

    Deliberately not run here: it costs model calls, and this file is part of
    `eval:deterministic`. The recorded run is:

      1. Run all 20 prompts in `manifest["e9_prompts"]` — the E3/E4 prompt set,
         written down here so E3, E4 and E9 measure the same ground — against an
         install in each voice, with the Stop hook live.
      2. Capture the first reply and, where the hook bounced it, the rewrite.
      3. Send each reply to the judge twice, once per axis, with E9_RUBRIC as the
         question and the original prompt as context. Never both axes in one
         call: a blended score hides a reply that reads well and lost a number.
      4. Score with e9_pass() and e9_rate(). The gates are ≥85% passing both
         axes on the first reply and ≥95% after one bounce.
      5. Paste both rates, the per-axis breakdown and the judge's model id into
         the release notes, as E2-E5 are recorded.

    Everything deterministic about that pipeline — the prompt set, the rubric,
    the two-axis arithmetic, and the linter half that catches report grammar
    before a judge is needed — is gated above and passes now.
    """
    return __doc__


# -------------------------------------------------------------------- main


def main():
    eval_a20_coverage()
    eval_long_sentence()
    eval_park_the_rule()
    eval_memory_block()
    eval_register_statement()
    eval_function_over_inventory()
    eval_e9_scaffold()

    try:
        os.unlink(lint_mod.cache_path(LEXICON))  # leave the repo tree as found
    except OSError:
        pass

    grouped = {}
    for assertion, name, ok, detail in results:
        grouped.setdefault(assertion, []).append((name, ok, detail))

    out = ["", "speakingwords — Phase 10 deterministic evals", ""]
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
    out.append("PHASE 10 PASS" if not failures else "PHASE 10 FAIL")
    out.append("")
    sys.stdout.write("\n".join(out))
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
