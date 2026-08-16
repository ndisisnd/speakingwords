# Releases

What's new for you, release by release.

## 0.3.1 — 2026-08-17

> Style corrections take up far less room in your terminal. When the checker catches a slip and asks for a rewrite, it now shows one compact line naming the broken rules — plus a short quote of each phrase to fix — instead of a multi-line report.

### 📈 Improved
- When the style checker bounces a reply for a rewrite, its feedback is now a single line — `Rule(s) violated: …` — followed by one short quote per offending phrase. You see exactly what to fix without a wall of text scrolling past.

## 0.3.0 — 2026-08-15

> You can now switch your assistant's writing to a Simplified Technical English register — short sentences, no contractions, active voice — built for runbooks, procedures, and readers whose first language isn't English. A new "both" mode pairs the always-present style reminder with active enforcement, and never states a rule twice.

### ✨ New
- Choose a writing register at setup: keep the casual, conversational default, or switch to an STE-inspired register that caps every sentence at 25 words, bans contractions, and favours active, one-instruction-per-sentence writing.
- A new "both" mode layers prevention and enforcement: the standing reminder stops most style slips before they happen, and the checker catches the rest. Each rule reaches the assistant exactly once, so you get both layers without paying for duplicated context.
- Set up without the interview: `init --defaults` takes every default and asks no questions, so scripted and piped installs run hands-free.
- Change register later with a plain request — "simplified technical english" or "back to slack register" — no reinstall needed.

### 📈 Improved
- In "both" mode, status reports each layer separately, so you can see at a glance what is preventing and what is enforcing.
- Turning off enforcement in "both" mode now steps down gracefully to reminder-only — and says so — instead of tearing everything out.
- The installer verifies the exact published package by checksum before installing, so you always get the bytes this release shipped.

### 🐛 Fixed
- Style prevention no longer silently disappears when a session resumes without a fresh start: the reminder is always in context, closing a gap where enforcement-only setups could run a session with no guidance at all.

## 0.2.0 — 2026-08-14

> You can now dial how much of a reply survives: a conciseness level rides alongside the voice you picked, and the assistant's grammar loosens to match a colleague-in-a-Slack-DM register. A new help command explains every command without leaving the terminal.

### ✨ New
- Pick a conciseness level at setup — a tight band or a fuller one — and the assistant's replies are measured against it, not just nudged toward it.
- Replies now read like a colleague in a DM: formal connectives ("furthermore", "moreover") are stripped, contractions are encouraged, and overlong sentences bounce.
- A built-in help command documents every command and topic, so you never need the README to remember a flag.
- Style guidance now loads at the start of every session automatically, on both supported assistants.

### 📈 Improved
- Replies at the "low" level now report what something does rather than inventorying its parts, keeping short answers genuinely short without losing facts.
- The conciseness dial simplified to two proven positions — low and high — after measurement showed the middle setting wasn't distinct; old "med" settings keep working as an alias.
- Sturdier under failure: settings writes are atomic, style-rule reads are cached, and a missing Python never blocks your reply.

### 🐛 Fixed
- Updating your preferences no longer drops keys it doesn't recognise, so config written by a newer version survives a round-trip.

## 0.1.0 — 2026-08-14

> First release. speakingwords makes your AI assistant write the way you would — it installs a style contract into your assistant's memory, and can enforce it by bouncing a reply that breaks the rules and having the assistant rewrite it before you see it.

### ✨ New
- Install a writing-style contract in one line, with a checksum-verified installer.
- Choose your enforcement mode: a standing memory block that steers every reply, or a hook that lints each reply and bounces violations for one rewrite.
- Pick a voice at setup — terse point-form or conversational prose — and the rules reshape replies to match.
- Works with both Claude Code and Codex, with the same rules and the same behaviour on each.
- Check on the install any time: a status view shows what's wired and how often the linter has fired, and update/unhook let you change or cleanly remove it.
