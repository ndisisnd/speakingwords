# speakingwords — v0.2.0 Plan

**Four workstreams: help becomes a first-class util (W1), a user-tunable conciseness level joins voice (W2), all output shifts to a simple Slack-register language standard (W3), and the runtime is hardened for public release (W4). W2 and W3 add capability and a new pref key, so this ships as a semver minor: v0.2.0.**

---

## 1. Problem

- **Help** is a hard-coded `USAGE` constant inside the dispatcher (`bin/speakingwords.js`) — the only command whose logic is not in `lib/`, and one flat wall of text that no longer scales across six commands.
- **Brevity is all-or-nothing.** Voice (`terse`/`convo`) controls *shape*, but nothing controls *how much* text survives. Users who like prose but want it 15% tighter, or who want aggressive cutting without losing paragraph form, have no dial.
- **Register is undefined.** The lexicon bans jargon words but says nothing about sentence construction. Replies can pass every rule and still read like a report — long clauses, formal connectives, essay grammar — when the target reader wants the voice of a colleague typing in Slack.
- **The runtime has pre-release rough edges.** `lint.py` re-parses the lexicon on every reply (latency grows with every `update`), `hits.jsonl` grows without bound, config writes are not atomic, and a machine without `python3` has no defined hook behaviour. None of these bite the author's machine; all of them bite the public.

## 2. Solution

### W1 — Help util (unchanged from the original v0.1.1 scope)

- Extract help into `lib/help.js`; the dispatcher keeps only argv parsing and a call in, matching `status` / `update` / `unhook`.
- Add topic help: `speakingwords help <command>` prints one command's purpose, flags, and its one gotcha.
- Keep every existing trigger unchanged — no command, `help`, and `-h/--help` all still print the full overview.
- One source of truth for the command list: overview text and valid topics derive from the same place, so they cannot drift.

### W2 — Conciseness level (`low` · `med` · `high`)

A third preference axis, orthogonal to voice. Voice says *what shape* a reply takes; conciseness says *how much of it survives*. Any voice pairs with any level.

| Level | Target cut vs. an unstyled reply | Character |
|-------|----------------------------------|-----------|
| `low` | 10–20% | Prose intact; only decoration goes — filler, restatement, hedge stacks |
| `med` (default) | 25–35% | Every sentence earns its place; explanations kept, elaborations cut |
| `high` | 40–50% | Only load-bearing content; every fact, number, path, and code block still survives |

Enforcement follows the existing two-layer split — deterministic where a regex can judge, probabilistic where only reading can:

- **Deterministic (`lint.py`)** — a new `## Conciseness rules` section in `lexicon.md`: restatement and padding patterns ("in other words", "to put it another way", "as mentioned above", "to summarize", "simply put"), each row carrying an `active at` column listing the levels where it fires. `lint.py` gains `--conciseness <level>`; the hook reads the level from `pref.json` and passes it through. Unknown or missing level behaves as `high` and never crashes (A19) — one fallback value everywhere, matching the upgrader default (§8).
- **Probabilistic (`SKILL.md` + evals)** — the rewrite skill gains a conciseness contract per level (the table above, with before→after exemplars). The percentage bands are **eval targets, not per-reply rules** — no linter can measure a cut against a counterfactual, so the bands are proven by E8: rewrite a fixture set at each level, check the median reduction lands in band, and have an LLM judge confirm zero fact loss.
- **Guardrails** — the anti-loss invariant outranks every level: losing a fact is worse than the original violation. And `high` + `convo` must stay prose — E4's "convo never collapses into terse" gate now runs at every level.

Plumbing: `pref.json` gains `conciseness`; `init` asks it as a fourth question (see Open questions); `update` hints can move it ("more concise" / "less aggressive"); the memory-mode block gains one line stating the level and stays within the ≤9-line budget (A1).

- **Upstream prevention (`SessionStart` injection)** — a `SessionStart` hook emits the active style rules (voice + conciseness level, scoped "applies to user-facing prose only") as `additionalContext`, once per session. Rationale: no hook event can intercept reply text pre-display — `MessageDisplay` is display-only, so Stop is inherently lint-after — which makes prevention the only way to cut bounces. The trade is favourable: one ~200–400 token block early in context (prompt-cache-friendly, not per-prompt) against a full reply regeneration per avoided bounce. The Stop hook stays as the enforcement backstop; injection failure changes nothing (fail-open, A26). Claude Code only — Codex parity is an open question (§8).

### W3 — Simple language, every mode

One register, stated once and enforced everywhere: **write like a colleague in a Slack DM**. Short sentences. Everyday words. Contractions fine. Technical terms fine — it is the grammar around them that must be simple, never the vocabulary of the domain.

- **Lexicon strip rules (deterministic)** — formal connectives and essay grammar join the strip table, at `warn` severity because each has legitimate uses: `furthermore`, `moreover`, `thus`, `hence`, `nevertheless`, `aforementioned`, `whilst`, `it should be noted`, `in order to` (→ "to"), `prior to` (→ "before"), `subsequent to`. Every new row ships one planted fixture and one clean control (A20) — a false positive bounces a good reply, still the worst failure class.
- **Language rule (probabilistic)** — new `lang-slack-register` row in `lexicon.md`, with an exemplar, applied by the rewrite skill in hook mode and stated in the memory block in memory mode. The memory-block template itself is rewritten in this register — the instruction should sound like what it asks for.
- **Structural check (deterministic, conservative)** — new `long-sentence` rule: fires only when three or more sentences in a reply exceed 35 words. The high trigger threshold is deliberate; a single long sentence is style, a pattern of them is register drift. Code fences, tables, and quoted text are exempt, as with `terse-prose-block`.
- **Proof** — E9 runs the E3/E4 prompt set through a judge scoring two things independently: "reads like a Slack message from a colleague" and "technical content fully intact". Both must pass; simplicity bought with lost precision is a failure.

### W4 — Stability hardening for public release

Five fixes, each one closing a failure mode that only shows up off the author's machine:

1. **Lexicon parse cache.** `lint.py` writes a sidecar cache (compiled-rule listing keyed by `lexicon.md` mtime + size) next to the lexicon. Stale, corrupt, or deleted cache → silent full reparse. The cache may never change a verdict: CI runs the E1 fixture set with and without it and diffs the output byte-for-byte (A21, E10). This keeps the <100 ms p95 latency budget (E6) honest as `update` grows the rule table.
2. **Atomic writes.** Every config write — `pref.json`, lexicon edits from `update`, memory-block re-render — goes temp-file-then-rename. A crash mid-write leaves the old file or the new file, never a torn one (A22).
3. **Telemetry rotation.** `hits.jsonl` rotates at 1 MB to a single `hits.jsonl.1`; `status` reads both and its totals are unchanged across a rotation boundary (A23). Unbounded append was fine for a dev tool; it is a slow-burn bug for a public one.
4. **Fail-open without `python3`.** The hook wrapper probes for `python3` before invoking `lint.py`; absent → exit clean immediately, record one line in `pref.json` (`lint_disabled_reason`) that `status` surfaces as a warning. A missing interpreter must never block a user's replies (A24) — the tool degrades to memory-mode honesty, not to breakage.
5. **Pref forward-compatibility.** `writePref` preserves unknown keys instead of dropping them, and every reader defaults a missing `conciseness` to `high` (E8 baseline showed current 0.1.0 behaviour cuts ~40%, the `high` band — a missing key means an upgrader, and upgraders keep the behaviour they chose). Users upgrading in place from 0.1.0 get working defaults with no migration step; users downgrading lose nothing (A25).

## 3. Non-goals

- No man pages, shell completion, colour/formatting engine, or interactive help browser.
- No per-reply conciseness override (a flag on a single message) — the level is installed configuration, same as voice.
- No readability-score engine (Flesch etc.) in the linter — the `long-sentence` heuristic plus the E9 judge cover register; a scoring library is a dependency and a rabbit hole.
- No new agents, no new modes.

## 4. Assertions (extends A1–A13)

**Help (W1)**
- **A14** — `help`, no-args, and `--help` produce byte-identical overview output.
- **A15** — Every accepted command has a topic; every topic maps to a real command (CI greps both directions).
- **A16** — Explicitly requested help exits `0`; help shown after an unknown command or bad flag exits `1`.
- **A17** — `help <unknown-topic>` exits non-zero, prints the full overview, and names the topic — never a stack trace.
- **A18** — Success paths write to stdout, error paths to stderr; `help > file` captures clean help only.

**Conciseness & language (W2, W3)**
- **A19** — `lint.py --conciseness` accepts `low|med|high`; any other value (or absence) behaves as `high` (the upgrader/behaviour-preservation default), exits per the A6 contract, never crashes.
- **A20** — Every new strip-table and conciseness-table row ships ≥1 planted fixture and ≥1 clean control; CI fails on an uncovered row.
- **A26** — The `SessionStart` injection emits the style block at most once per session, scoped to user-facing prose; hook absence, failure, or timeout changes no behaviour — the Stop backstop is unaffected either way.

**Hardening (W4)**
- **A21** — Deleting or corrupting the lexicon cache never changes a lint verdict; E1 output with and without cache is byte-identical.
- **A22** — All config writes are atomic (temp + rename); a write interrupted at any point leaves a parseable file.
- **A23** — `status` totals are identical immediately before and after a `hits.jsonl` rotation of the same data.
- **A24** — With `python3` absent from PATH, the hook exits `0` and no reply is ever blocked.
- **A25** — A 0.1.0 `pref.json` (no `conciseness` key) works with every 0.2.0 util unmodified; a rewrite preserves keys it does not know.

## 5. Evals (extends E1–E7)

| Eval | Method | Gate |
|------|--------|------|
| **E8 — Conciseness bands** | 20 fixture replies rewritten at each level; measure word-count reduction; LLM judge checks facts, numbers, paths, and code blocks all survive | Median reduction in band per level (10–20 / 25–35 / 40–50%); zero fact loss on judged sample; `high`+`convo` output still prose (E4 gate re-run) |
| **E9 — Slack register** | E3/E4 prompt set; judge scores register ("colleague in a DM") and technical fidelity as separate axes | ≥85% pass both axes on first reply; ≥95% after one bounce |
| **E10 — Cache parity** | Run full E1 fixture set cold (no cache), warm (cache present), corrupted (garbage cache file); diff JSON output | Byte-identical verdicts in all three states; warm run p95 under the E6 budget |

E1 fixture set grows to cover every new rule row (A20). E10 and the extended E1 are deterministic and join `eval:deterministic`; E8–E9 cost model calls and run on demand, recorded at release like E2–E5.

## 6. Acceptance criteria

**Help (W1)**
- [ ] `help`, no-args, and `--help` print the same overview — all six commands, one-line description each; nothing from 0.1.0 dropped.
- [ ] `help init` / `help update` / `help unhook` print each command's flags and its one gotcha; `help <unknown>` exits non-zero, prints the overview, names the bad topic.
- [ ] Help logic in `lib/help.js`; dispatcher only parses args and dispatches.
- [ ] README gains a short "Getting help" section.

**Conciseness (W2)**
- [ ] `init` captures a level; `pref.json` records it; hook passes it to `lint.py`.
- [ ] Conciseness rules fire per their `active at` column — a "to summarize" fixture bounces at `high`, passes at `low`.
- [ ] `update "more concise"` raises the level and says so; `.bak` discipline (A4) holds.
- [ ] Memory block states the level and stays ≤9 lines (A1) in all four targets.
- [ ] E8 gates pass at all three levels.
- [ ] `SessionStart` hook injects the style block once per session; a masked or failing hook changes nothing (A26); Stop-bounce rate on the E8 prompt set is lower with injection than without.

**Language (W3)**
- [ ] New strip rules live in `lexicon.md` with fixtures and clean controls; `furthermore` bounces, "the aforementioned case law" style domain text is covered by a documented park-the-rule note, mirroring `strip-leverage`.
- [ ] `long-sentence` fires on a three-long-sentence fixture and stays silent on one long sentence.
- [ ] Memory-block template reads in the register it prescribes.
- [ ] E9 gates pass.

**Hardening (W4)**
- [ ] Warm-cache lint beats cold on a 100-run timing check and E10 passes.
- [ ] Kill-mid-write fault test leaves parseable `pref.json` and lexicon.
- [ ] Rotation boundary test passes; `status` reads both files.
- [ ] Hook exits clean with `python3` masked from PATH; `status` shows the degradation warning.
- [ ] 0.1.0 `pref.json` fixture passes every util.

**Release**
- [ ] Version bumped; internal `speakingwords` self-pin matches.
- [ ] P1–P6 stay green; new phases green; deterministic evals wired into `eval:deterministic`.
- [ ] Tagged.

## 7. Build phasing (extends P1–P6; one commit per phase, same orchestration contract as v0.1.0 §13)

| Phase | Scope | Proof gate (commit blocker) |
|-------|-------|------------------------------|
| **P7** | W1 help: `lib/help.js`, topic help, dispatcher slimming | A14–A18; `evals/run_p7` |
| **P8** | W4 hardening first — cache, atomic writes, rotation, fail-open, pref compat | A21–A25; E10 |
| **P9** | W2 conciseness: lexicon section, `lint.py --conciseness`, `SKILL.md` contract, pref + init + update + memory-block plumbing, `SessionStart` injection | A19, A20 (conciseness rows), A26; E8 recorded |
| **P10** | W3 language: strip rows, `lang-slack-register`, `long-sentence`, memory-template rewrite | A20 (language rows); E9 recorded; E1 extended set green |

P8 runs before P9/P10 deliberately: the new rule tables should land on the cached parser and atomic writers, not be retrofitted onto them.

## 8. Open questions

- **Init question count** — RESOLVED (2026-08-14): four plain questions. Merging voice + conciseness into one picker would hide that they are independent axes. Docs retire the "≤3 questions" wording.
- **Topic depth (W1)** — RESOLVED (2026-08-14): the relevant slice of the overview plus flags and the one gotcha. One source of truth, no drift.
- **Codex injection parity (W2)** — RESOLVED (2026-08-14): timebox a check on whether Codex has an equivalent pre-reply context channel; if none is confirmed, the Codex adapter ships lint-after only and docs say so. Parity does not block the release.
- **Default level for upgraders** — RESOLVED (2026-08-14): baseline measurement puts current 0.1.0 behaviour at ~40% cut, i.e. the `high` band — not `med`/`low` as guessed. Missing `conciseness` key therefore defaults to `high`, preserving behaviour for upgraders; only upgraders can hit the missing-key path, since `init` always writes a level. New installs still get `med` suggested at init. A25 and W4 §5 updated to match.
