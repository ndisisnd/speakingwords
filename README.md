# speakingwords

**An output parser for agent replies.** You pick how your agent should sound, once, and
it keeps sounding that way — without you restating it every session.

Agent output drifts. It leans on stock vocabulary ("Landed", "Sweep", "Great point"), it
narrates instead of answering, and it slides back to its default voice no matter how many
times you ask for something terse. That is a configuration problem being solved as a
conversation problem. `speakingwords` turns the preference into installed configuration.

Works with **Claude Code** and **OpenAI Codex CLI**.

---

## Install

Requires **Node.js >= 18** and **Python 3** (the linter is Python; the CLI is Node).

**npm**

```sh
npm i -g speakingwords
```

**cURL** — for people who do not want a global npm install

```sh
curl -fsSL <install-url> | sh
```

Both paths install the same files. The cURL script downloads the release tarball, checks
its SHA-256, unpacks it to `~/.speakingwords/app`, and symlinks `speakingwords` into
`~/.local/bin` (telling you if that is not on your PATH).

> **v0.1.0 note.** There is no published release endpoint yet, so `install.sh` ships with
> no baked-in URL. Pass one:
>
> ```sh
> SPEAKINGWORDS_URL=<tarball-url> SPEAKINGWORDS_SHA256=<sum> sh install.sh
> ```
>
> It **refuses to install without a checksum** unless you pass `--insecure` deliberately.
> Removing it again: `sh install.sh --uninstall`.

Then:

```sh
speakingwords init
```

---

## The two decisions

`init` asks three questions — mode, agent + scope, voice — and then prints exactly what it
wrote and where.

### Mode: how hard the rules are enforced

| Mode | Mechanism | Reliability | Footprint |
|------|-----------|-------------|-----------|
| **memory** | Writes up to 9 point-form rule lines into your agent's memory file | Lower — suggestive; the agent can still drift | Plain instruction lines. No scripts, no hooks. |
| **hook** | Installs a Stop hook that lints every finished reply and bounces violations | Higher — checked on every reply | A hook entry, the linter, the lexicon, a telemetry log |

Memory mode is honest about what it is: a suggestion the agent may ignore. Hook mode is
the one that actually holds.

### Voice: what the output looks like

| Voice | Shape |
|-------|-------|
| **terse** | Point form only. Bullets, short lists, tables, code. Brevity wins every trade-off. |
| **convo** | Prose is retained. Point form is allowed, but brevity is not forced. |

Both voices obey the same language rules. Switching voice changes the *shape* of a reply,
never the standard of the language.

### Scope

`local` writes to the current project; `global` writes to your home config, so it applies
everywhere.

| | Claude Code | Codex CLI |
|---|---|---|
| Memory target, local | `CLAUDE.local.md` in the project | `AGENTS.md` in the project |
| Memory target, global | `~/.claude/CLAUDE.md` | `~/.codex/AGENTS.md` |
| Hook wiring, local | `.claude/settings.json` | `.codex/hooks.json` |
| Hook wiring, global | `~/.claude/settings.json` | `~/.codex/hooks.json` |
| Installed skill root | `~/.claude/skills/speakingwords/` | `~/.codex/speakingwords/` |

Installing for **both** agents ships one shared core at the Claude Code root and points the
Codex wiring at it — one lexicon, one linter, one log. Tuning the rules once changes
behaviour on both.

---

## How enforcement works

A rendered reply cannot be silently rewritten after the fact, so hook mode does not try.
It **lints and bounces**:

1. The agent finishes a reply. The **Stop hook** fires.
2. `lint.py` runs the **deterministic pass**: every strip rule (a regex from the lexicon)
   against the reply text, plus a structural check in terse voice that flags paragraph-form
   answers. Code blocks are excluded from the structural check.
3. **Clean** → the hook exits silently. Nothing is shown, nothing is logged.
4. **Violation** → the hook returns `{"decision": "block", "reason": ...}` with the list of
   what tripped. The agent then does the **probabilistic pass**: it reads `SKILL.md` and
   rewrites its own reply against the voice contract.
5. Every violation on a bounce is appended to `hits.jsonl` — the data `status` reads.

Two guarantees matter more than the catching:

- **One bounce maximum.** The hook reads the host's `stop_hook_active` flag. A reply that
  has already been bounced once is approved whatever it says. There is no lint loop.
- **Fail-open, always.** Malformed input, a missing transcript, an unreadable lexicon, a bug
  in the linter — every one of them approves the reply, and the hook always exits 0. The
  worst thing a broken install can do is let a bad reply through. It can never eat a good
  reply or wedge your session.

A false positive is the worst failure class here, so the shipped rules are anchored
narrowly: `Landed` only at the start of a line ("the plane landed safely" is fine),
`Certainly` only with punctuation after it ("certainly true" is fine), `elevate` but never
"elevator".

### Codex specifics

- **Trust.** Codex will not run a hook until you grant it trust, once. The installer says so
  explicitly. Until you do, Codex replies pass unlinted.
- **Codex below v0.124.0 is audit-only.** The stable hooks engine starts at v0.124.0. On
  anything older there is no Stop hook to wire, so the installer uses the `notify`
  (`agent-turn-complete`) fallback instead: replies are linted *after* the turn is already
  delivered, and violations are logged for `status`, but **nothing is blocked or rewritten**.
  The install summary states this plainly rather than burying it. Upgrade Codex and re-run
  `init` to get real enforcement.
- The `notify` key is user-level only in Codex — there is no per-project equivalent — so the
  audit pass applies everywhere even if you chose `local`. If some other tool already owns
  `notify`, the installer refuses rather than overwriting it.

---

## Commands

```
speakingwords init [flags]     install a style contract
speakingwords version          print the installed version
speakingwords status           show what the linter caught (hook mode)
speakingwords update "<hint>"  tune the rules from one line of English
speakingwords unhook [--yes]   remove hook wiring (alias: unset)
```

### init

```sh
speakingwords init                                  # asks three questions
speakingwords init --hook --agent claude --scope global --voice terse
```

| Flag | Values |
|------|--------|
| `--memory` / `--hook` | mode |
| `--agent` | `claude` · `codex` · `both` |
| `--scope` | `local` · `global` |
| `--voice` | `terse` · `convo` |

Passing all four skips every question, which is what makes it scriptable. Re-running `init`
replaces the memory block in place rather than adding a second one.

### status

Aggregates `hits.jsonl` into a table: rule, how often it fired, one real example of what
tripped it, when it last fired.

Under memory mode it prints one line explaining there is nothing to count — memory mode
installs no hook, so there is no telemetry — and exits 0. A half-written log line from a
killed process is skipped and counted, never thrown.

### update

Tune the rules from plain English. Say what you want **less** of or **more** of:

```sh
speakingwords update "less emoji"
speakingwords update "no game-changer, stop saying dive into"
speakingwords update "more robust"          # allow a word again
```

`less X` · `no X` · `stop saying X` · `ban X` · `avoid X` → adds a strip rule.
`more X` · `allow X` · `unban X` · `stop flagging X` → removes one.

Edits land in the installed `refs/lexicon.md`, which is the file the linter actually reads,
so a new rule is live on the very next reply — no reinstall. Every touched file gets a
`.bak` beside it first; **if the backup cannot be written, nothing is edited.** In memory
mode the memory block is re-rendered too, so the two never disagree.

### unhook (alias: unset)

Removes the hook wiring for every agent in your install: the Claude Code `settings.json`
entry, the Codex `hooks.json` entry, and the Codex `config.toml` notify line. It tells you
exactly which files it will touch *before* touching them, and a bare Enter means no.
`--yes` skips the question, not the report.

What stays: `hits.jsonl` and the installed skill files. Your record of what was caught is
yours; turning enforcement off is not deleting your history.

After removing, it greps every file it touched for `speakingwords` and prints the count. A
non-zero count is a loud failure and a non-zero exit — a half-removed hook is worse than
either state.

`unset` is the same code path, so the two can never drift.

---

## The rules

Everything the linter knows lives in one file, `refs/lexicon.md`. Nothing is hardcoded.

- **Strip rules** — 28 shipped by default. Deterministic regex: banned vocabulary, stock
  openers, marketing verbs. Each row is `id`, `pattern`, `severity`, `guidance`.
- **Language rules** — 8 shipped. Positive rules a regex cannot judge (plain words over
  jargon, no self-narration, no sycophantic openers, lead with the answer), each with one
  before → after exemplar. The rewrite pass applies these by reading.

To park a rule without deleting it, prefix its id with `#`.

---

## Installed layout

```
<skill root>/speakingwords/
├── SKILL.md              # the rewrite skill: voice contract + language rules
├── refs/lexicon.md       # strip rules + language rules — the file `update` edits
├── scripts/
│   ├── lint.py           # the deterministic pass
│   ├── hook_stop.py      # Claude Code Stop hook
│   ├── hook_codex.py     # Codex Stop hook (imports hook_stop — one verdict, two agents)
│   └── notify_codex.py   # Codex audit-only fallback
├── pref.json             # { agents, mode, scope, voice, version }
└── hits.jsonl            # hook-mode telemetry, append-only
```

`lint.py` also runs standalone:

```sh
python3 lint.py --voice terse reply.txt
cat reply.txt | python3 lint.py --voice convo
```

It exits `0` on clean input and `2` on violations, and never any other code. The hook
wiring depends on that contract.

---

## What v0.1.0 does not do

- No per-project rule profiles — one active profile per scope.
- No editing of *your* prompts. This is an output parser only.
- No agents beyond Claude Code and Codex CLI.
- No Codex VS Code extension support — hooks are known not to fire there.
- No GUI. Terminal only.

---

## Development

```sh
npm run eval          # all six deterministic phase gates, offline, no model calls
npm run eval:p1       # …through eval:p6 to run one
```

The evals are fixture-driven and touch nothing outside a temp tree:
`SPEAKINGWORDS_HOME` fakes the home directory and `SPEAKINGWORDS_CODEX_VERSION` fakes the
installed Codex, so every path runs on a machine that has neither agent installed.

## License

MIT — see [LICENSE](LICENSE).
