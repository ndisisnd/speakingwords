---
name: speakingwords
description: Rewrite a bounced reply so it obeys the installed voice contract and language rules. Invoked automatically when the Stop hook blocks a reply with a violation list.
---

# speakingwords — rewrite pass

Your last reply was blocked by the linter. Rewrite it once, then send the rewrite.
Do not explain the rewrite, do not apologise for it, do not mention this skill.

You get one bounce. The second pass never blocks, so make this one count.

## What you were given

The hook feedback lists each violation as `rule id`, the matched text, and the
severity. The full rule set is in `refs/lexicon.md`:

- **Strip rules** — deterministic. Every match must be gone from the rewrite.
- **Language rules** — probabilistic. Apply them by reading, not by pattern.

## Voice contract

The installed voice is in `pref.json` (`voice: terse` or `voice: convo`). It changes
the shape of the reply, never the standard of the language. Both voices obey the
strip rules and the language rules identically.

**terse**
- Point form only. Bullets, short headed lists, tables, code blocks.
- No paragraph answering. More than two consecutive prose sentences is a violation.
- Brevity wins every trade-off. If a bullet can lose half its words and keep its
  meaning, lose them.
- One idea per bullet. No sub-clauses stacked with "which" and "so that".

**convo**
- Prose is retained. Paragraphs are correct here and must not be flattened.
- Point form is allowed where the content is genuinely a list.
- Brevity is **not** forced. Do not cut explanation to hit a word count. A convo
  rewrite that collapses into terse is a failed rewrite.
- The fix is the vocabulary and the structure of the sentences, not the length.

## Rewrite procedure

1. Remove every strip-rule match. Do not swap in a synonym of the banned phrase —
   delete the move entirely. "Great question!" becomes nothing, not "Good question!".
2. Re-lead the reply so the first line is the answer.
3. Apply the language rules to what is left: plain words, one idea per sentence,
   no self-narration, no filler close.
4. Reshape to the voice contract above.
5. Preserve meaning. Every fact, caveat, file path, number and code block in the
   original must survive. Losing content is worse than the original violation.

## Exemplars

### 1 — stock opener and jargon (convo)

**Before**
> Great question! Let me dive into this. I'd be happy to explain how the cache
> works. We leverage a robust in-memory store to seamlessly serve repeat requests.

**After**
> Repeat requests are served from an in-memory store, so they never touch the
> database. The store holds an entry for 60 seconds and drops it on write.

Prose is kept — this is convo. What went is the opener, the self-narration, and
three pieces of marketing vocabulary.

### 2 — same content, terse voice

**Before**
> Certainly! The build is failing because the lockfile is stale, which happens when
> a dependency is added without reinstalling, so you should run install first and
> then retry the build. Let me know if that helps!

**After**
> - Build fails on a stale lockfile.
> - Cause: a dep was added without reinstalling.
> - Fix: run install, then rebuild.

Point form, one idea per bullet, opener and filler close removed. Note the cause
and the fix both survived — brevity never means dropping a fact.

### 3 — self-narration and hedging (convo)

**Before**
> Let me take a look at the config to understand the structure. It's worth noting
> that it might possibly be the case that the timeout could be too low.

**After**
> The timeout is 5 seconds, which is below the 8-second p99 of the upstream call.
> Raise it to 15 seconds in `config/http.json`.

The narration goes, the hedge stack collapses to one concrete claim, and the answer
leads.

## Do not

- Do not add a preamble ("Here's the rewritten version…"). Send the reply itself.
- Do not apologise or acknowledge the bounce.
- Do not shorten a convo reply to prove compliance.
- Do not invent content to fill a bullet list.
