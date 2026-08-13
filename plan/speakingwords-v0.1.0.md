# speakingwords — v0.1.0 Plan

**An output parser for agent replies. The user picks one of two installation modes — memory or hook — and one of two voice modes — terse or convo. The tool then keeps every agent reply in that shape, permanently, without the user having to repeat themselves.**

---

## 1. Problem

Agent output is often convoluted. It leans on stock vocabulary ("Landed", "Sweep", "Great point"), pads answers with narration, and drifts back to its default voice no matter how many times the user asks for something terse or plain.

The cost is not one bad reply — it is the *repetition*. Users who want a consistent output style have to restate it in every session, and even then compliance decays over a long conversation. Style preferences are a configuration problem, and today they are being solved as a conversation problem.

## 2. Solution

`speakingwords` is a skill that turns a style preference into installed configuration. It offers two enforcement strengths, because users sit at different points on the reliability-vs-invasiveness trade-off:

| Mode | Mechanism | Reliability | Footprint |
|------|-----------|-------------|-----------|
| **Memory** | Writes <10 point-form style rules into `CLAUDE.md` / `CLAUDE.local.md` (user picks local or global) | Lower — suggestive; the agent can still drift | Minimal — plain instruction lines, no scripts, no hooks |
| **Hook** | Installs a hook that fires when the agent provides output, lints the reply against a lightweight SKILL, and forces a rewrite when it violates the rules | Higher — enforced on every reply | A hook entry in settings, a linter script, and a lexicon file |

Within either mode, the user also picks a **voice**:

- **terse** — point-form only. Brevity first; concise and succinct wins every trade-off.
- **convo** — conversational. Prose is retained; point form is allowed but brevity is *not* forced.

Both modes share the same language rules, so switching modes never changes *what* good output looks like — only how strongly it is enforced.

The tool supports two agents — **Claude Code** and **OpenAI Codex CLI** — through one shared core and thin per-agent adapters (see §4.4). The rules, the linter, and the voice contract are identical on both; only the install wiring differs.

## 3. Goals and non-goals

**Goals (v0.1.0)**

- One-command setup that installs either mode and either voice, on either supported agent (Claude Code or Codex CLI).
- A shared rule set: human-sounding language rules plus a strip-list of banned vocabulary.
- Hook mode is measurable: every catch is logged, and `status` shows the user what was caught.
- The user can tune the rules (`update`), see the version (`version`), and cleanly get out (`unhook`).
- Distributable as an npm package and as a cURL one-liner.

**Non-goals (v0.1.0)**

- No per-project rule profiles (one active profile per scope).
- No editing of *user* prompts — this is an output parser only.
- No support for agents other than Claude Code and Codex CLI (the adapter layer leaves the door open; nothing else is built).
- No Codex VS Code extension support — hooks are known not to fire there (upstream bug); v0.1.0 targets Codex CLI only.
- No GUI; terminal only.

## 4. How each mode works

### 4.1 Memory mode

The design constraint is the line budget: **fewer than 10 individual point-form lines**, because agent instruction files compete for attention and long blocks get skipped. The installer:

1. Asks local or global. The target file comes from the active adapter — Claude Code: `CLAUDE.local.md` (local) / `~/.claude/CLAUDE.md` (global); Codex: `AGENTS.md` in the project root (local) / `~/.codex/AGENTS.md` (global). Same rendered block in all four targets.
2. Renders the chosen voice's rule set into ≤9 bullet lines inside a fenced, marker-delimited block (`<!-- speakingwords:start -->` … `<!-- speakingwords:end -->`) so `update` and `unhook` can find and replace it without touching the user's other content.
3. Never duplicates: re-running install replaces the existing block in place.

Memory mode is honest about its nature — the docs and the installer both say it is *suggestive, not enforced*, and point at hook mode for reliability.

### 4.2 Hook mode

The hard truth about hooks: an agent's rendered reply cannot be silently rewritten after the fact. So enforcement is **lint-and-bounce**, not post-hoc editing:

1. A **Stop hook** fires when the agent finishes a reply. Both agents expose this event: Claude Code wires it in `settings.json`; Codex (hooks engine GA since May 2026, stable from v0.124.0) wires it in `~/.codex/hooks.json` or project `.codex/hooks.json`. Codex deliberately mirrors Claude Code's lifecycle event names, stdin payload shape, and `{"decision": "block", "reason": …}` contract — so **one linter and one bounce flow serve both agents**; only the adapter's install step differs.
2. A linter script (`lint.py`) runs the **deterministic pass** over the final message: regex scan against the lexicon (banned words, phrases, stock openers), plus structural checks for the active voice (e.g. terse mode flags paragraph-form answers over a length threshold).
3. **Clean** → the hook exits silently, zero added latency beyond the scan (<100 ms budget).
4. **Violations** → the hook blocks and returns the violation list as feedback. The agent then performs the **probabilistic pass**: it rewrites its reply following the lightweight SKILL (the language rules, the voice contract, before→after exemplars). One bounce maximum per reply — a loop guard prevents infinite lint cycles.
5. Every violation is appended to `hits.jsonl` (timestamp, rule id, matched text) — the data source for `status`.

This split is deliberate: the deterministic layer is cheap, testable, and never hallucinates; the probabilistic layer handles what regex can't (tone, structure, sounding human).

### 4.3 The rule set (shared by both modes)

Two rule classes, both defined in `refs/lexicon.md` so `update` has one file to edit:

- **Strip rules (deterministic)** — banned vocabulary and phrases, e.g. "Landed", "Sweep", "Great point", "Great question", "Certainly!", "I'd be happy to". Each entry: pattern, severity, replacement guidance.
- **Language rules (probabilistic)** — positive rules that make output sound human: plain words over jargon, no self-narration, no sycophantic openers, lead with the answer. Each rule ships with one before→after exemplar.

### 4.4 Agent adapters

Everything that defines *what good output is* lives in the shared core (`SKILL.md`, `refs/lexicon.md`, `scripts/lint.py`, `hits.jsonl`) and is byte-identical across agents. Everything that defines *where things plug in* lives in a small adapter per agent:

| Adapter concern | Claude Code | Codex CLI |
|-----------------|-------------|-----------|
| Memory target (local / global) | `CLAUDE.local.md` / `~/.claude/CLAUDE.md` | `AGENTS.md` / `~/.codex/AGENTS.md` |
| Hook wiring | Stop hook in `settings.json` | Stop hook in `~/.codex/hooks.json` (or project `.codex/hooks.json`) |
| Skill install root | `~/.claude/skills/speakingwords/` | `~/.codex/speakingwords/` (referenced from the hook + AGENTS.md block) |
| Hook trust | Standard settings entry | Codex requires hook trust to be granted once; the installer surfaces this step explicitly |

`init` detects which agent is present (both installed → ask; the user can also install for both, which shares one core and wires two adapters). `pref.json` records the agent(s) so every util knows what to touch.

**Degraded path (Codex only):** if the installed Codex version predates the stable hooks engine (< v0.124.0), hook mode falls back to **audit-only** — the `notify` = `agent-turn-complete` mechanism runs the linter over the finished turn and logs hits, but cannot block or bounce. The installer says so plainly and recommends upgrading; `status` still works, enforcement does not.

## 5. Utils

| Command | Modes | Behaviour |
|---------|-------|-----------|
| `status` | Hook only | Aggregates `hits.jsonl` and prints a terminal table: rule / phrase, hit count, last seen. Memory mode prints a one-line explanation that there is nothing to count (no hook, no telemetry) and exits 0. |
| `update` | Both | Interactive tuning. Argument hints ask what the user wants to see **less** of or **more** of. Edits `SKILL.md` or the ref files directly, but first writes a `.bak` backup of every touched file **in the same repo/directory**. In memory mode, also re-renders the CLAUDE.md block so memory and rule files never disagree. |
| `version` | Both | Prints the installed version (single source of truth: `package.json`). |
| `unhook` (alias: `unset`) | Hook only | Warns first ("This removes the Stop hook, the settings entry, and stops enforcement — telemetry is kept. Continue?"), then removes all hook wiring for every agent recorded in `pref.json` (`settings.json` on Claude Code, `hooks.json` and any `notify` fallback on Codex). Prints exactly what was removed. |

## 6. Distribution

- **npm**: `npm i -g speakingwords` → a `speakingwords` bin exposing `init`, `status`, `update`, `version`, `unhook`. `init` walks mode (memory/hook) → scope (local/global) → voice (terse/convo).
- **cURL**: `curl -fsSL <install-url> | sh` — for users without Node. The script verifies its download (checksum), installs the same file tree, and prints the same post-install summary. Both paths must produce byte-identical installed rule files.

## 7. File layout (installed)

```
<agent skill root>/speakingwords/     # ~/.claude/skills/… (Claude) · ~/.codex/… (Codex)
├── SKILL.md              # the lightweight rewrite skill (voice contract + rules)
├── refs/
│   └── lexicon.md        # strip rules + language rules (the file `update` edits)
├── scripts/
│   └── lint.py           # deterministic pass; also powers `status` aggregation
├── adapters/             # per-agent install/uninstall wiring (claude.sh, codex.sh)
├── pref.json             # { agents: [...], mode, scope, voice, version }
└── hits.jsonl            # hook-mode telemetry (append-only, shared across agents)
```

When both agents are installed, one root holds the core and the other agent's wiring points at it — the lexicon is edited once and applies everywhere.

## 8. Evals

Evals are fixture-driven: a corpus of real agent replies (collected, anonymised) with known violations, plus clean controls.

| Eval | Method | Target |
|------|--------|--------|
| **E1 — Strip precision** | Run `lint.py` over 50 violation fixtures + 50 clean controls | ≥95% of planted banned phrases caught; **zero** false positives on clean controls (a false positive bounces a good reply — worst failure class) |
| **E2 — Rewrite fidelity** | For 20 bounced fixtures, have the model rewrite per SKILL.md; grade with an LLM judge on (a) violation removed, (b) meaning preserved, (c) voice contract honoured | ≥90% pass all three |
| **E3 — Terse compliance** | 20 prompts run with terse mode active; judge scores point-form-only + no stock vocabulary | ≥85% clean on first reply (pre-bounce), ≥98% after one bounce |
| **E4 — Convo non-regression** | Same 20 prompts in convo mode; judge confirms prose is retained and brevity is *not* forced | ≥95% — convo must never collapse into terse |
| **E5 — Memory-mode drift** | 30-turn synthetic session with memory mode installed; measure violation rate per 10-turn window | Documented, not gated — this is the honesty number that justifies hook mode's existence |
| **E6 — Latency** | Time `lint.py` on a 4,000-word reply, 100 runs | p95 < 100 ms |
| **E7 — Cross-agent parity** | Replay the E1 fixture set through both adapters' hook entry points (Claude Stop payload and Codex Stop payload) | Identical verdicts and identical `hits.jsonl` lines for every fixture — the payload adapter introduces zero behavioural difference |

Fixtures and judge prompts live in `evals/` in the repo; `npm run eval` runs E1 and E6 deterministically in CI, E2–E5 on demand (they cost model calls).

## 9. Assertions (mechanical, enforced in code and CI)

- **A1** — Memory-mode block is always ≤9 bullet lines. Installer refuses to write a 10th; CI greps the rendered templates.
- **A2** — Memory-mode writes are idempotent: install → install produces zero diff; the marker block appears exactly once.
- **A3** — `unhook` leaves zero `speakingwords` references in any settings file — Claude `settings.json`, Codex `hooks.json`, and Codex `config.toml` (`notify`) alike (verified by post-run grep in the command itself; it prints the check result).
- **A4** — `update` never edits without first writing `.bak`; if the backup write fails, the edit is aborted.
- **A5** — Hook loop guard: at most one bounce per reply; the second lint pass always exits clean-or-warn, never block.
- **A6** — `lint.py` exits 0 on clean input, 2 on violations, and never any other code; hook wiring depends on this contract.
- **A7** — `version` output equals `package.json` version equals `pref.json` version after install (one source of truth, two mirrors checked).
- **A8** — cURL install and npm install produce identical `SKILL.md`, `refs/`, and `scripts/` checksums.
- **A9** — `hits.jsonl` lines are valid single-line JSON; `status` skips (and counts) malformed lines rather than crashing.
- **A10** — `status` under memory mode exits 0 with the explanatory line — never a stack trace.
- **A11** — Core files (`SKILL.md`, `refs/`, `scripts/`) installed for Claude Code and for Codex have identical checksums; only `adapters/` and wiring differ.
- **A12** — Codex config edits (`hooks.json`, `config.toml`, `AGENTS.md`) are idempotent and preserve all user content outside the marker block / owned entries; install → install produces zero diff, mirroring A2.
- **A13** — On Codex < v0.124.0 the installer never writes a `hooks.json` entry; it installs the `notify` audit fallback and states the downgrade in its summary.

## 10. Acceptance criteria

**Setup**
- [ ] `speakingwords init` completes mode → scope → voice in ≤3 questions and prints what it installed and where.
- [ ] Memory mode: the marker block exists in the chosen CLAUDE file, ≤9 lines, correct voice.
- [ ] Hook mode: the Stop hook fires on the next agent reply with no user action beyond install.
- [ ] Both npm and cURL paths install successfully on a clean macOS machine.
- [ ] Codex: memory mode lands in the correct `AGENTS.md`; hook mode's Stop hook fires and bounces a violating reply on Codex ≥ v0.124.0; the trust-grant step is surfaced during install.
- [ ] Installing for both agents shares one lexicon: an `update` edit changes behaviour on both without a second edit.

**Enforcement**
- [ ] A reply containing "Landed" or "Great point" is bounced once and comes back clean.
- [ ] A clean reply passes with no visible artefact and no perceptible delay.
- [ ] Terse voice yields point-form replies; convo voice yields prose without forced brevity (spot-checked against E3/E4 fixtures).

**Utils**
- [ ] `status` shows a correct hit table after a session with known violations; correct memory-mode fallback message otherwise.
- [ ] `update "less emoji, more contractions"` edits the lexicon, leaves `.bak` files beside the originals, and (memory mode) re-renders the CLAUDE block.
- [ ] `version` prints the semver; `unhook` warns, removes everything on confirm, does nothing on decline, and `unset` behaves identically.

**Quality gates for release**
- [ ] E1 and E6 pass in CI; E2–E4 pass in a recorded run attached to the release.
- [ ] All ten assertions green.
- [ ] Fresh-machine install → violate → status → update → unhook walkthrough completed end-to-end by someone other than the author.

## 11. Milestones

| # | Deliverable | Proof |
|---|-------------|-------|
| M1 | Rule set + `lint.py` + fixtures | E1, E6 pass |
| M2 | Memory mode install/uninstall | A1, A2; setup criteria |
| M3 | Hook mode on Claude Code (Stop hook, bounce, telemetry) | E2, E3, E4; A5, A6 |
| M3.5 | Codex adapter (AGENTS.md target, hooks.json wiring, notify fallback) | E7; A11, A12, A13 |
| M4 | Utils (`status`, `update`, `version`, `unhook`) | A3, A4, A7, A9, A10 |
| M5 | Packaging (npm + cURL) + docs | A8; fresh-machine walkthrough on both agents |

## 12. Open questions (resolve before M3)

1. **Bounce visibility** — should the user see that a reply was bounced (a one-line notice) or should enforcement be invisible? Leaning invisible, with `status` as the audit trail.
2. **Judge model for E2–E4** — pin one model for reproducibility, or run the current default? Pin, and record it in the eval output.
3. **Lexicon seeding** — ship an opinionated default strip-list, or start minimal and let `update` grow it? Leaning opinionated default (~25 entries), because an empty linter teaches nothing on day one.
4. **Codex Stop-block semantics** — public docs confirm payload/decision parity with Claude Code, but the exact bounce behaviour (does a blocked Stop re-prompt the model the same way?) must be verified empirically on a pinned Codex version at the start of M3.5. If bounce semantics differ, the Codex adapter ships audit-only in v0.1.0 and enforcement follows in v0.2.0.

## 13. Build phasing (execution sequence)

The milestones in §11 become seven sequential phases. **Each phase is exactly one commit**, made by the orchestrator after the phase's proof passes — build agents never commit. Phases are strictly ordered; each builds on the artefacts of the previous one.

**Repo layout (source tree — distinct from the installed tree in §7):**

```
speakingwords/
├── plan/                  # this document
├── skill/                 # shipped core: SKILL.md, refs/lexicon.md, scripts/lint.py
├── adapters/              # per-agent wiring: claude.js, codex.js
├── bin/speakingwords.js   # CLI entry: init · status · update · version · unhook
├── lib/                   # CLI internals shared by bin + adapters
├── evals/                 # fixtures + runners (E1, E6, E7 deterministic; E2–E5 prompts)
├── install.sh             # cURL path (mirrors npm install output; A8)
└── package.json
```

| Phase | Scope | Proof gate (commit blocker) | Maps to |
|-------|-------|------------------------------|---------|
| **P0** | Repo init, phasing plan, `.gitignore` | — (orchestrator only) | — |
| **P1** | Core: `skill/SKILL.md`, `skill/refs/lexicon.md` (~25 seed strip rules + language rules), `skill/scripts/lint.py` (exit 0/2 contract, terse/convo structural checks), fixtures + deterministic eval runner | E1 ≥95%/0 FP, E6 p95 <100 ms; A6 | M1 |
| **P2** | CLI scaffold + memory mode: `package.json`, `bin/`, `init` (mode→scope→voice, ≤3 questions), marker-block renderer for all four memory targets (Claude local/global, Codex local/global), idempotent writes | A1, A2, A12 (memory targets) | M2 |
| **P3** | Hook mode, Claude Code: Stop-hook wiring in `settings.json`, bounce feedback, one-bounce loop guard, `hits.jsonl` telemetry | A5, A6 re-verified end-to-end | M3 |
| **P4** | Codex adapter: `hooks.json` wiring + trust-step surfacing, version detection, `notify` audit fallback (<v0.124.0), shared-core pathing | E7 parity; A11, A13 | M3.5 |
| **P5** | Utils: `status` (table + memory-mode fallback), `update` (less/more hints, `.bak`, memory re-render), `version`, `unhook`/`unset` (warn → remove → post-check) | A3, A4, A7, A9, A10 | M4 |
| **P6** | Packaging: npm publish readiness, `install.sh` with checksum verification, README, npm↔cURL parity | A8; fresh-tree walkthrough | M5 |

**Orchestration contract:** the orchestrator dispatches one build agent per phase (Opus tier), sequentially. The agent reads this plan, builds only its phase's scope, runs its proof gate, and reports. The orchestrator reviews the diff, re-runs the gate, commits (`P<n>: <scope>`), then dispatches the next phase. A failed gate bounces back to the same agent once; a second failure halts the sequence for human review.
