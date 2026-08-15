# Changelog

All notable changes to this project will be documented here.

## 2026-08-15

### [1] — Release-notes history is complete: readers can see what every version gave them

- `RELEASES.md`: backfill user-facing notes for v0.1.0 (initial release: style contract, memory/hook modes, two voices, Claude Code + Codex, checksummed installer) and v0.2.0 (conciseness levels, Slack register, help command, hardening), newest-first below v0.3.0

## History

- 2026-08-14 — P0: repo init + v0.1.0 plan with build phasing
- 2026-08-14 — P1: core skill, lexicon, deterministic linter, E1/E6 evals
- 2026-08-14 — P2: CLI scaffold + memory mode (marker block, 4 targets, idempotent)
- 2026-08-14 — P3: Claude Code hook mode — Stop hook, lint-and-bounce, one-bounce guard, telemetry
- 2026-08-14 — P4: Codex adapter — hooks.json wiring, notify audit fallback, shared core, E7 parity
- 2026-08-14 — P5: utils — status table, update with .bak, version check, unhook with post-check
- 2026-08-14 — P6: packaging — npm tarball allowlist, checksummed install.sh, README, LICENSE
- 2026-08-14 — docs: public-facing files — README header/badges/FAQ, SECURITY, llms.txt, repo URLs
- 2026-08-14 — build: wire cURL installer to the published npm tarball
- 2026-08-14 — plan: v0.2.0 — help util, conciseness levels, Slack register, hardening; all §8 questions resolved
- 2026-08-14 — P7: W1 help — lib/help.js single-source COMMANDS table, topic help, dispatcher slimming
- 2026-08-14 — P8: W4 hardening — lexicon parse cache, atomic writes, hits rotation, python3 fail-open, pref key preservation
- 2026-08-14 — P9: W2 conciseness — lexicon rules with active-at levels, --conciseness (high fallback), fourth init question, SKILL.md band contract, SessionStart injection
- 2026-08-14 — P10: W3 language — 11 formal-connective strip rules, lang-slack-register, long-sentence check, memory template in register
- 2026-08-14 — P11: lang-function-over-inventory (med-only) — report function not parts; anti-loss invariant scoped to retrievable enumerations
- 2026-08-14 — P12: Codex SessionStart parity — same injector script via .codex/hooks.json, version-floor gated, unhook removes both
- 2026-08-14 — release prep v0.2.0: version bump + self-pin, eval:p7–p10 wired, installer pins refreshed, run_p6 contract repair; E8/E9 recorded — gates FAIL, tag withheld
- 2026-08-14 — build: refresh installer checksum to the HEAD-state tarball (note: re-pin from the published tarball at publish)
- 2026-08-14 — plan: v0.2.0 patch 1 — make conciseness levels operative, convo prose provable, register counter-exemplars, causal links as facts
- 2026-08-14 — P13: patch1 — word-budget procedure, two-sided bands, in-band exemplars (old ones taught ~50% cuts), convo bullet gate (A27), register counter-exemplars, causal links as facts; eval:p13 wired
- 2026-08-14 — record: E8/E9 patch1 re-record — still FAIL; dial now measurable (11.3pt spread), A27 real measurement shows convo→bullets as dominant failure
- 2026-08-14 — record: E8/E9 rig2 — real installed environment; E8 low/med/losses/A27 all PASS, high undercuts; E9 register-bound FAIL; rig ships as evals/record_rig.py
- 2026-08-14 — P14: two-position dial — low + high, med promoted to high (rig2-proven bands 10–20 / 25–35); med survives as silent legacy alias; E9 register recorded as known gap
- 2026-08-14 — build: commit package-lock.json
- 2026-08-15 — plan: v0.3.0 — STE-inspired register (W5) + mode:both with no-duplication contract (W6); open questions resolved 2026-08-15; lint-verified before/after examples
- 2026-08-15 — P15: mode both — block prevents, hook enforces, injector deliberately absent (A31); unhook degrades both→memory (A32); status layer table, update one-pass re-render; run_p15 74/74, chain green; E12 recording deferred to release
- 2026-08-15 — P16: STE-inspired register — lexicon register table + ste-contraction, lint --register with slack fallback (A33), 25-word structural cap, memory line swap under the 9-line budget (A30), five init questions + --defaults, register rides hook/injector/update; run_p16 153/153, chain P1-P16 green; E11 recording deferred to release
- 2026-08-15 — release: v0.3.0 — version + self-pin + install.sh repin; E11/E12 recorded (rig: driver.py recovered, record_v030.py, register-aware provisioning): STE deterministic gates 40/40 clean, judged axis + E12 gate FAIL as documented rubric/gate-design gaps (README Known gaps, evals/records/e11-e12-v0.3.0)
- 2026-08-15 — build: post-publish repin — install.sh SHA-256 to published 0.3.0 tarball, lockfile self-pin 0.2.0→0.3.0
