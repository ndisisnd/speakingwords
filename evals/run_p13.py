#!/usr/bin/env python3
"""Deterministic evals for speakingwords Phase 13 (patch1 W5–W8).

No model calls, no network. Everything here is arithmetic on the shipped
contract text plus the harness helper the recording driver imports.

What is gated here
------------------
  A27  A convo rewrite introduces no bullet list the original did not have, at
       every level. The v0.2.0 run passed its "convo stays prose" gate vacuously
       — the driver proxied the question through lint.py's `terse-prose-block`
       rule, which by construction cannot fire on a bullet list. The comparison
       now lives in evals/harness_checks.py, is exercised here against
       bullet-introducing, faithful and bullet-dropping rewrites, and is what
       the next recording driver must call.
  A28  SKILL.md states the band as a two-sided obligation — under the floor is
       named as the same failure as over the ceiling — and the rewrite procedure
       opens with the word-budget computation. The per-level exemplar is checked
       arithmetically: every level's rewrite lands inside the band computed from
       its own "Before", and the failure case lands under the floor.
  A29  The fact definition names causal and purpose links, and the five losses
       the v0.2.0 run recorded are replayed as fixtures: each one's dropped
       clause is classified a loss by the shipped rubric's own wording.

  P13  W7 surfaces: SKILL.md's register section carries the three recorded
       report-grammar tells with a rewrite each, the `## Do not` list names the
       two the linter cannot catch, and hook_session.py's register line names
       the same three, so prevention and enforcement cannot drift.

Usage:  python3 evals/run_p13.py
Exit:   0 all gates pass, 1 any gate fails.
"""

import json
import math
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SCRIPTS = os.path.join(ROOT, "skill", "scripts")
SKILL_MD = os.path.join(ROOT, "skill", "SKILL.md")
HOOK_SESSION = os.path.join(SCRIPTS, "hook_session.py")
FIXTURES = os.path.join(HERE, "fixtures")
RECORD = os.path.join(HERE, "records", "e8-e9-v0.2.0.json")

# Same bands as run_p9.py's E8_BANDS, restated rather than imported: run_p9
# builds a temp home on import-time paths, and this file must stay cheap. The
# first check below fails loudly if the two ever disagree.
E8_BANDS = {"low": (0.10, 0.20), "med": (0.25, 0.35), "high": (0.40, 0.50)}
LEVELS = ("low", "med", "high")

# run_p10 pins this string to appear exactly once in SKILL.md. The register
# counter-exemplar added in W7 must not restate it — checked below.
REGISTER_PHRASE = "colleague in a Slack DM"

sys.dont_write_bytecode = True
sys.path.insert(0, HERE)
import harness_checks as hc  # noqa: E402

results = []
notes = []


def check(assertion, name, ok, detail=""):
    results.append((assertion, name, bool(ok), detail))


def read(path):
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


def words(text):
    return len(text.split())


def flat(text):
    """Whitespace-collapsed, so a pin does not depend on where a line wrapped."""
    return re.sub(r"\s+", " ", text)


def budget(total, level):
    """The band as a floor and a ceiling in surviving words — the step-1 sum."""
    lo, hi = E8_BANDS[level]
    return math.ceil(total * (1 - hi)), math.floor(total * (1 - lo))


# ------------------------------------------------------- A27: the prose gate


CONVO_BEFORE = "\n".join([
    "The retry budget is three attempts. After those three are used up, the job",
    "moves into the dead-letter queue, because a human has to look at it there.",
    "",
    "Nothing retries it after that.",
])

CONVO_FAITHFUL = "\n".join([
    "The retry budget is three attempts. A fourth failure moves the job to the",
    "dead-letter queue, because a human has to look at it there.",
    "",
    "Nothing retries it after that.",
])

CONVO_BULLETED = "\n".join([
    "The retry budget is three attempts.",
    "",
    "- A fourth failure parks the job in the dead-letter queue.",
    "- A human has to look at it there.",
    "- Nothing retries it after that.",
])

LIST_BEFORE = "\n".join([
    "Three things changed in this release:",
    "",
    "- The retry budget dropped to three attempts.",
    "- Parked jobs now wait for a human.",
    "- The dead-letter queue got a size cap.",
])

LIST_TIDIED = "\n".join([
    "Three things changed: the retry budget dropped to three attempts, parked",
    "jobs now wait for a human, and the dead-letter queue got a size cap.",
])

FENCED = "\n".join([
    "Run this and read the output:",
    "",
    "```",
    "- item one",
    "- item two",
    "1. numbered step",
    "```",
])


def eval_a27_helper():
    """The comparison itself, before anything is asserted with it."""
    check("A27", "the runner's bands match run_p9's E8_BANDS",
          "E8_BANDS = {\"low\": (0.10, 0.20), \"med\": (0.25, 0.35), \"high\": (0.40, 0.50)}"
          in read(os.path.join(HERE, "run_p9.py")))

    # The helper must classify a bullet exactly as lint.py already does. If it
    # did not, the linter and the harness would be answering different questions
    # about the same reply, and the gate would drift away from the product.
    corpus = []
    for kind in ("clean", "violations"):
        folder = os.path.join(FIXTURES, kind)
        for name in sorted(os.listdir(folder)):
            corpus.append(read(os.path.join(folder, name)))
    disagreements = []
    for text in corpus:
        for line in hc.bullet_lines(text):
            if not hc.lint_mod.NON_PROSE_LINE.match(line):
                disagreements.append(line.strip()[:60])
    check("A27", "every line the helper calls a bullet, lint.py calls non-prose",
          not disagreements, "; ".join(disagreements[:4]))
    counted = sum(hc.bullet_line_count(t) for t in corpus)
    check("A27", "the helper finds bullets in the fixture corpus at all",
          counted > 0, "%d bullet lines across %d fixtures" % (counted, len(corpus)))

    check("A27", "bullets inside a fenced code block are not the reply's shape",
          hc.bullet_line_count(FENCED) == 0, str(hc.bullet_line_count(FENCED)))
    check("A27", "numbered steps count as bullets",
          hc.bullet_line_count("1. first\n2. second") == 2)

    # The pair-level comparison, both directions.
    introduced = hc.convo_bullet_check(CONVO_BEFORE, CONVO_BULLETED)
    check("A27", "a bullet-introducing convo rewrite fails",
          not introduced["ok"] and introduced["introduced"] == 3, json.dumps(introduced))
    faithful = hc.convo_bullet_check(CONVO_BEFORE, CONVO_FAITHFUL)
    check("A27", "a faithful convo rewrite passes", faithful["ok"], json.dumps(faithful))
    tidied = hc.convo_bullet_check(LIST_BEFORE, LIST_TIDIED)
    check("A27", "losing a bullet the original had is not a failure",
          tidied["ok"], json.dumps(tidied))
    kept = hc.convo_bullet_check(LIST_BEFORE, LIST_BEFORE)
    check("A27", "keeping a list the original had is not a failure", kept["ok"])


def eval_a27_gate():
    """The run-level gate the recording driver calls, at every level."""
    for level in LEVELS:
        failing = hc.convo_prose_gate([("e01", "convo", level, CONVO_BEFORE, CONVO_BULLETED)])
        check("A27", "%s: the gate catches a bullet-introducing convo rewrite" % level,
              len(failing) == 1 and failing[0]["level"] == level, json.dumps(failing))
        passing = hc.convo_prose_gate([("e01", "convo", level, CONVO_BEFORE, CONVO_FAITHFUL)])
        check("A27", "%s: the gate passes a faithful convo rewrite" % level,
              passing == [], json.dumps(passing))
        # Terse is untouched: point form is the terse answer, and counting its
        # bullets would fail every correct rewrite.
        terse = hc.convo_prose_gate([("e01", "terse", level, CONVO_BEFORE, CONVO_BULLETED)])
        check("A27", "%s: terse rewrites are not judged by the prose gate" % level,
              terse == [], json.dumps(terse))

    check("A27", "an empty run is a passing gate, not a crash",
          hc.convo_prose_gate([]) == [])

    # The gate can fail. That is the whole point of replacing the old proxy: the
    # v0.2.0 driver's check could not fire on a bullet list at all.
    mixed = hc.convo_prose_gate([
        ("e01", "convo", "high", CONVO_BEFORE, CONVO_FAITHFUL),
        ("e02", "convo", "high", CONVO_BEFORE, CONVO_BULLETED),
        ("e03", "terse", "high", CONVO_BEFORE, CONVO_BULLETED),
    ])
    check("A27", "the gate reports only the convo pair that introduced bullets",
          [f["pair"] for f in mixed] == ["e02|high"], json.dumps(mixed))

    skill = read(SKILL_MD)
    check("A27", "SKILL.md states the convo no-new-bullets contract",
          "No new bullets." in skill
          and "introduces no bullet list the original did not have" in flat(skill))
    check("A27", "the high+convo guardrail names the bullet count, not the old proxy",
          "counts the bullet lines" in flat(skill)
          and "terse-prose-block" not in skill.split("`high` + `convo` stays prose")[1][:400])


# ------------------------------------------------- A28: the two-sided band


def eval_a28_band():
    skill = read(SKILL_MD)

    # --- the obligation, stated in both directions ---
    check("A28", "SKILL.md names both edges of the band",
          "The band has two edges, and both are real." in skill)
    check("A28", "undershooting is named as the same failure as overshooting",
          "Landing under the floor is the same failure as landing over the ceiling"
          in flat(skill))
    check("A28", "the two-sided rule is made concrete at `low`",
          "cutting 40% is a bug, not extra credit" in flat(skill))
    check("A28", "the `Do not` list forbids cutting past the floor",
          "cut past the band's floor" in skill)

    # --- the brevity clause is scoped to the terse block ---
    terse_block = skill.split("**terse**")[1].split("**convo**")[0]
    check("A28", "the brevity clause lives inside the terse voice block",
          "Brevity wins every trade-off" in terse_block
          and skill.count("Brevity wins every trade-off") == 1)
    check("A28", "the brevity clause is bounded by the band",
          "Brevity wins every trade-off — within the conciseness band" in skill)
    check("A28", "the terse block says a deeper cut still cannot cross the floor",
          "under the band's floor" in terse_block)

    # --- neither authority borrows the other's ---
    check("A28", "the level owns how much and the voice owns what shape",
          "The level owns *how much* survives; the voice owns *what shape* it takes."
          in flat(skill))
    check("A28", "neither borrows the other's authority",
          "Neither borrows the other's authority" in flat(skill)
          and "a voice never licenses a deeper cut" in flat(skill))

    # --- the procedure opens with the budget computation ---
    procedure = skill.split("## Rewrite procedure")[1].split("## Exemplars")[0]
    step1 = procedure.split("2. Remove every strip-rule match")[0]
    check("A28", "the rewrite procedure opens with the word-budget computation",
          step1.lstrip().startswith("1. Compute the word budget first."), step1[:120])
    check("A28", "step 1 counts the original's words",
          "Count the words in the reply that bounced" in step1)
    check("A28", "step 1 turns the band into a floor and a ceiling in words",
          "floor and a ceiling in words" in step1)
    check("A28", "step 1 says the rewrite lands inside that range",
          "Land inside that range." in step1)
    check("A28", "step 1 names undershooting as a failure with a fix",
          "put it back" in step1)
    check("A28", "the strip-rule step still runs, one place later",
          "2. Remove every strip-rule match" in procedure)
    check("A28", "the conciseness step is tied back to the budget",
          "until the budget from step 1 is met" in procedure)

    # The kept-word percentages in step 1 must be the bands, not a second set of
    # numbers that can drift away from them.
    for level in LEVELS:
        lo, hi = E8_BANDS[level]
        phrase = "%d–%d%%" % (round((1 - hi) * 100), round((1 - lo) * 100))
        check("A28", "step 1's keep-range at `%s` is the band restated" % level,
              phrase in step1 and level in step1, phrase)


def exemplar_blocks(skill):
    """(label, caption, word count) for each quoted block in the level exemplar."""
    section = skill.split("### Before → after, one paragraph at each level")[1]
    section = section.split("### Guardrails")[0]
    out = {}
    pattern = r"\*\*(Before|low|med|high|The failure case)\*\*([^\n]*)\n((?:> [^\n]*\n)+)"
    for match in re.finditer(pattern, section):
        body = " ".join(ln[2:] for ln in match.group(3).strip().split("\n"))
        out[match.group(1)] = (match.group(2).strip(), words(body))
    return out


def eval_a28_exemplar():
    """The exemplar is arithmetic, so it is checked as arithmetic.

    A per-level exemplar that itself lands outside the band teaches the drift it
    is supposed to prevent — and the v0.2.0 one did, at every level.
    """
    skill = read(SKILL_MD)
    blocks = exemplar_blocks(skill)

    check("A28", "the exemplar ships a Before and all three levels",
          all(k in blocks for k in ("Before", "low", "med", "high")),
          ", ".join(sorted(blocks)))
    if "Before" not in blocks:
        return

    caption, total = blocks["Before"]
    check("A28", "the Before block states its own word count truthfully",
          "%d words" % total in caption, "%s (counted %d)" % (caption, total))

    for level in LEVELS:
        if level not in blocks:
            continue
        caption, kept = blocks[level]
        floor, ceiling = budget(total, level)
        cut = (total - kept) / float(total)
        lo, hi = E8_BANDS[level]
        check("A28", "the `%s` exemplar lands inside its own band" % level,
              lo <= cut <= hi,
              "%d/%d words, %.1f%% cut, band %.0f–%.0f%%" % (kept, total, cut * 100, lo * 100, hi * 100))
        check("A28", "the `%s` exemplar states the budget it was computed from" % level,
              "budget %d–%d" % (floor, ceiling) in caption, caption)
        check("A28", "the `%s` exemplar states its own word count truthfully" % level,
              "%d words" % kept in caption, "%s (counted %d)" % (caption, kept))
        notes.append("exemplar %-4s %d/%d words, %.0f%% cut, budget %d–%d"
                     % (level, kept, total, cut * 100, floor, ceiling))

    check("A28", "the exemplar ships the failure case", "The failure case" in blocks)
    if "The failure case" in blocks:
        caption, kept = blocks["The failure case"]
        under = [l for l in LEVELS if kept < budget(total, l)[0]]
        check("A28", "the failure case is under the floor at every level",
              under == list(LEVELS), "%d words, under at %s" % (kept, ", ".join(under)))
        check("A28", "the failure case is named as a failure, not a target",
              "It fails twice over." in skill and "under the floor at `low`" in skill)
        check("A28", "the failure case names the fact the over-cut cost",
              "cutting past the floor cost a fact" in flat(skill))


# --------------------------------------------- A29: causal links are facts


def eval_a29_rubric():
    skill = read(SKILL_MD)
    fact = skill.split("What counts as a fact")[1].split("\n- ")[0]

    check("A29", "the fact definition names causal and purpose links",
          hc.rubric_names_causal_links(skill), fact[-260:])
    for marker in hc.CAUSAL_MARKERS:
        check("A29", "the definition names the \"%s\" link" % marker,
              '"%s"' % marker in fact)
    check("A29", "dropping why while keeping what is named a loss",
          "Dropping the *why* while keeping the *what* is a loss, not a compression."
          in flat(skill))

    # The enumeration exemption is untouched — this patch adds a fact kind, it
    # does not reopen what `lang-function-over-inventory` settled (P11).
    check("A29", "the enumeration exemption survives",
          "one fact about a change, not one fact per member" in flat(skill))
    check("A29", "the exemption still requires the count and the pointer",
          hc.rubric_names_re_derivable(skill)
          and "count" in fact and "pointer" in fact)

    # run_p10's P11 checks read a 900-character window off the same anchor.
    window = skill.split("What counts as a fact")[1][:900]
    check("A29", "the P11 pins still fall inside run_p10's 900-char window",
          all(w in window for w in
              ("number", "path", "caveat", "code block", "re-derive", "count", "pointer")))

    check("A29", "the preserve-meaning step names the causal link",
          "number, causal link and code block in the original must survive"
          in flat(skill))
    check("A29", "the `Do not` list names the causal link",
          "Do not drop a fact, number, path, causal link or code block" in skill)


def eval_a29_replay():
    """The five recorded losses, replayed against the contract we now ship."""
    cases = hc.replay_cases()
    recorded = [loss["pair"] for loss in json.loads(read(RECORD))["e8"]["losses"]]

    check("A29", "every recorded loss is replayed as a fixture",
          sorted(c["pair"] for c in cases) == sorted(recorded),
          "fixtures %s vs record %s" % (sorted(c["pair"] for c in cases), sorted(recorded)))
    check("A29", "the replay fixtures quote the judge's recorded reasoning",
          all(c["judge_reason"] for c in cases)
          and all(any(c["judge_reason"] == loss["reason"]
                      for loss in json.loads(read(RECORD))["e8"]["losses"])
                  for c in cases))

    skill = read(SKILL_MD)
    for case in cases:
        verdict = hc.classify_loss(case, skill)
        check("A29", "%s is a loss under the shipped rubric" % case["pair"],
              verdict["loss"], json.dumps(verdict))

    causal = [c for c in cases if c["rubric_clause"] == "causal-link"]
    check("A29", "four of the five are covered by the new causal-link clause",
          len(causal) == 4, str(len(causal)))
    for case in causal:
        check("A29", "%s's dropped clause really carries a causal link" % case["pair"],
              hc.carries_causal_link(case["dropped_clause"]), case["dropped_clause"])

    # The classification has to be able to say no, or it proves nothing.
    blunted = skill.replace("And a causal or purpose link is a fact", "And nothing else is")
    still = [c["pair"] for c in causal if hc.classify_loss(c, blunted)["loss"]]
    check("A29", "removing the clause from the rubric stops classifying them as losses",
          not still, ", ".join(still))
    plain = dict(causal[0], dropped_clause="the job is parked in the dead-letter queue")
    check("A29", "a clause with no causal link is not classified by that clause",
          not hc.classify_loss(plain, skill)["loss"])


# ------------------------------------------- P13: W7 register counter-exemplars


TELLS = ("Bolded section headers", "Labelled bullets", "Roll-call lists")


def eval_w7_register():
    skill = read(SKILL_MD)
    register = skill.split("## Register")[1].split("## Voice contract")[0]

    check("P13", "SKILL.md's register section carries a report-grammar counter-exemplar",
          "### Report grammar" in register)
    for tell in TELLS:
        check("P13", "the counter-exemplar names the \"%s\" tell" % tell.lower(),
              "**%s.**" % tell in register)
    # Each tell is shown with the message that replaces it, not just banned.
    arrows = register.split("### Report grammar")[1].count("→")
    check("P13", "each tell ships its rewrite", arrows >= len(TELLS), "%d rewrites" % arrows)
    check("P13", "the counter-exemplar keeps the facts and drops the furniture",
          "Each keeps every fact after the" in register)

    # run_p10 pins the register sentence to exactly one appearance. The new
    # section must not restate it.
    check("P13", "the register is still stated exactly once",
          skill.count(REGISTER_PHRASE) == 1, str(skill.count(REGISTER_PHRASE)))

    check("P13", "the `Do not` list forbids a header a short reply does not need",
          "Do not add a header to a reply that fits without one." in skill)
    check("P13", "the `Do not` list forbids `Label:` bullets a sentence could carry",
          "Do not write `Label:` bullets where a sentence does the job." in skill)

    # The terse exemplar used to teach `Cause:` / `Fix:` bullets — the exact tell
    # E9 recorded. A contract cannot forbid on one page what it demonstrates on
    # another.
    exemplars = skill.split("## Exemplars")[1]
    check("P13", "no exemplar demonstrates the labelled-bullet tell it forbids",
          "> - Cause:" not in exemplars and "> - Fix:" not in exemplars)
    check("P13", "the terse exemplar still carries the cause and the fix",
          "A dep was added without reinstalling." in exemplars
          and "Run install, then rebuild." in exemplars)


def eval_w7_session_block():
    """The SessionStart line names the same three tells, in the same words."""
    hook = read(HOOK_SESSION)
    rule = hook.split("REGISTER_RULE = (")[1].split("\n)")[0]

    check("P13", "the session register line names report grammar",
          "No report grammar" in rule, rule[-200:])
    for phrase in ("bolded section headers", "labelled bullets", "roll-call lists"):
        check("P13", "the session line names %s" % phrase, phrase in rule, rule[-200:])
    check("P13", "the session line names the labels the judge actually saw",
          "Cause: / Fix:" in rule)
    check("P13", "the register is still one line in the block",
          rule.count("\n") <= 6 and "\n\n" not in rule, rule)

    # The block still renders, still fits, and still obeys its own register.
    block = render_block()
    check("P13", "the SessionStart block still renders", block is not None)
    if block:
        bullets = [ln for ln in block.splitlines() if ln.strip().startswith("- ")]
        check("A26", "the block still carries one register line",
              sum(1 for ln in bullets if "Register:" in ln) == 1, block[:200])
        check("A26", "the block still states the register in the shared words",
              any(REGISTER_PHRASE in ln for ln in bullets))


def render_block():
    """The block as the hook emits it, for a convo/med install."""
    import shutil
    import tempfile

    home = tempfile.mkdtemp(prefix="speakingwords-p13-")
    root = os.path.join(home, "skill")
    os.makedirs(os.path.join(root, "scripts"))
    shutil.copy(HOOK_SESSION, os.path.join(root, "scripts", "hook_session.py"))
    with open(os.path.join(root, "pref.json"), "w", encoding="utf-8") as fh:
        json.dump({"voice": "convo", "conciseness": "med"}, fh)
    try:
        proc = subprocess.run(
            [sys.executable, os.path.join(root, "scripts", "hook_session.py")],
            input=json.dumps({"session_id": "p13", "source": "startup"}),
            capture_output=True, text=True,
        )
        if proc.returncode != 0 or not proc.stdout.strip():
            return None
        try:
            payload = json.loads(proc.stdout)
        except ValueError:
            return None
        return payload["hookSpecificOutput"]["additionalContext"]
    finally:
        shutil.rmtree(home, ignore_errors=True)


# -------------------------------------------------------------------- main


def main():
    eval_a27_helper()
    eval_a27_gate()
    eval_a28_band()
    eval_a28_exemplar()
    eval_a29_rubric()
    eval_a29_replay()
    eval_w7_register()
    eval_w7_session_block()

    grouped = {}
    for assertion, name, ok, detail in results:
        grouped.setdefault(assertion, []).append((name, ok, detail))

    out = ["", "speakingwords — Phase 13 deterministic evals", ""]
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
    out.append("PHASE 13 PASS" if not failures else "PHASE 13 FAIL")
    out.append("")
    sys.stdout.write("\n".join(out))
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
