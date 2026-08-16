# speakingwords — v0.3.1: compact bounce feedback

**One change: the Stop-hook bounce feedback becomes terse. `build_reason()` in `skill/scripts/hook_stop.py` replaces the multi-line "broke N style rules" header and per-rule severity bullets with a single `Rule(s) violated: a, b` line, one quoted-match locator per rule, and a one-line rewrite instruction. No lexicon change, no lint change, no pref keys, no new capability. Codex inherits the change through `decide()`.**

---

## 1. Problem — the feedback string floods the terminal

- The bounce feedback is the most verbose thing the hook prints.
- The old format spent four framing tokens before the first rule: a header sentence, a blank line, then `rule (severity):` per bullet, then a blank line, then a three-clause rewrite instruction naming voice, conciseness, and register.
- The user reads the same rule set on every bounce. The header sentence and the severity tag add words without adding a decision.
- The conciseness and register tokens in the rewrite line are already on disk in `pref.json`. Restating them in the feedback is redundant.

## 2. Solution — Path B (terse header, keep the locators)

Two paths were weighed:

- **Path A** — emit only `Rule(s) violated: a, b`. Drops the quoted match. Cheapest, but the rewrite loses its span locator, so the agent must re-scan its own reply to find each offending span.
- **Path B** — terse header plus one compact quoted-match bullet per reported violation. One extra line per rule, locator preserved.

**Path B ships.** The locator is what makes the rewrite a targeted edit instead of a full re-read.

### The new format

```
Rule(s) violated: <distinct rule ids, first-seen order>
- <rule>: "<matched span>"
- <rule>: "<matched span>"
Rewrite the last reply following <SKILL.md> — <voice> voice. Do not mention the correction.
```

- Line 1 de-duplicates rule ids across all violations, in first-seen order.
- One bullet per **reported** violation (capped at `MAX_REPORTED_VIOLATIONS`), each quoting `item["match"]` verbatim through `json.dumps`.
- The `... and N more of the same kind.` overflow line is unchanged.
- The rewrite line drops the conciseness and register clauses. It keeps the SKILL.md path, the voice token, and `Do not mention the correction.`

### What stays

- `build_reason()` keeps its `conciseness` and `register` parameters. `decide()` still passes them, and the linter still reads them from `pref.json`. Only their printing is removed.
- The 4000-char cap and the `MAX_REPORTED_VIOLATIONS` truncation are untouched.
- The telemetry record in `hits.jsonl` is untouched — severity still logs, it just stops printing.

## 3. Non-goals

- No lexicon rule rows, no `lint.py` change, no pref keys — the detection layer is proven and untouched.
- No Codex-specific edit. `hook_codex.py` calls `decide()`, which calls the new `build_reason()`; parity holds by construction (E7).
- No model call, no judged eval. This is a deterministic-string change.

## 4. Assertions (extends the P3 set)

- **A34** — The bounce feedback opens with `Rule(s) violated: ` and names every distinct rule id the linter found on that reply.
- The pre-existing P3 obligations remain and still pass unchanged: every rule id present, the matched text quoted, `Rewrite the last reply` present, the SKILL.md path present, the `<voice> voice` token present, `Do not mention the correction` present, reason ≤ 4000 chars.

## 5. Evals

Gates unchanged. The format change is proven deterministically:

| Eval | Gate | Result |
|------|------|--------|
| `run_p3.py` | A2/A5/A6/A9/P3 — new A34 header assertion added | PASS 71/71 |
| `run_deterministic.py` | E1 recall/false-pos, E6 latency, A6 exit codes | PASS |

No recorded (judged) eval is affected.

## 6. Acceptance criteria

- [x] `build_reason()` emits the compact format (header, locator bullets, one-line rewrite).
- [x] `run_p3.py` gains the `Rule(s) violated:` header assertion; the full P3 set stays green.
- [x] `run_deterministic.py` stays green.
- [ ] Version bumped to 0.3.1; changelog and release notes updated.
- [ ] Published; install.sh SHA-256 repinned to the published tarball; lockfile self-pinned to 0.3.1; main + `v0.3.1` tag pushed.

## 7. Release ritual (unchanged from v0.3.0)

`npm publish` rebuilds the tarball, so the SHA-256 baked into `install.sh` before publish never matches. Always download the published tarball, recompute the SHA-256, repin `install.sh`, and `npm install` to move the lockfile self-pin — in one post-publish commit.
