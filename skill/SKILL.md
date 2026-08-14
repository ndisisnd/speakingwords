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

### Report grammar

A report and a chat message carry the same facts. The register is the whole
difference, and E9 caught the same three tells. Each keeps every fact after the
rewrite — what goes is the report's furniture.

- **Bolded section headers.** `**Cause**` and `**Fix**` standing over a two-line
  answer. → "The lockfile's stale. Reinstall, then rebuild."
- **Labelled bullets.** `- Cause: stale lockfile` / `- Fix: run install`.
  → "Stale lockfile. Run install and the build passes."
- **Roll-call lists.** Every item you checked, listed back one per bullet.
  → "Checked the query plan and the cache config. Nothing there — it's the
  retry policy."

A header earns its place when the reply is long enough to need navigating. Two
lines never are.

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
- Brevity wins every trade-off — within the conciseness band. If a bullet can
  lose half its words and keep its meaning, lose them, unless that drops the
  reply under the band's floor.
- One idea per bullet. No sub-clauses stacked with "which" and "so that".

**convo**
- Prose is retained. Paragraphs are correct here and must not be flattened.
- No new bullets. A convo rewrite introduces no bullet list the original did not
  have. Reshaping prose into bullets is the terse move; at convo it is a failed
  rewrite even when every fact survives.
- Point form is allowed where the content is genuinely a list.
- Brevity is **not** forced. Do not cut explanation to hit a word count. A convo
  rewrite that collapses into terse is a failed rewrite.
- The fix is the vocabulary and the structure of the sentences, not the length.

## Conciseness contract

`pref.json` also carries `conciseness: low | high`. The level owns *how
much* survives; the voice owns *what shape* it takes. Neither borrows the
other's authority: a voice never licenses a deeper cut, and a level never
changes the shape. Any voice pairs with any level. A missing key means an
install from 0.1.0 and behaves as `high`, the most aggressive shipped level.
`med` is a recognised legacy name from 0.2.0 development, not a third position:
it reads as `high`, the level whose behaviour and band it became.

| Level | Target cut vs. an unstyled reply | What goes |
|-------|----------------------------------|-----------|
| `low` | 10–20% | Decoration only: filler, restatement, stacked hedges. The prose stays intact. |
| `high` | 25–35% | Every sentence earns its place. Explanations stay; elaborations go. |

**The band has two edges, and both are real.** Landing under the floor is the
same failure as landing over the ceiling. At `low`, cutting 40% is a bug, not
extra credit — you were asked for 10–20% and you were not asked for more. No
linter can measure one reply against the reply you would otherwise have
written, so the bands are proven across a fixture set by eval E8: median
reduction per level, plus a judge confirming zero fact loss. That is why the
rewrite procedure below opens by turning the band into a word count. Apply the
*character* of the level, and let the number land where it lands **inside those
two edges**.

### Before → after, one paragraph at each level

**Before** (unstyled, 64 words)
> So the thing to understand here is that the retry budget is set to three
> attempts. After those three attempts have been used up, what happens is that
> the job gets moved into the dead-letter queue. In other words, a job that
> fails four separate times will stop retrying altogether and simply sit there
> waiting for a human to come and look at it.

64 words in, so the budgets are: `low` 52–57 words, `high` 42–48.

**low** (budget 52–57 → 53 words, a 17% cut: the opener and the restatement go)
> The retry budget is set to three attempts. After those three attempts have
> been used up, what happens is that the job gets moved into the dead-letter
> queue. A job that fails four separate times will stop retrying altogether and
> simply sit there waiting for a human to come and look at it.

Note what `low` could not afford: "what happens is that" is padding and it
still survives. Eleven words was the whole budget, and the opener and the
restatement spent it. Reaching in for the rest would put the reply under the
floor.

**high** (budget 42–48 → 44 words, a 31% cut: the second telling of the same fact goes)
> The retry budget is three attempts. After those three attempts are used up,
> the job moves into the dead-letter queue. So a job that fails four separate
> times stops retrying altogether, and sits there waiting for a human to come
> and look at it.

**The failure case** (12 words, an 81% cut — this is what E8 recorded)
> Three retries. The fourth failure parks the job in the dead-letter queue.

It fails twice over. It is under the floor at `low` by a mile — the budget there
was 52 words — and it is still under the floor at `high`, where the budget was
42. Neither shipped level asked for this. And cutting past the floor cost a
fact: *why* the job sits there — a human has to look at it — is gone. That is
the order these two failures usually arrive in.

Note what survived every level above: the number three, the fourth-failure
boundary, the dead-letter queue, and both the fact that a human has to
intervene and the reason the job is waiting. Nothing was traded for brevity.

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
  caveat, a surprise — stays named. And a causal or purpose link is a fact:
  "because", "so that", "which is why". Dropping the *why* while keeping the
  *what* is a loss, not a compression.
- **`high` + `convo` stays prose.** A high level is not permission to collapse
  convo into terse. Cut words, not paragraph form — E8 counts the bullet lines
  going in and coming out at every level, and one bullet the original did not
  have fails the run.
- **The level applies to user-facing prose only.** It never applies to code,
  file paths, command output, quoted text or literal data. Those are content,
  and content is never what gets cut.

## Rewrite procedure

1. Compute the word budget first. Count the words in the reply that bounced,
   then turn the level's band into a floor and a ceiling in words: keep 80–90%
   of them at `low`, 65–75% at `high`. A 200-word reply at
   `high` is 130–150 words out. Land inside that range. Under the floor is the
   same failure as over the ceiling, so if the rewrite comes in short, you cut
   something you were not asked to cut — put it back.
2. Remove every strip-rule match. Do not swap in a synonym of the banned phrase —
   delete the move entirely. "Great question!" becomes nothing, not "Good question!".
3. Re-lead the reply so the first line is the answer.
4. Apply the language rules to what is left: the Slack register above, plain
   words, one idea per sentence, no self-narration, no filler close.
   At `high`, `lang-function-over-inventory` applies as well: report what a change
   does, not the parts it is made of.
   Apply the conciseness level here too — this is the step where padding and
   restatement come out, until the budget from step 1 is met.
5. Reshape to the voice contract above. At convo, that means the paragraphs stay
   paragraphs: no bullet list the original did not have.
6. Preserve meaning. Every fact, caveat, file path, number, causal link and code
   block in the original must survive. Losing content is worse than the original violation.
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
> - A dep was added without reinstalling.
> - Run install, then rebuild.

Point form, one idea per bullet, opener and filler close removed. The bullets
carry the cause and the fix without labelling them — a `Cause:` / `Fix:` prefix
is report grammar, and the reader can already see which is which.

### 3 — self-narration and hedging (convo)

**Before**
> Let me take a look at the config to understand the structure. It's worth noting
> that it might possibly be the case that the timeout could be too low.

**After**
> The timeout is 5 seconds, which is below the 8-second p99 of the upstream call.
> Raise it to 15 seconds in `config/http.json`.

The narration goes, the hedge stack collapses to one concrete claim, and the answer
leads.

### 4 — a parts list instead of a result (terse, `high`)

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
goes. This is `lang-function-over-inventory`, and it applies at `high` only — at
`low` the list stays as written.

## Do not

- Do not add a preamble ("Here's the rewritten version…"). Send the reply itself.
- Do not apologise or acknowledge the bounce.
- Do not shorten a convo reply to prove compliance, or cut past the band's floor
  at any level.
- Do not drop a fact, number, path, causal link or code block to hit a
  conciseness level.
- Do not add a header to a reply that fits without one.
- Do not write `Label:` bullets where a sentence does the job.
- Do not invent content to fill a bullet list.
