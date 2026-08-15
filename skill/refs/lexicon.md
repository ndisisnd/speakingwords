# speakingwords — lexicon

The single rule file. `speakingwords update` edits this file and nothing else, so
everything the linter knows lives here. `scripts/lint.py` parses the **Strip rules**
table below at runtime; no rule is hardcoded in Python.

## How the strip table is read

- Every row of the table under `## Strip rules` is one rule.
- Columns are fixed and ordered: `id`, `pattern`, `severity`, `guidance`.
- `pattern` is a Python regular expression, written between backticks.
- Every pattern is compiled with `IGNORECASE` and `MULTILINE`. So `^` means
  "start of a line", not "start of the reply", and casing never matters.
- A literal pipe inside a pattern must be written `\|` (the parser unescapes it).
- `severity` is `error` or `warn`. Both count as violations and both bounce the reply;
  severity only shapes how `status` reports and how loudly the rewrite skill treats it.
- Rows whose `id` starts with `#` are ignored (that is how you park a rule without
  deleting it).
- `speakingwords update "more <phrase>"` takes a row out for you — it is the
  supported way to park a rule whose phrase is normal in your domain, and the
  rows that expect it say so in their guidance.

## Strip rules

| id | pattern | severity | guidance |
|----|---------|----------|----------|
| strip-landed | `^\s*Landed\b` | error | Stock completion opener. Anchored to line start so ordinary prose ("the plane landed safely", "the change landed in main") never matches. Say what changed instead. |
| strip-sweep | `^\s*Swee?pt?\b` | error | Stock work-report opener ("Sweep:", "Swept the config"). Anchored to line start so "we swept the floor" mid-sentence is safe. Name the actual action. |
| strip-great-point | `\bgreat point\b` | error | Sycophantic filler. `\b` after `point` means "a great pointer" does not match. Drop the compliment and answer. |
| strip-great-question | `\bgreat question\b` | error | Sycophantic opener. Answer the question rather than rating it. |
| strip-excellent-question | `\bexcellent question\b` | error | Same class as above. |
| strip-certainly | `^\s*Certainly[!,.]` | error | Stock affirmation opener. Requires the punctuation so "certainly true in that case" mid-sentence is safe. |
| strip-happy-to | `\bI['’]d be happy to\b` | error | Service-desk filler. Just do the thing. Both straight and curly apostrophes covered. |
| strip-absolutely-opener | `^\s*Absolutely[!,.]` | error | Stock affirmation. Punctuation-gated so "absolutely required" is safe. |
| strip-youre-absolutely-right | `\byou['’]re absolutely right\b` | error | Sycophancy under correction. Acknowledge the fix in one plain clause instead. |
| strip-let-me-know | `\blet me know if\b` | warn | Closing filler. The user knows they can reply. |
| strip-hope-this-helps | `\bI hope this helps\b` | warn | Closing filler. Ends the reply on nothing. |
| strip-dive-into | `\bdive into\b` | error | Jargon verb. Use "look at", "read", "check". |
| strip-deep-dive | `\bdeep dive\b` | error | Jargon noun. Say what the investigation actually is. |
| strip-delve | `\bdelv[a-z]*\b` | error | Signature agent verb, covers delve/delves/delved/delving. No common English word shares the `delv` prefix. |
| strip-leverage | `\bleverag[a-z]*\b` | error | Jargon verb, covers leverage/leverages/leveraged/leveraging. Financial-noun sense ("2x leverage") would also match; park this rule if you write about finance. |
| strip-utilize | `\butiliz[a-z]*\b` | error | Always means "use". Covers utilize/utilizes/utilization. |
| strip-seamless | `\bseamless[a-z]*\b` | error | Marketing adjective, covers seamless/seamlessly. Describe the actual behaviour. |
| strip-robust | `\brobust\b` | warn | Marketing adjective with no content. Say what it survives. |
| strip-comprehensive | `\bcomprehensive\b` | warn | Filler intensifier in agent replies. Legitimate in "comprehensive income" style domain text, so it is `warn` not `error`. |
| strip-worth-noting | `\bit['’]s worth noting\b` | error | Self-narration. If it is worth noting, note it. |
| strip-as-an-ai | `\bas an AI\b` | error | Identity narration. Never load-bearing for the answer. |
| strip-elevate | `\belevates?\b` | error | Marketing verb. Deliberately excludes "elevator" and "elevation". |
| strip-supercharge | `\bsupercharg[a-z]*\b` | error | Marketing verb, covers supercharge/supercharged/supercharging. |
| strip-game-changer | `\bgame[-\s]?chang(er\|ing)\b` | error | Marketing noun. Covers "game-changer", "game changer", "gamechanging". |
| strip-perfect-opener | `^\s*Perfect[!.]` | error | Stock affirmation opener. Punctuation-gated so "a perfect square" is safe. |
| strip-testament | `\ba testament to\b` | error | Stock flourish. |
| strip-in-conclusion | `^\s*In conclusion\b` | warn | Essay scaffolding. Lead with the conclusion instead of announcing it. |
| strip-i-apologize | `^\s*I apologi[sz]e\b` | warn | Apology opener. State the correction, not the contrition. |
| strip-furthermore | `\bfurthermore\b` | warn | Essay connective. Start a new sentence, or use "also". No everyday sentence needs it. |
| strip-moreover | `\bmoreover\b` | warn | Essay connective, same class as `furthermore`. "And" or a full stop does the job. |
| strip-thus | `\bthus\b(?!\s+far)` | warn | Formal connective. Say "so". The lookahead keeps "thus far" out of it, which is the one phrase where the word is idiomatic. |
| strip-hence | `\bhence\b` | warn | Formal connective. Say "so" or "that is why". `\b` means "henceforth" and "whence" never match. |
| strip-nevertheless | `\bnevertheless\b` | warn | Essay connective. Say "still" or "even so". |
| strip-aforementioned | `\baforementioned\b` | warn | Legal-register adjective. Name the thing again instead. It is genuinely correct in law, contract and standards text ("the aforementioned case law"), so it is `warn`, and anyone writing in those domains should park it: `speakingwords update "more aforementioned"` removes the row, or prefix the id with `#` by hand. |
| strip-whilst | `\bwhilst\b` | warn | Formal variant of "while". "While" is the everyday word and means the same thing. |
| strip-it-should-be-noted | `\bit\s+should\s+be\s+noted\b` | warn | Self-narration in the passive. If it should be noted, note it. |
| strip-in-order-to | `\bin\s+order\s+to\b` | warn | Three words for one. Say "to". Gated on the whole phrase, so "the rows come back in order" is safe. |
| strip-prior-to | `\bprior\s+to\b` | warn | Say "before". Gated on the whole phrase, so "the prior run" and "prior art" are safe. |
| strip-subsequent-to | `\bsubsequent\s+to\b` | warn | Say "after". Gated on the whole phrase, so "subsequent runs" is safe. |

## How the conciseness table is read

- Every row of the table under `## Conciseness rules` is one rule, and the
  columns are fixed and ordered: `id`, `pattern`, `severity`, `active at`,
  `guidance`. Same pattern dialect as the strip table (backticks, `IGNORECASE`,
  `MULTILINE`, `\|` for a literal pipe).
- `active at` lists the conciseness levels where the row fires, comma separated,
  drawn from `low` and `high`. Membership is literal — a row that omits `low` is
  silent at `low`, whatever the other level says.
- `med` is a recognised legacy name, not a third level. The dial carried three
  positions during 0.2.0 development; the recorded run proved `low` and `med`
  inside their bands and left the old `high` undercutting its own floor, so
  `med`'s behaviour and band were promoted to become `high`. A cell that still
  says `med` is read as `high`.
- `lint.py --conciseness <level>` picks the level. An unknown or missing level
  behaves as `high`: only an upgrade from 0.1.0 can produce a missing level,
  0.1.0 behaviour already measured in the `high` band, and `high` is the most
  aggressive shipped level, so it is the value that preserves what the user
  already had.
- A row with an unreadable `active at` cell falls back to `high` only. A rule
  nobody can place belongs at the strictest level, not everywhere.

## Conciseness rules

Padding and restatement: text that repeats what the reply already said, or
announces what it is about to say. These are what the conciseness level cuts.
Severity is `warn` across the table — each phrase has legitimate uses at some
level, which is exactly why the `active at` column exists.

| id | pattern | severity | active at | guidance |
|----|---------|----------|-----------|----------|
| conc-in-other-words | `\bin other words\b` | warn | high | Restatement. The sentence before it already said this. If that sentence was unclear, fix it; do not say it twice. |
| conc-another-way | `\bto put it another way\b` | warn | high | Same class as above, one clause longer. Rewrite the first attempt instead of appending a second. |
| conc-as-mentioned-above | `\bas (?:mentioned\|noted\|stated) above\b` | warn | low, high | Pure back-reference. The reader has the text above; pointing at it adds nothing at any level, so this row fires even at `low`. |
| conc-to-summarize | `^\s*To summari[sz]e\b` | warn | high | Essay scaffolding. Lead with the summary rather than announcing one. Anchored to line start so "used to summarize the log" is safe. |
| conc-simply-put | `^\s*Simply put,` | warn | high | Framing phrase that adds no content. Anchored and comma-gated so the instruction "simply put the file in /tmp" is safe. Only `high` cuts this hard. |

## How the register table is read

- Every row of the table under `## Register rules` is one rule, and the columns
  are fixed and ordered: `id`, `pattern`, `severity`, `active at register`,
  `guidance`. Same pattern dialect as the tables above (backticks, `IGNORECASE`,
  `MULTILINE`, `\|` for a literal pipe).
- `active at register` lists the registers where the row fires, drawn from
  `slack` and `ste`. Membership is literal, exactly like the conciseness column.
- `lint.py --register <value>` picks the register. An unknown or missing
  register behaves as `slack`. Every install that predates the register key was
  a Slack-register install, so `slack` is the value that preserves what the user
  already had.
- A row with an unreadable `active at register` cell falls back to `ste` only.
  The rule nobody can place goes to the newer, stricter register, never to the
  one every existing install runs — widening a rule's reach is how a false
  positive reaches a user who never asked for it.
- The register is a swap, not an extra layer. At `ste` these rows fire and
  `lang-slack-register` is off; at `slack` these rows are silent and nothing
  else changes. Strip rules, language rules and conciseness rules are
  register-neutral and run the same way at both.
- Register rows read the reply with its code taken out. Fenced blocks and
  inline backtick spans are blanked first, so a rule about how sentences are
  written can never fire on a command, a path or a quoted snippet. Strip rules
  still read the raw text, as they always have.
- The `ste` register is **inspired by** ASD-STE100 Simplified Technical English.
  It implements writing rules only. It ships no approved-word dictionary, and
  output from it is not conformant STE.

## Register rules

Sentence construction, not vocabulary. These rows only fire at `ste`, where the
reader is often working in a second language and a procedure has to read the
same way every time.

The sentence-length cap is not in this table. It is a structural check, like
`long-sentence`, so it lives with the structural rules below.

| id | pattern | severity | active at register | guidance |
|----|---------|----------|--------------------|----------|
| ste-contraction | `\b(?:can\|don\|doesn\|didn\|isn\|aren\|wasn\|weren\|hasn\|haven\|hadn\|won\|couldn\|shouldn\|wouldn\|mustn\|ain)['’]t\b\|\b(?:it\|that\|there\|what\|here\|he\|she\|who\|let)['’]s\b\|\bI['’](?:m\|ve\|d\|ll)\b\|\b(?:we\|you\|they)['’](?:re\|ve\|ll\|d)\b` | warn | ste | Contractions are out at `ste`: write "do not", "it is", "we will". The pattern enumerates the verb forms one by one and never matches a bare apostrophe, so "the pump's seal" and "the operators' manual" stay clean. The `'s` forms are the honest edge: `it's`, `that's`, `there's`, `what's`, `here's`, `he's`, `she's`, `who's` and `let's` are listed because a following noun cannot be told from a following verb by pattern. They are safe to list anyway — each of those words takes `its`, `whose` or no possessive at all, so a possessive reading of the listed forms does not exist. Any other `'s` is left alone. Code is exempt: fenced blocks and inline backtick spans are blanked before this row runs, so a contraction inside quoted code or a shell command is never a violation. |

## Language rules

Probabilistic. A regex cannot judge these, so the rewrite skill applies them by reading.
Each rule ships one before → after exemplar.

There is no `active at` column here, because a language rule is the register and
the register does not move with the conciseness dial. A row that *is* level-gated
says so in its own first line, in the form `Active at: <levels>.` A row with no
such line is active at every level. Only `lang-function-over-inventory` is gated
today.

| id | rule |
|----|------|
| lang-slack-register | **Write like a colleague in a Slack DM.** Short sentences, everyday words, contractions where they read naturally. Technical terms are fine — it is the grammar around them that stays simple, never the vocabulary of the domain. <br> Before: "Prior to the deployment it should be noted that the aforementioned migration must be applied, whilst the read replicas remain in a lagging state." <br> After: "Run the migration before you deploy. The read replicas are still lagging." |
| lang-plain-words | **Plain words over jargon.** Use the shortest word that carries the meaning. <br> Before: "We leveraged a robust caching layer to optimise throughput." <br> After: "We added a cache. Requests got faster." |
| lang-answer-first | **Lead with the answer.** The first sentence resolves the question; reasoning follows. <br> Before: "There are a few things to consider here. First, the config loads at boot… so yes, it is cached." <br> After: "Yes, it is cached. The config loads once at boot." |
| lang-no-self-narration | **No self-narration.** Do not describe what you are about to do, are doing, or have decided to do. <br> Before: "Let me take a look at the file to understand the structure." <br> After: (read the file, then) "The file defines three exports." |
| lang-no-sycophancy | **No sycophantic openers.** Never rate the question or the user. <br> Before: "Great question! You're absolutely right to ask." <br> After: "The timeout is 30 seconds." |
| lang-one-idea-per-sentence | **One idea per sentence.** Split compound sentences rather than stacking clauses. <br> Before: "The build fails because the lockfile is stale, which happens when deps are added without reinstalling, so run install first." <br> After: "The build fails on a stale lockfile. That happens when deps are added without reinstalling. Run install first." |
| lang-no-filler-close | **No filler close.** End on the last piece of content. <br> Before: "…and that fixes it. Let me know if you need anything else!" <br> After: "…and that fixes it." |
| lang-concrete-over-vague | **Concrete over vague.** Replace intensifiers with the fact behind them. <br> Before: "This significantly improves performance." <br> After: "This cuts the p95 from 400 ms to 90 ms." |
| lang-no-hedging-stack | **No stacked hedges.** At most one qualifier per claim. <br> Before: "It might possibly be the case that this could sometimes fail." <br> After: "This fails when the cache is cold." |
| lang-function-over-inventory | Active at: `high`. **Report what a change does, not the parts it is made of.** A completed-work report names the function, keeps the count, and points at where the parts live — a file, a table, a diff. The roll call goes; the pointer replaces it. This covers enumerations only. Numbers, paths, code blocks, caveats and anything the reader cannot re-derive are never the roll call, and they stay. <br> Before: "Added strip rules for `furthermore`, `moreover`, `thus`, `hence`, `nevertheless`, `aforementioned`, `whilst`…" <br> After: "Formal essay connectives now bounce — 11 rules, each with a near-miss control; see the strip table." |

## Structural rules

Applied by `lint.py`, not by the tables above. One is voice-dependent, two are
not. The two sentence-length rules are the same rule at two settings: exactly
one of them runs, and the installed register picks which.

| id | applies to | rule |
|----|-----------|------|
| terse-prose-block | terse voice only | A paragraph of more than two consecutive non-bullet sentences is a prose block. Terse voice is point-form only, so rewrite it as bullets. Code fences, headings, tables and bullet lists are exempt. Convo voice never triggers this rule — prose is retained and brevity is not forced. |
| long-sentence | both voices, `slack` register only | Three or more prose sentences of over 35 words each. One long sentence is style; three is essay grammar, which is what the Slack register rules out. The threshold is deliberately high — a false positive bounces a good reply. Exempt through the same path as `terse-prose-block`: code fences, tables, quotes, headings and bullets are not counted. |
| ste-long-sentence | both voices, `ste` register only | One prose sentence of over 25 words. Every sentence counts, and there is no allowance: at `ste` a long sentence is the violation, not a pattern of them. ASD-STE100 caps procedural sentences at 20 words and descriptive ones at 25; a linter cannot tell the two apart, so the looser cap applies to both — a false positive bounces a good reply, which is the worse failure. Replaces `long-sentence` at this register; the two never run together. Exempt through the same path: code fences, tables, quotes, headings and bullets are not counted. |
