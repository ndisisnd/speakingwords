# Releases

What's new for you, release by release.

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
