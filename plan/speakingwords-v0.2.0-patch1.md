# speakingwords — v0.2.0 Patch 1: make the levels operative

**One workstream: the E8/E9 recorded run (evals/records/e8-e9-v0.2.0.md) proved the conciseness levels and the register are stated but not obeyed. This patch rewrites the probabilistic contract so they bind, fixes the one vacuous harness check, and re-records. No new capability, no new pref keys, no lexicon rule changes — the v0.2.0 tag ships when this patch turns the two judged gates green.**

---

## 1. Problem — what the recording showed

- **The level does nothing.** Medians 47.7% / 51.2% / 49.7% against bands 10–20 / 25–35 / 40–50. The rewriter cuts to ~50% whatever level it is handed, even with the level named twice in the prompt. Diagnosis: SKILL.md's brevity energy ("brevity wins every trade-off") is voice-scoped in intent but reads as global; the per-level exemplar shows the bands but nothing *forbids* overshooting them. Undercutting reads as failure, overcutting reads as diligence.
- **Convo collapses into bullets.** The rewriter converts prose replies to bullet lists at every level. The E4 guardrail did not catch it because the harness proxied "stays prose" with `terse-prose-block` — a check that cannot fire on a bullet list. The gate passed vacuously.
- **Register fails as report grammar.** E9 register axis 65.8%: bolded headers, labelled bullets (`Cause:` / `Fix:`), roll-call lists. Fidelity axis is nearly clean (97.4%) — the model keeps the facts and formats them like a report.
- **Five anti-loss drops in 60 pairs.** All causal or qualifying clauses ("the reason parked jobs wait", "simpler to reason about", exact-pin-vs-range). The fact definition names numbers, paths, caveats — it does not name causal links, so the rewriter treats them as elaboration.

## 2. Solution

### W5 — Level mechanics in SKILL.md

- **Budget arithmetic, stated as procedure.** Step 0 of the rewrite: count the original's words, compute the level's floor and ceiling as word counts, and land inside them. A number the model computes binds harder than a percentage it reads.
- **Overcutting is a violation, symmetrically.** New contract line: undershooting the band's floor is the same failure as overshooting its ceiling — at `low`, cutting 40% *is the bug*, not extra credit. The band exemplar gains the failure case: the 61-word fixture cut to 16 words labelled "correct at `high`, a failure at `low`".
- **Scope the brevity clause.** "Brevity wins every trade-off" moves inside the terse-voice block explicitly and gains "— within the conciseness band". The level owns *how much*; the voice owns *what shape*; neither may borrow the other's authority.

### W6 — Convo keeps its paragraphs, provably

- **Contract line with teeth:** a convo rewrite introduces no bullet list the original did not have. Reshaping prose into bullets is the terse voice's move; doing it at convo is a failed rewrite even when every fact survives.
- **Close the vacuous check.** The harness (and E4) stop proxying "stays prose" through `terse-prose-block`. New deterministic comparison: bullet-line count of the rewrite ≤ bullet-line count of the original for convo runs (A27). This is measurable without a judge and impossible to pass by collapsing.

### W7 — Register counter-exemplars

- SKILL.md's register section gains a "what report grammar looks like" counter-exemplar built from the three recorded failure modes: bolded section headers, labelled bullets, roll-call lists — each shown with its Slack-DM rewrite.
- The `## Do not` list gains: no headers in a reply that fits without them; no `Label:` bullets where a sentence does the job.
- The SessionStart block's register line names the same three tells, so prevention and enforcement agree.

### W8 — Causal links are facts

- The "What counts as a fact" guardrail adds one clause: a causal or purpose link ("because", "so that", "which is why") is a fact — dropping *why* while keeping *what* is loss. All five recorded losses fall under this clause; the enumeration exemption is untouched.

### Harness (recording driver, not shipped code)

- Convo prose gate re-implemented per W6 (bullet-introduction diff, all levels — not just `high`).
- Re-record E8 and E9 per the existing `e8_report()` / `e9_report()` procedures, same pinned model (`claude-sonnet-5`), same fixtures, same judge rubric. Gates unchanged.

## 3. Non-goals

- No band renegotiation — the bands stay as written for this iteration. If a full contract rewrite still cannot hold 10–20% at `low`, *that* recording is the evidence that the bands are fiction, and renegotiation becomes its own decision (§8).
- No lexicon rule rows, no lint.py changes, no pref changes — the deterministic layer is proven and untouched.
- No model change and no prompt-engineering escape hatches in the harness (trimming SKILL.md per-level, few-shotting the judge). The product must work with the contract it ships.

## 4. Assertions (extends A1–A26)

- **A27** — For every convo rewrite at every level, bullet-line count of the output ≤ bullet-line count of the input. Checked deterministically in the E8 harness and asserted in the phase runner against the SKILL.md wording.
- **A28** — SKILL.md states the band as a two-sided obligation (floor and ceiling both named as failure directions) and the rewrite procedure opens with the word-budget computation. Grep-able phrasing, covered by the phase runner.
- **A29** — The fact definition names causal/purpose links; the five recorded e8 loss cases, replayed as fixtures, are each classified "loss" by the rubric's own wording.

## 5. Evals

Gates unchanged from v0.2.0 §5 — that is the point:

| Eval | Gate |
|------|------|
| **E8 re-record** | Medians in band per level (10–20 / 25–35 / 40–50); zero judged losses; A27 zero bullet-introductions at convo |
| **E9 re-record** | ≥85% both axes first reply; ≥95% after one bounce |

Recorded to `evals/records/e8-e9-v0.2.0-patch1.{md,json}`, same model pin, alongside — not replacing — the failed record. The failed record is the baseline that proves the patch did something.

## 6. Acceptance criteria

- [ ] SKILL.md: budget procedure, two-sided band, scoped brevity clause, convo bullet line, register counter-exemplars, causal-link clause (W5–W8).
- [ ] Phase runner covers A27–A29 deterministically; existing runners P1–P10 all stay green (SKILL.md text pins in run_p9/run_p10 updated where wording moved).
- [ ] E8/E9 re-recorded; both gates PASS; record committed.
- [ ] Tag `v0.2.0` — this patch has no version of its own; it is what the withheld tag was waiting for.

## 7. Build phasing

| Phase | Scope | Proof gate (commit blocker) |
|-------|-------|------------------------------|
| **P13** | W5–W8: SKILL.md contract rewrite, hook_session register line, harness prose-gate fix, phase runner | A27–A29; P1–P10 green |
| **Record** | E8/E9 re-record (orchestrator-dispatched, model calls) | Both gates PASS → tag `v0.2.0` |

Same orchestration contract as v0.1.0 §13. If the re-record fails again, the sequence halts for human review with both records side by side — that is decision territory (§8), not retry territory.

## 8. Open questions

- **If `low` still cannot hold 10–20%** after a contract this explicit, the bands are renegotiated with two data points instead of one. Candidate fallback: low 15–30 / med 30–45 / high 45–60, re-anchored to what one-pass rewrites of this model family actually do. Not decided now; decided only on evidence. — **RESOLVED 2026-08-14, see §9.**

---

## 9. Addendum (2026-08-14) — the dial ships two positions

**Decision.** The conciseness dial ships as `low` and `high`. `med`'s behaviour and band are promoted to become `high`; the old 40–50% `high` band is dropped entirely. This closes §8: the bands were renegotiated, and the renegotiation was a deletion rather than a re-anchoring.

### The evidence

The rig2 recording (`evals/records/e8-e9-v0.2.0-rig2.md`) is the first measurement taken in a real installed environment — a throwaway HOME provisioned by the installer, with the Stop hook and SessionStart injection live and SKILL.md on disk rather than pasted into the prompt. The two earlier recordings pasted the contract, which produced prompt-injection refusals and register behaviour the product never exhibits, so they measure something the product is not.

| Level | Band as recorded | rig2 median | Verdict |
|---|---|---|---|
| `low` | 10–20% | 16.1% | in band |
| `med` | 25–35% | 25.4% | in band |
| `high` | 40–50% | 19.9% | **under its own floor** |

Two positions were proven and one was fiction. Keeping the fiction would ship a dial where a third of the settings does not do what the table says — and worse, where the most aggressive setting cuts *less* than the middle one. The recording's own diagnosis is that the two-sided band plus the injected block make the model conservative, so `high` stalls in `low`/`med` territory. That is not a contract-wording problem to iterate on; it is evidence that a 40–50% one-pass cut is not a thing this model family does while also holding the anti-loss invariant.

Promoting `med` rather than renaming `low` keeps the proven character of the default: 25–35% is what a user asking for "concise" was already getting, and it was landing where it claimed to.

### The normalization contract

`med` is a **recognised legacy value**, not an error, everywhere a level is read:

- `lint.py` — `LEVELS = ("low", "high")`, `LEGACY_LEVELS = {"med": "high"}`. `normalise_level()` maps it, and so does `parse_levels()`, so a lexicon cell still reading `med, high` places its row at `high` rather than falling through to the default.
- `hook_stop.py` / `hook_session.py` — the same two constants, restated rather than imported, because both read a preference before the linter module is loaded and a fail-open script must not fail closed on an import error. `run_p9.py` asserts the three copies agree.
- `lib/pref.js` — `LEGACY_LEVELS` plus `normaliseLevel()`, which returns `null` for a genuinely unknown value so the `--conciseness` flag can report a real error naming only the two shipped levels.
- `lib/memory.js` — a legacy value renders the `high` bullet, byte-identical to an install that says `high`.

Unknown values and a missing key still fall back to `high` — **one fallback value everywhere**, which is the A19 contract unchanged. What moved is only the rationale: `high` is now "the most aggressive shipped level" rather than "the band 0.1.0 measured in", and both statements happen to point at the same value.

Files are never rewritten to normalise a level. Readers normalise at read time; `writePref` preserves whatever value it finds when it was handed no opinion (A25). A 0.2.0-dev `pref.json` still saying `med` keeps saying `med` and behaves as `high`.

### E9's register gates ship as a documented known gap

**Decision: the release is not blocked on them.** Both E9 gates are red (71.8% against 85% and 95%), and the recording's own eval-design findings say why the gates cannot be reached as written:

1. The prompt set presupposes shared context ("our tokens", "our cache"). A grounded installed agent honestly asks which repo instead of role-playing. The pasted baselines only scored well because a contract-in-prompt model plays along.
2. The terse voice and the DM-register axis fight each other. Terse mandates point form; judges read bulleted answers as support-doc grammar.
3. The hook cannot bounce register. Register violations are invisible to regex rules, so a ">=95% after one bounce" gate assumes a bounce that fires 3 times in 40.

A gate that measures something the product cannot do is a gate to redefine, not a feature to withhold a release for. Redefining them — self-contained prompts, a rubric that agrees with the terse contract, a post-bounce measure the hook can actually reach — is future work with its own plan. The result is stated in the README's "Known gaps" section and the recordings are in the repo, so nobody has to take the claim on trust.

### Scope of the change

Recordings in `evals/records/` are **immutable history** and were not touched. The rig2 record still reads `low` / `med` / `high` against the bands in force when it ran; this addendum is the remapping story. Everything else that knows a level moved: `lib/pref.js`, `lib/memory.js`, `lib/update.js`, `lib/help.js`, `bin/speakingwords.js`, `skill/SKILL.md`, `skill/refs/lexicon.md`, `skill/scripts/{lint,hook_stop,hook_session}.py`, `README.md`, and the phase runners `run_p8`/`run_p9`/`run_p10`/`run_p13` plus `evals/record_rig.py`.
