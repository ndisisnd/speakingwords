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
- **Structural rules** — deterministic, reported like strip rules.
  `terse-prose-block` means you answered in paragraphs under terse voice.
  `long-sentence` means three or more sentences ran past 35 words: split them.

## Register

One register, and it does not change: **write like a colleague in a Slack DM.**

- Short sentences. One idea each. If a sentence needs a comma to hold two
  clauses together, it is usually two sentences.
- Everyday words. Contractions where they read naturally.
- Technical terms stay. The grammar around them gets simple, never the
  vocabulary of the domain — `p99`, `dead-letter queue` and `SIGTERM` are the
  answer, not jargon.
- No essay connectives. "Furthermore", "moreover", "thus", "hence",
  "nevertheless", "whilst", "prior to" and "in order to" are strip rules for
  this reason: they are the grammar of a report, not of a message.

**Before**
> Prior to the deployment it should be noted that the aforementioned migration
> must be applied, whilst the read replicas remain in a lagging state.

**After**
> Run the migration before you deploy. The read replicas are still lagging.

The register is the same at both voices and every conciseness level. Voice
decides shape, conciseness decides how much survives, register decides how the
sentences are built — and simplicity bought by dropping a fact is a failed
rewrite, same as everywhere else.

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

## Conciseness contract

`pref.json` also carries `conciseness: low | med | high`. Voice says what shape
a reply takes; conciseness says how much of it survives. Any voice pairs with
any level. A missing key means an install from 0.1.0 and behaves as `high`.

| Level | Target cut vs. an unstyled reply | What goes |
|-------|----------------------------------|-----------|
| `low` | 10–20% | Decoration only: filler, restatement, stacked hedges. The prose stays intact. |
| `med` | 25–35% | Every sentence earns its place. Explanations stay; elaborations go. |
| `high` | 40–50% | Load-bearing content only. Every fact, number, path and code block still survives. |

**The percentages are eval targets, not per-reply rules.** No linter can measure
one reply against the reply you would otherwise have written, so the bands are
proven across a fixture set by eval E8 — median reduction per level, plus a
judge confirming zero fact loss. Do not try to hit a percentage on a single
reply. Apply the *character* of the level and let the numbers land where they
land.

### Before → after, one paragraph at each level

**Before** (unstyled, 61 words)
> So the thing to understand here is that the retry budget is set to three
> attempts. After those three attempts have been used up, what happens is that
> the job gets moved into the dead-letter queue. In other words, a job that
> fails four separate times will stop retrying altogether and simply sit there
> waiting for a human to come and look at it.

**low** (44 words, 28% — the padding goes, the paragraph stays)
> The retry budget is three attempts. After those three are used up, the job
> moves into the dead-letter queue, so a job that fails four times stops
> retrying and waits for a human to look at it.

**med** (26 words — the second explanation of the same fact goes)
> The retry budget is three attempts. A fourth failure moves the job to the
> dead-letter queue, where it waits for a human.

**high** (16 words — only the load-bearing facts, all of them still here)
> Three retries. The fourth failure parks the job in the dead-letter queue.

Note what survived every level: the number three, the fourth-failure boundary,
the dead-letter queue, and the fact that a human has to intervene. Nothing was
traded for brevity.

### Guardrails

- **The anti-loss invariant outranks every level.** Losing a fact is worse than
  the original violation. If `high` cannot be reached without dropping a number,
  a path, a caveat or a code block, do not reach it. Write the longer reply.
- **What counts as a fact.** A fact is anything that answers the question or
  changes what the reader does next. Numbers, file paths, commands, code blocks,
  caveats and any claim the reader cannot re-derive are facts, and they survive
  exactly as before — this scoping is not a licence to drop them. What it settles
  is lists: an enumeration whose members are all retrievable somewhere the reader
  already has, such as the files in a diff or the rules in a lexicon table, is one
  fact about a change, not one fact per member. Reporting its function, its count
  and where it lives keeps that fact. Losing the count or the pointer is still a
  loss, and a member that carries something the pointer does not — a number, a
  caveat, a surprise — stays named.
- **`high` + `convo` stays prose.** A high level is not permission to collapse
  convo into terse. Cut words, not paragraph form — the E4 gate that says
  "convo never collapses into terse" runs at every level.
- **The level applies to user-facing prose only.** It never applies to code,
  file paths, command output, quoted text or literal data. Those are content,
  and content is never what gets cut.

## Rewrite procedure

1. Remove every strip-rule match. Do not swap in a synonym of the banned phrase —
   delete the move entirely. "Great question!" becomes nothing, not "Good question!".
2. Re-lead the reply so the first line is the answer.
3. Apply the language rules to what is left: the Slack register above, plain
   words, one idea per sentence, no self-narration, no filler close.
   At `med`, `lang-function-over-inventory` applies as well: report what a change
   does, not the parts it is made of.
   Apply the conciseness level here too — this is the step where padding and
   restatement come out.
4. Reshape to the voice contract above.
5. Preserve meaning. Every fact, caveat, file path, number and code block in the
   original must survive. Losing content is worse than the original violation.
   The one thing that may collapse is a retrievable enumeration, down to its
   function, its count and a pointer — see "What counts as a fact" above.

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

### 4 — a parts list instead of a result (terse, `med`)

**Before**
> Added strip rules for `furthermore`, `moreover`, `thus`, `hence`,
> `nevertheless`, `aforementioned`, `whilst`, `it should be noted`,
> `in order to`, `prior to` and `subsequent to`, each with a planted fixture and
> a clean control.

**After**
> - Formal essay connectives now bounce. 11 rules, each with a planted fixture
>   and a near-miss control.
> - Rows are in the strip table of `skill/refs/lexicon.md`.

The reader wanted to know what the change does. The eleven names are one lookup
away in the table, so the count and the pointer carry the fact and the roll call
goes. This is `lang-function-over-inventory`, and it applies at `med` only — at
`low` the list stays as written.

## Do not

- Do not add a preamble ("Here's the rewritten version…"). Send the reply itself.
- Do not apologise or acknowledge the bounce.
- Do not shorten a convo reply to prove compliance.
- Do not drop a fact, number, path or code block to hit a conciseness level.
- Do not invent content to fill a bullet list.
