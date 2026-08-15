# speakingwords — v0.3.0 Plan

**Two workstreams: a second register modelled on ASD-STE100 Simplified Technical English joins the Slack register behind a new `register` pref key (W5), and `mode: both` lets memory and hook run together with a hard no-duplication contract — the block prevents, the hook enforces, and nothing is stated twice (W6). Both add a pref value and new capability, so this ships as a semver minor: v0.3.0.**

---

## 1. Problem

- **The register is single and it fights a real audience.** The Slack register is the only one that ships: contractions encouraged, colleague-in-a-DM grammar. Users writing procedures, runbooks and operator-facing docs — especially for readers whose first language is not English — want the opposite discipline: controlled sentence length, active voice, one instruction per sentence, no contractions. That is exactly what ASD-STE100 codifies, and today the tool's own rules push replies away from it.
- **Mode is a forced either/or, and each half has a gap the other closes.** Memory mode is suggestive only — the agent can drift and nothing bounces. Hook mode enforces, and its `SessionStart` injection prevents, but injection is per-session and has a documented miss: a Codex resume emits no `SessionStart` (openai/codex#24228), so that session runs with no upstream contract at all. A CLAUDE.md block is in context every session unconditionally. Users who want both the always-present block *and* enforcement currently cannot have them.
- **Naive stacking would be worse than either mode alone.** If `both` simply turned everything on, the contract would sit in context twice — once from the block, once from the injector — burning tokens and teaching nothing extra. The two layers must divide the work: memory prevents, hook enforces, and prevention has to be measurably good enough that the hook rarely fires.

## 2. Solution

### W5 — STE register (`register: slack | ste`)

A third preference axis. Voice says *what shape*, conciseness says *how much survives*, register says *how the sentences are built*. Any voice and any level pairs with either register. `slack` is the default and the value every existing install behaves as; a missing key means an upgrade from ≤0.2.0 and reads as `slack` — behaviour preservation, same reasoning as the conciseness fallback (§0.2.0 plan §8).

The register is **STE-inspired, not STE-conformant**. The ASD-STE100 specification's writing rules are implementable; its ~900-word approved dictionary is ASD's copyright and shipping or reproducing it is a non-goal. What ships is the rule discipline:

| STE rule | Enforcement |
|----------|-------------|
| Sentence length cap: ≤25 words, every sentence | Deterministic — `ste-long-sentence` fires per sentence at `ste` (replacing the Slack register's 3×35 `long-sentence` heuristic, which stays for `slack`) |
| No contractions | Deterministic — `ste-contraction`, an enumerated verb-contraction pattern (`don't`, `can't`, `it's`…), never a bare-apostrophe match, so possessives (`the pump's seal`) stay safe |
| Active voice; imperative for instructions | Probabilistic — SKILL.md contract + memory-block line; a passive-voice regex is a false-positive machine and false positives bounce good replies |
| One instruction per sentence; one topic per paragraph | Probabilistic — SKILL.md contract |
| Keep articles ("Install **the** pump", not telegraphic "Install pump") | Probabilistic — SKILL.md contract |
| One word, one meaning (approved-word *spirit*, not the dictionary) | Probabilistic — SKILL.md guidance with exemplars |

Mechanics, mirroring the conciseness build:

- **Lexicon** gains a `## Register rules` table with an `active at register` column; same pattern dialect, same fixture discipline (every row ships a planted fixture and a clean control, A20 extended).
- **`lint.py --register <value>`**; the hook reads `register` from `pref.json` and passes it through. Unknown or absent → `slack`, never a crash (A33, mirroring A19).
- **Register conflict is a swap, not a merge.** `lang-slack-register` and the contraction encouragement are *off* at `ste`; the strip rules, language rules and conciseness rules are register-neutral and stay on. In the memory block the STE line **replaces** the Slack-DM line (line 1), so the bullet budget holds at 9 (A1, asserted as A30).
- **SKILL.md** gains a register section parallel to the voice contract: the installed register comes from `pref.json`, both registers obey strip/language/conciseness rules identically, before→after exemplars per rule.
- **Init** asks register as a fifth question, default `slack`; `init --defaults` takes every default and asks nothing (§8); `update` hints can move it ("simplified technical english", "back to slack register").

### W6 — Combined mode (`mode: both`)

`init` offers three modes: `memory`, `hook`, `both`. At `both`:

- **The memory block is written** exactly as memory mode writes it (same renderer, same A1 budget, same idempotence).
- **The Stop hook is wired** exactly as hook mode wires it (same lint, same one-bounce contract).
- **The `SessionStart` injector is *not* installed.** This is the no-duplication rule made mechanical: the block is already in context every session, so injecting the same contract again is the one thing `both` must never do. The division of labour is total — the block owns prevention, the Stop hook owns enforcement, and no rule text reaches the model twice (A31).

Why this beats hook mode's own injector for these users: the block survives the Codex resume gap (no `SessionStart` fired → hook mode's prevention silently absent; the block is still there), it is visible and versionable in the user's own CLAUDE.md, and it costs its tokens once per context, not once per wiring path.

Prevention must be provable, not assumed. **E12** runs the E8 prompt set twice — hook-only and `both` — and gates on the Stop-bounce rate at `both` being strictly lower. "Memory works well enough that the hook rarely fires" is the whole point of the mode; if the block does not measurably cut bounces, `both` is just hook mode plus clutter and does not ship.

Util behaviour at `both`:

- **`status`** reports both layers: block present/absent + linter counters, and names the mode.
- **`update`** re-renders the block *and* edits the lexicon in one pass — the two artefacts derive from the same rule file, so they cannot drift (the block's banned-word line is already lexicon-derived).
- **`unhook`** degrades `both` → `memory`: hook removed, block kept, `pref.json` rewritten, and it says so. Removing everything is `uninstall`'s job, not `unhook`'s (A32).

## 3. Non-goals

- No ASD-STE100 dictionary — not shipped, not reproduced, not "helpfully" approximated as a 900-word list. Rule discipline only, labelled STE-*inspired* everywhere user-facing.
- No conformance claim. Output is not certified STE and the docs say so.
- No procedural/descriptive sentence classification (STE's 20 vs 25-word split) — the linter cannot classify intent; one conservative cap (25) applies. Recorded as accepted imprecision.
- No passive-voice regex. Probabilistic rules stay probabilistic.
- No per-reply register override; register is installed configuration, same as voice and level.
- No third voice, no new conciseness level, no new agents.

## 4. Assertions (extends A1–A27)

- **A33** — `lint.py --register` accepts `slack|ste`; any other value or absence behaves as `slack`, exits per the A6 contract, never crashes.
- **A34** — Every `## Register rules` row ships ≥1 planted fixture and ≥1 clean control; `ste-contraction` fixtures include a possessive clean control (`the pump's seal`) and a quoted-code clean control.
- **A30** — The memory block at `register: ste` still renders ≤9 bullet lines: the STE line replaces the Slack line, never appends. CI renders all voice × level × register combinations and counts.
- **A31** — At `mode: both`, the contract reaches the model exactly once: the block is present *and* no `SessionStart` entry for speakingwords exists in any agent config. CI asserts both directions (hook mode keeps its injector; both mode has none).
- **A32** — `unhook` at `both` leaves a working memory install: block intact, hook gone, `pref.json` says `memory`, and a subsequent `init` can restore `both` idempotently.
- **A35** — A 0.2.0 `pref.json` (no `register` key) works with every 0.3.0 util unmodified; rewrites preserve unknown keys both directions (extends A25).

## 5. Evals (extends E1–E10)

| Eval | Method | Gate |
|------|--------|------|
| **E11 — STE register** | Fixture set rewritten at `ste` (both voices, both levels); deterministic pass counts contractions and per-sentence word counts; LLM judge scores active voice / imperative instructions and technical fidelity as separate axes | 0 contractions; 100% of prose sentences ≤25 words; ≥85% pass both judge axes first reply, ≥95% after one bounce; zero fact loss |
| **E12 — Both-mode prevention** | E8 prompt set run at `mode: hook` and `mode: both`, same voice/level; Stop-bounce rate measured from `hits.jsonl` | Bounce rate at `both` strictly lower than hook-only; block-present sessions never double-inject (A31 checked in-run) |

E1 fixture set grows to cover every register row (A34). Register rows join `eval:deterministic`; E11–E12 cost model calls and are recorded at release like E8–E9.

## 6. Acceptance criteria

**STE register (W5)**
- [ ] `init` captures register; `pref.json` records it; hook passes it to `lint.py`.
- [ ] `ste-contraction` bounces `don't` at `ste`, stays silent at `slack`, and never fires on possessives or code fences.
- [ ] `ste-long-sentence` fires per sentence >25 words at `ste`; `long-sentence` (3×35) still governs `slack`.
- [ ] SKILL.md register section shipped with exemplars; memory block renders the STE line in place of the Slack line at ≤9 bullets in every combination.
- [ ] Docs say "STE-inspired" and link the free ASD-STE100 spec download; no dictionary content anywhere in the tree.
- [ ] E11 gates pass.

**Both mode (W6)**
- [ ] `init` offers three modes; `both` writes the block, wires the Stop hook, and wires no injector.
- [ ] `status` at `both` reports both layers; `update` moves both artefacts in one pass.
- [ ] `unhook` degrades to `memory` and says so; re-`init` restores.
- [ ] E12 gate passes: measured bounce rate at `both` is lower than hook-only.

**Release**
- [ ] Version bumped; self-pin matches; P1–P14 stay green; new phases green; deterministic evals wired into `eval:deterministic`; tagged.

## 7. Build phasing (extends P1–P14; one commit per phase)

| Phase | Scope | Proof gate (commit blocker) |
|-------|-------|------------------------------|
| **P15** | W6 both mode: pref value, init third choice, wiring minus injector, `unhook`/`status`/`update` behaviour | A31, A32; E12 recorded |
| **P16** | W5 STE register: lexicon `## Register rules`, `lint.py --register`, SKILL.md contract, memory-line swap, init/update plumbing | A30, A33–A35; E11 recorded; E1 extended set green |

P15 first: it is mechanical plumbing with no new language design, and P16's register question lands on an init flow that already asks about mode correctly.

## 8. Open questions

- **Sentence cap: 20 or 25?** RESOLVED (2026-08-15): 25 words flat. STE's real rule is 20 (procedural) / 25 (descriptive); the linter cannot tell the two apart, and the house rule is that a false positive — bouncing a good reply — is the worst failure class. Revisit only if E11 shows replies routinely landing 21–25 and reading like run-ons.
- **Init question count is now five.** RESOLVED (2026-08-15): keep five plain questions interactively — independent axes stay visible, extending the 0.2.0 resolution — and add `init --defaults`, which takes every default and asks nothing. Scripts and piped installs skip the interview. Ships in P16 with the register question.
- **Does `ste` want a terse-voice interaction note?** RESOLVED (2026-08-15): one line in the SKILL.md register section, no new rule machinery — the cap applies per bullet, the contraction rule applies everywhere. Terse bullets are already short, so the cap rarely binds.
