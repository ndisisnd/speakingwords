'use strict';

// Memory mode: render the style contract as a short marker-delimited block and
// write it into the agent's memory file (plan §4.1).
//
// Two hard rules, both asserted in evals/run_p2.js:
//   A1  The block never exceeds 9 bullet lines. Agent memory files compete for
//       attention and long blocks get skipped, so the budget is enforced in
//       code, not by convention. Over budget = throw, never truncate.
//   A2/A12  Writes are idempotent. Re-running install replaces the existing
//       block in place and touches nothing outside the markers, so
//       install -> install produces a zero diff and all user content survives.

const fs = require('node:fs');
const path = require('node:path');

const START = '<!-- speakingwords:start -->';
const END = '<!-- speakingwords:end -->';
const MAX_BULLETS = 9;
const BLOCK_RE = /<!-- speakingwords:start -->[\s\S]*?<!-- speakingwords:end -->/;

const LEXICON_PATH = path.join(__dirname, '..', 'skill', 'refs', 'lexicon.md');

// Rule ids are readable by design, so the banned-word line is derived from the
// lexicon rather than duplicated here. These overrides only cover ids whose
// literal text differs from the id (openers gated on punctuation, contractions).
const PHRASE_OVERRIDES = {
  'strip-absolutely-opener': 'Absolutely!',
  'strip-perfect-opener': 'Perfect!',
  'strip-certainly': 'Certainly!',
  'strip-landed': 'Landed',
  'strip-sweep': 'Sweep',
  'strip-youre-absolutely-right': "you're absolutely right",
  'strip-happy-to': "I'd be happy to",
  'strip-let-me-know': 'let me know if',
  'strip-hope-this-helps': 'I hope this helps',
  'strip-worth-noting': "it's worth noting",
  'strip-as-an-ai': 'as an AI',
  'strip-i-apologize': 'I apologise',
  'strip-in-conclusion': 'In conclusion',
  'strip-game-changer': 'game-changer',
  'strip-testament': 'a testament to',
};

// Used only when the lexicon file is unreachable (e.g. a partial install tree).
const FALLBACK_PHRASES = [
  'Landed', 'Sweep', 'great point', 'great question', 'Certainly!', 'Absolutely!',
  'Perfect!', 'dive into', 'delve', 'leverage', 'utilize', 'seamless', 'robust',
  "it's worth noting", 'as an AI', 'supercharge', 'game-changer', 'let me know if',
];

function idToPhrase(id) {
  if (PHRASE_OVERRIDES[id]) return PHRASE_OVERRIDES[id];
  return id.replace(/^strip-/, '').replace(/-/g, ' ');
}

// Parse the `## Strip rules` table of skill/refs/lexicon.md for its rule ids.
// Rows whose id starts with `#` are parked and skipped, matching lint.py.
function readStripPhrases(lexiconPath = LEXICON_PATH) {
  let text;
  try {
    text = fs.readFileSync(lexiconPath, 'utf8');
  } catch {
    return FALLBACK_PHRASES.slice();
  }
  const section = text.split(/^## Strip rules\s*$/m)[1];
  if (!section) return FALLBACK_PHRASES.slice();
  const body = section.split(/^## /m)[0];

  const phrases = [];
  for (const line of body.split('\n')) {
    if (!line.trim().startsWith('|')) continue;
    const cells = line.split('|').slice(1, -1).map((c) => c.trim());
    if (cells.length < 4) continue;
    const id = cells[0];
    if (!id || id === 'id' || /^-+$/.test(id) || id.startsWith('#')) continue;
    phrases.push(idToPhrase(id));
  }
  return phrases.length ? phrases : FALLBACK_PHRASES.slice();
}

const VOICE_BULLETS = {
  terse: 'Voice is terse: point form only, one idea per bullet, brevity wins every trade-off. Never answer in paragraphs.',
  convo: 'Voice is convo: prose is retained and brevity is not forced. Use point form only where the content is genuinely a list.',
};

// The shared eight. These are the lexicon Language rules (§ Language rules)
// compressed to one auditable line each — a human should be able to read the
// block in a CLAUDE.md and know exactly what was asked for.
function baseBullets(phrases) {
  return [
    'Lead with the answer. The first line resolves the question; reasoning comes after.',
    'Plain words over jargon. Use the shortest word that carries the meaning.',
    'One idea per sentence. Split compound sentences instead of stacking clauses.',
    'No self-narration. Do not describe what you are about to do, are doing, or have decided to do.',
    'No sycophantic openers. Never rate the question or the user.',
    'No filler close. End on the last piece of content.',
    'Concrete over vague. Replace intensifiers with the fact behind them, and use at most one hedge per claim.',
    `Never use these words and phrases: ${phrases.join(', ')}.`,
  ];
}

function countBullets(block) {
  return block.split('\n').filter((line) => line.startsWith('- ')).length;
}

/**
 * Render the marker block.
 *
 * @param {object} options
 * @param {'terse'|'convo'} options.voice
 * @param {string} [options.version]      stamped into the block header comment
 * @param {string[]} [options.bullets]    injected template, used by evals to
 *                                        prove the >9 refusal path (A1)
 * @param {string} [options.lexiconPath]  override for tests
 * @returns {string} block text, no trailing newline
 */
function renderBlock(options = {}) {
  const { voice, version = '0.1.0', bullets, lexiconPath } = options;

  let lines;
  if (bullets) {
    lines = bullets.slice();
  } else {
    if (!VOICE_BULLETS[voice]) throw new Error(`Unknown voice: ${voice}`);
    lines = baseBullets(readStripPhrases(lexiconPath)).concat(VOICE_BULLETS[voice]);
  }

  if (lines.length > MAX_BULLETS) {
    throw new Error(
      `speakingwords memory block would be ${lines.length} bullet lines; the limit is ${MAX_BULLETS} (assertion A1). Refusing to write.`
    );
  }

  const header = `<!-- speakingwords v${version} · memory mode · voice: ${voice} · managed block, edits here are overwritten -->`;
  const block = [START, header, ...lines.map((l) => `- ${l}`), END].join('\n');

  // Belt and braces: the counter used by CI counts rendered lines, so check the
  // rendered form too rather than trusting the input array length.
  if (countBullets(block) > MAX_BULLETS) {
    throw new Error(`Rendered block exceeds ${MAX_BULLETS} bullet lines (assertion A1).`);
  }
  return block;
}

function hasBlock(text) {
  return BLOCK_RE.test(text);
}

/**
 * Write the block into a memory file idempotently.
 *
 * - File absent      -> create it holding just the block.
 * - Block present    -> replace in place, byte for byte, touching nothing else.
 * - Block absent     -> append after exactly one blank-line separator.
 *
 * @returns {{path:string, action:'created'|'replaced'|'appended', changed:boolean}}
 */
function writeBlock(targetPath, block) {
  let existing = null;
  try {
    existing = fs.readFileSync(targetPath, 'utf8');
  } catch (err) {
    if (err.code !== 'ENOENT') throw err;
  }

  let next;
  let action;
  if (existing === null) {
    next = `${block}\n`;
    action = 'created';
  } else if (hasBlock(existing)) {
    next = existing.replace(BLOCK_RE, block);
    action = 'replaced';
  } else {
    const trimmed = existing.replace(/\s+$/, '');
    next = trimmed.length ? `${trimmed}\n\n${block}\n` : `${block}\n`;
    action = 'appended';
  }

  const changed = next !== existing;
  if (changed) {
    fs.mkdirSync(path.dirname(targetPath), { recursive: true });
    fs.writeFileSync(targetPath, next, 'utf8');
  }
  return { path: targetPath, action, changed };
}

module.exports = {
  START,
  END,
  MAX_BULLETS,
  BLOCK_RE,
  renderBlock,
  writeBlock,
  hasBlock,
  countBullets,
  readStripPhrases,
};
