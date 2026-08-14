#!/usr/bin/env python3
"""Deterministic checks the E8/E9 recording driver must call (patch1 §2 W6, W8).

The recording driver lives outside this repo — it needs model calls, so it can
never run inside `eval:deterministic`. That is exactly why the checks it makes
live *here*: the v0.2.0 run passed its "convo stays prose" gate vacuously,
because the driver proxied the question through the linter's
`terse-prose-block` rule, and that rule cannot fire on a bullet list. A gate
that cannot fail is not a gate. So the comparison is written once, in the repo,
gated by evals/run_p13.py, and the driver imports it:

    sys.path.insert(0, "<repo>/evals")
    from harness_checks import convo_prose_gate, replay_cases, classify_loss

What is exported
----------------
  bullet_lines(text)          the bullet lines of a reply, code fences removed
  bullet_line_count(text)     how many there are
  convo_bullet_check(a, b)    A27 on one pair: no bullet the original lacked
  convo_prose_gate(pairs)     A27 over a whole run — what the driver calls
  replay_cases()              the five recorded E8 loss cases, as fixtures
  classify_loss(case, rubric) whether SKILL.md's own wording calls that a loss

Classification is borrowed from `lint.py`, never re-invented. A bullet line is
a line `prose_blocks()` already drops as non-prose, narrowed to the two markers
that make a list. If the two ever disagreed, the linter and the harness would
be answering different questions about the same reply — run_p13 asserts they
do not.
"""

import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SCRIPTS = os.path.join(ROOT, "skill", "scripts")
SKILL_MD = os.path.join(ROOT, "skill", "SKILL.md")
REPLAY = os.path.join(HERE, "fixtures", "e8_loss_replay.json")

# Same import discipline as run_p8/run_p9: no __pycache__ left in the shipped tree.
sys.dont_write_bytecode = True
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)
import lint as lint_mod  # noqa: E402

# The list-making subset of lint.NON_PROSE_LINE. Headings, quotes, table rows
# and fences are non-prose too, but none of them is what a rewrite reaches for
# when it flattens a paragraph — bullets and numbered steps are.
BULLET_LINE = re.compile(r"^\s*(?:[-*+•]\s|\d+[.)]\s)")

# The connectives SKILL.md names in "What counts as a fact" (patch1 §2 W8). A
# clause hanging off one of these carries *why*, and dropping why while keeping
# what is the loss all five recorded E8 failures share.
CAUSAL_MARKERS = ("because", "so that", "which is why")


# ------------------------------------------------------------------ A27


def bullet_lines(text):
    """Bullet and numbered-step lines, with fenced code blanked out first.

    Fences go first for the same reason lint.py blanks them: a shell listing
    or a diff inside a code block is content, not the reply's shape, and a
    rewrite that quotes one has not turned prose into a list.
    """
    stripped = lint_mod.strip_code_fences(text or "")
    return [ln for ln in stripped.splitlines() if BULLET_LINE.match(ln)]


def bullet_line_count(text):
    return len(bullet_lines(text))


def convo_bullet_check(original, rewrite):
    """A27 on one pair: a convo rewrite introduces no bullet the original lacked.

    Deliberately one-sided. Losing a bullet is fine — that is a rewrite tidying
    a list the original already had. Gaining one is the failure, at every level:
    `high` is not permission to collapse convo into terse.
    """
    before = bullet_line_count(original)
    after = bullet_line_count(rewrite)
    return {
        "ok": after <= before,
        "before": before,
        "after": after,
        "introduced": max(0, after - before),
    }


def convo_prose_gate(pairs):
    """A27 over a run. `pairs` is an iterable of (name, voice, level, before, after).

    Returns the failures, so an empty list is the passing gate. Terse pairs are
    not judged here — point form is the terse answer, and counting its bullets
    would fail every correct rewrite.
    """
    failures = []
    for name, voice, level, before, after in pairs:
        if voice != "convo":
            continue
        verdict = convo_bullet_check(before, after)
        if not verdict["ok"]:
            failures.append({
                "pair": "%s|%s" % (name, level),
                "level": level,
                "bullets_before": verdict["before"],
                "bullets_after": verdict["after"],
                "introduced": verdict["introduced"],
            })
    return failures


# ------------------------------------------------------------------ A29


def replay_cases():
    """The five fact losses the v0.2.0 recording produced, as replay fixtures."""
    with open(REPLAY, "r", encoding="utf-8") as fh:
        return json.load(fh)["cases"]


def rubric_names_causal_links(rubric=None):
    """Does the shipped rubric say a causal or purpose link is a fact?"""
    text = rubric if rubric is not None else read_rubric()
    scoped = text.split("What counts as a fact")
    if len(scoped) < 2:
        return False
    body = scoped[1].split("\n- ")[0]
    return ("causal" in body and "purpose" in body
            and all(m in body for m in CAUSAL_MARKERS))


def rubric_names_re_derivable(rubric=None):
    """Does it still protect a claim the reader cannot re-derive? (pre-existing)"""
    text = rubric if rubric is not None else read_rubric()
    scoped = text.split("What counts as a fact")
    return len(scoped) > 1 and "re-derive" in scoped[1].split("\n- ")[0]


def read_rubric():
    with open(SKILL_MD, "r", encoding="utf-8") as fh:
        return fh.read()


def carries_causal_link(clause):
    return [m for m in CAUSAL_MARKERS if m in (clause or "").lower()]


def classify_loss(case, rubric=None):
    """Would SKILL.md's own wording call this dropped clause a loss?

    Deterministic, and deliberately not a re-judgement: the judge already ruled
    on these five, and the question here is only whether the contract we ship
    now *says* so. A case is covered when the rubric clause it is filed under is
    present, and — for the causal ones — when the clause really does hang off
    one of the connectives the rubric names.
    """
    text = rubric if rubric is not None else read_rubric()
    covered_by = case["rubric_clause"]

    if covered_by == "causal-link":
        markers = carries_causal_link(case["dropped_clause"])
        return {
            "loss": bool(markers) and rubric_names_causal_links(text),
            "covered_by": covered_by,
            "markers": markers,
        }
    if covered_by == "cannot-re-derive":
        return {
            "loss": rubric_names_re_derivable(text),
            "covered_by": covered_by,
            "markers": [],
        }
    return {"loss": False, "covered_by": covered_by, "markers": []}
